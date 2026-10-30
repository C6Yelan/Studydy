"""使用真 HTTP 與可終止的合成子程序，不呼叫模型或使用產品資料。"""
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, Thread
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

import pytest
from runtime import podcast_process, provider_http
from test_podcast_provider import host as provider


def wait_until(predicate, timeout=6):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    assert predicate(), 'operation did not finish within the test deadline'


def alive(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().split()[2] != 'Z'
    except (FileNotFoundError, ProcessLookupError):
        return False


def post(base, path, body, token='synthetic-provider-token'):
    request = Request(base + path, data=json.dumps(body).encode(),
                      headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
    try:
        with urlopen(request, timeout=10) as response:
            raw = response.read()
            return response.status, json.loads(raw) if response.headers['Content-Type'] == 'application/json' else raw
    except HTTPError as error:
        raw = error.read()
        return error.code, json.loads(raw) if error.headers['Content-Type'] == 'application/json' else None


@pytest.fixture
def provider_server(monkeypatch, tmp_path):
    monkeypatch.setenv('STUDYDY_PODCAST_PROVIDER_TOKEN', 'synthetic-provider-token')
    monkeypatch.setenv('STUDYDY_LUNA_CALL_LOG', str(tmp_path / 'luna-calls.jsonl'))
    server = ThreadingHTTPServer(('127.0.0.1', 0), provider.Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    monkeypatch.setenv('STUDYDY_PODCAST_PROVIDER_URL', base)
    try:
        yield base
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize('stage', ['audio', 'align'])
def test_http_cancel_stops_process_releases_lock_and_isolates_new_lease(provider_server, monkeypatch, tmp_path, stage):
    marker = tmp_path / 'started'
    job = {'job_id': str(uuid4()), 'job_token': str(uuid4())}

    def generate(body, *, cancelled=None, **kwargs):
        if not body.get('fast'):
            podcast_process.run([sys.executable, '-c',
                'import os,time,pathlib;pathlib.Path(__import__("sys").argv[1]).write_text(str(os.getpid()));time.sleep(60)',
                str(marker)], timeout=65, cancelled=cancelled)
        return (b'RIFFsynthetic', None) if stage == 'audio' else {'synthetic': True}

    monkeypatch.setattr(provider, 'align_audio' if stage == 'align' else stage, generate)
    with ThreadPoolExecutor() as pool:
        running = pool.submit(post, provider_server, '/' + stage, job)
        try:
            wait_until(marker.exists)
            pid = int(marker.read_text())
            assert post(provider_server, '/jobs/cancel', job, token='wrong')[0] == 401
            assert post(provider_server, '/jobs/cancel', {**job, 'job_token': str(uuid4())})[0] == 200
            assert not running.done() and alive(pid)
            started = time.monotonic()
            assert post(provider_server, '/jobs/cancel', job) == (200, {'cancelled': True})
            assert running.result(timeout=4) == (502, {'error_code': 'PODCAST_CANCELLED'})
            assert time.monotonic() - started < 4 and not alive(pid)
            # 舊 lease 的遲到取消不能停止同一節目重試後的新工作。
            assert post(provider_server, '/jobs/cancel', job)[0] == 200
            assert post(provider_server, '/' + stage, {**job, 'job_token': str(uuid4()), 'fast': True})[0] == 200
        finally:
            post(provider_server, '/jobs/cancel', job)


def test_cancel_before_dispatch_and_while_waiting_for_resource_never_starts_generation(provider_server, monkeypatch):
    calls = []
    monkeypatch.setattr(provider, 'audio', lambda *a, **k: calls.append(True))
    early = {'job_id': str(uuid4()), 'job_token': str(uuid4())}
    post(provider_server, '/jobs/cancel', early)
    assert post(provider_server, '/audio', early) == (502, {'error_code': 'PODCAST_CANCELLED'})
    queued = {'job_id': str(uuid4()), 'job_token': str(uuid4())}
    with ThreadPoolExecutor() as pool:
        provider.LOCK.acquire()
        try:
            request = pool.submit(post, provider_server, '/audio', queued)
            wait_until(lambda: podcast_process._key(queued) in podcast_process._jobs)
            post(provider_server, '/jobs/cancel', queued)
            assert request.result(timeout=2) == (502, {'error_code': 'PODCAST_CANCELLED'})
            assert not calls
        finally:
            provider.LOCK.release()
            post(provider_server, '/jobs/cancel', queued)


@pytest.mark.parametrize('stop', ['cancel', 'timeout'])
def test_process_group_is_reaped_with_large_stdin(stop, tmp_path):
    marker = tmp_path / 'processes.json'
    program = '''import json,os,pathlib,signal,subprocess,sys,time
child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
def terminate(*args):
    child.wait(timeout=1)
    sys.exit(0)
signal.signal(signal.SIGTERM,terminate)
pathlib.Path(sys.argv[1]).write_text(json.dumps([os.getpid(),child.pid]))
sys.stdin.read()
time.sleep(60)
'''
    cancelled = Event()
    with ThreadPoolExecutor() as pool:
        future = pool.submit(podcast_process.run, [sys.executable, '-c', program, str(marker)],
                             input='synthetic ' * 100000, timeout=1 if stop == 'timeout' else None, cancelled=cancelled)
        wait_until(marker.exists)
        pids = json.loads(marker.read_text())
        if stop == 'cancel':
            cancelled.set()
        with pytest.raises(ValueError if stop == 'cancel' else subprocess.TimeoutExpired):
            future.result(timeout=4)
        assert all(not alive(pid) for pid in pids)


@pytest.mark.parametrize('stage', ['luna', 'audio', 'align', 'transcribe'])
def test_real_provider_entry_points_pass_cancellation_to_child(stage, monkeypatch, tmp_path):
    marker = tmp_path / 'started'
    executable = tmp_path / 'synthetic-worker'
    executable.write_text(f'#!{sys.executable}\nimport os,time,pathlib\npathlib.Path({str(marker)!r}).write_text(str(os.getpid()))\ntime.sleep(60)\n')
    executable.chmod(0o700)
    for name in ('STUDYDY_CODEX_BIN', 'STUDYDY_PODCAST_TTS_PYTHON', 'STUDYDY_STT_PYTHON'):
        monkeypatch.setenv(name, str(executable))
    cancelled = Event()
    with ThreadPoolExecutor() as pool:
        if stage == 'luna':
            future = pool.submit(provider.luna, 'synthetic', {'type': 'object'}, cancelled=cancelled)
        elif stage == 'audio':
            future = pool.submit(provider.audio, {'script': {}, 'purpose':'podcast'}, cancelled=cancelled)
        elif stage == 'transcribe':
            import base64
            future = pool.submit(provider.transcribe, {'audio':base64.b64encode(b'synthetic').decode()}, cancelled=cancelled)
        else:
            import base64
            future = pool.submit(provider.align_audio, {'audio': base64.b64encode(b'synthetic').decode(), 'texts': ['synthetic']}, cancelled=cancelled)
        try:
            wait_until(marker.exists)
            pid = int(marker.read_text())
            cancelled.set()
            with pytest.raises(ValueError, match='PODCAST_CANCELLED'):
                future.result(timeout=4)
            assert not alive(pid)
        finally:
            cancelled.set()


def test_long_running_job_is_not_pruned_and_remains_cancellable(monkeypatch):
    body={'job_id':str(uuid4()),'job_token':str(uuid4())}
    with podcast_process.job(body) as cancelled:
        now=time.monotonic()
        monkeypatch.setattr(podcast_process.time,'monotonic',lambda:now+21600)
        podcast_process.cancel({'job_id':str(uuid4()),'job_token':str(uuid4())})
        podcast_process.cancel(body)
        assert cancelled.is_set()


def test_no_generation_deadline_even_when_clock_passes_old_limits(monkeypatch,tmp_path):
    marker=tmp_path/'started';cancelled=Event()
    with ThreadPoolExecutor() as pool:
        future=pool.submit(podcast_process.run,[sys.executable,'-c',
            'import pathlib,sys,time;pathlib.Path(sys.argv[1]).write_text("ready");time.sleep(60)',str(marker)],
            timeout=None,cancelled=cancelled)
        try:
            wait_until(marker.exists)
            original=time.monotonic
            monkeypatch.setattr(podcast_process.time,'monotonic',lambda:original()+21600)
            time.sleep(.4)
            assert not future.done()
        finally:
            cancelled.set()
        with pytest.raises(ValueError,match='PODCAST_CANCELLED'):
            future.result(timeout=4)


def test_audio_transport_keeps_connection_timeout_but_not_generation_timeout(provider_server,monkeypatch):
    observed=[];connection=provider_http.HTTPConnection
    class ObservedConnection(connection):
        def connect(self):
            observed.append(self.timeout)
            super().connect()
        def getresponse(self):
            observed.append(self.sock.gettimeout())
            return super().getresponse()
    monkeypatch.setattr(provider_http,'HTTPConnection',ObservedConnection)
    monkeypatch.setattr(provider,'audio',lambda *a,**k:(b'RIFFsynthetic',None))
    request=Request(provider_server+'/audio',data=b'{"purpose":"podcast"}',headers={
        'Content-Type':'application/json','Authorization':'Bearer synthetic-provider-token'})
    with provider_http.generation_response(request) as response:
        assert response.read()==b'RIFFsynthetic'
    assert observed==[10,None]


@pytest.mark.parametrize('waiting_for', ['headers', 'body'])
def test_cancel_releases_local_http_wait_even_when_provider_cancel_delivery_fails(monkeypatch, waiting_for):
    from http.server import BaseHTTPRequestHandler
    from runtime import generation_jobs

    entered, release, cancel, delivered, finished = (Event() for _ in range(5))
    errors = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            if waiting_for == 'body':
                self.send_response(200)
                self.send_header('Content-Length', '100')
                self.end_headers()
                self.wfile.write(b'{')
                self.wfile.flush()
            entered.set()
            release.wait(10)

    def check():
        if cancel.is_set():
            raise generation_jobs.JobCancelled()

    def failed_cancel(*args, **kwargs):
        delivered.set()
        raise OSError('synthetic cancel endpoint unavailable')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    serving = Thread(target=server.serve_forever, daemon=True)
    serving.start()
    monkeypatch.setenv('STUDYDY_PODCAST_PROVIDER_URL', f'http://127.0.0.1:{server.server_port}')
    monkeypatch.setenv('STUDYDY_PODCAST_PROVIDER_TOKEN', 'synthetic-token')
    monkeypatch.setattr(generation_jobs, 'urlopen', failed_cancel)

    def run():
        try:
            with generation_jobs.provider_job(check):
                provider_http._host('/semantics', {})
        except Exception as error:
            errors.append(error)
        finally:
            finished.set()

    worker = Thread(target=run)
    worker.start()
    try:
        assert entered.wait(3)
        cancel.set()
        assert delivered.wait(2)
        assert finished.wait(2), 'cancelled generation still occupies the local worker'
        assert len(errors) == 1 and isinstance(errors[0], generation_jobs.JobCancelled)
    finally:
        release.set()
        worker.join(timeout=3)
        server.shutdown()
        server.server_close()
        serving.join(timeout=2)


def test_cancel_during_connect_does_not_send_generation_or_affect_the_next_scope(provider_server, monkeypatch):
    from runtime import generation_jobs

    connecting, release, cancelled, delivered = (Event() for _ in range(4))
    connection = provider_http.HTTPConnection
    generated = []

    class DelayedConnection(connection):
        def connect(self):
            connecting.set()
            assert release.wait(5)
            super().connect()

    def check():
        if cancelled.is_set():
            raise generation_jobs.JobCancelled()

    def failed_cancel(*args, **kwargs):
        delivered.set()
        raise OSError('synthetic cancel endpoint unavailable')

    def audio(*args, **kwargs):
        generated.append(True)
        return b'RIFFsynthetic', None

    monkeypatch.setattr(provider_http, 'HTTPConnection', DelayedConnection)
    monkeypatch.setattr(generation_jobs, 'urlopen', failed_cancel)
    monkeypatch.setattr(provider, 'audio', audio)

    def run():
        with generation_jobs.provider_job(check):
            return provider_http.provider('/audio', {'purpose': 'podcast'})

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run)
        try:
            assert connecting.wait(2)
            cancelled.set()
            assert delivered.wait(2)
        finally:
            release.set()
        with pytest.raises(generation_jobs.JobCancelled):
            future.result(timeout=3)
        assert generated == []
        # 重用同一個 thread；已取消 scope 不得污染下一個工作。
        cancelled.clear()
        assert pool.submit(run).result(timeout=3) == b'RIFFsynthetic'
        assert generated == [True]


def test_resource_queue_has_no_default_deadline_and_health_does_not_queue(provider_server, monkeypatch):
    import threading
    cancelled = Event()
    with ThreadPoolExecutor() as pool:
        provider.TEXT_LOCK.acquire()
        try:
            assert post(provider_server, '/luna-health', {})[0] == 200
            def acquire():
                with podcast_process.locked(provider.TEXT_LOCK, cancelled): pass
            future = pool.submit(acquire)
            original = time.monotonic
            monkeypatch.setattr(podcast_process.time, 'monotonic', lambda: original() + 86400)
            time.sleep(.3)
            assert not future.done()
            cancelled.set()
            with pytest.raises(ValueError, match='PODCAST_CANCELLED'): future.result(timeout=3)
        finally:
            cancelled.set()
            provider.TEXT_LOCK.release()


@pytest.mark.parametrize('binary', [False, True])
def test_delayed_reader_receives_complete_large_stdin_and_eof(binary, tmp_path):
    """大於 pipe 容量，且子程序晚於取消輪詢間隔才讀取時，仍須送完資料。"""
    payload = ('合成資料 ABC 123\n' * 30000).encode()
    output = tmp_path / 'received'
    program = 'import sys,time,pathlib,hashlib;time.sleep(.5);data=sys.stdin.buffer.read();pathlib.Path(sys.argv[1]).write_text(hashlib.sha256(data).hexdigest())'
    result = podcast_process.run([sys.executable,'-c',program,str(output)],
        input=payload if binary else payload.decode(), timeout=5, cancelled=Event())
    import hashlib
    assert result.returncode == 0
    assert output.read_text() == hashlib.sha256(payload).hexdigest()


def test_tracked_lock_reports_real_wait_and_execution():
    from threading import Lock, Thread, Event
    from uuid import uuid4
    from runtime import podcast_process as process
    import time
    lock=Lock();lock.acquire()
    body={'job_id':str(uuid4()),'job_token':str(uuid4())}
    started=Event();finish=Event()
    def work():
        with process.tracked_lock(lock,None,body,'/audio'):
            started.set();finish.wait(2)
    thread=Thread(target=work);thread.start()
    try:
        deadline=time.monotonic()+2
        while not process.progress({'jobs':[body]})['jobs'] and time.monotonic()<deadline:
            time.sleep(.01)
        queued=process.progress({'jobs':[body]})['jobs'][0]
        assert queued['status']=='queued' and queued['started_at'] is None
        lock.release();assert started.wait(2)
        running=process.progress({'jobs':[body]})['jobs'][0]
        assert running['status']=='running' and running['started_at']
    finally:
        finish.set();thread.join(3)
    assert process.progress({'jobs':[body]})=={'jobs':[]}


def test_run_drains_stderr_in_memory_while_sending_large_input(capsys):
    result = podcast_process.run(
        [sys.executable, '-c',
         "import sys; sys.stderr.write('合成錯誤內容\\n' * 12000); sys.stderr.flush(); sys.stdin.read(); print('不應輸出的內容')"],
        input='合成教材\n' * 12000,
        timeout=5,
        capture_stderr=True,
    )
    assert result.returncode == 0 and result.stdout is None
    assert result.stderr == '合成錯誤內容\n' * 12000
    captured = capsys.readouterr()
    assert captured.out == captured.err == ''
