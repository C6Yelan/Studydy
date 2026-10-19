"""可取消的轉檔不以原 60 秒期限殺掉正常工作。"""
import subprocess
import sys
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from pathlib import Path
import pytest
from document_normalization import converter
from test_podcast_process import alive


@pytest.mark.parametrize('jobs', [1, 8])
def test_conversion_waits_for_process_and_kills_it_when_source_is_cancelled(monkeypatch, jobs):
    monkeypatch.setattr(converter, 'conversion_policy', lambda: {})
    original = subprocess.Popen
    children = []
    def spawn(command, **kwargs):
        process = original(command[:command.index('--') + 1] +
            [sys.executable, '-c', 'import time;time.sleep(60)'], **kwargs)
        children.append(process)
        return process
    monkeypatch.setattr(converter.subprocess, 'Popen', spawn)
    def convert_one(_):
        checks = 0
        def check():
            nonlocal checks
            checks += 1
            if checks == 4: raise converter.NormalizationError('SOURCE_CANCELLED')
        with pytest.raises(converter.NormalizationError, match='SOURCE_CANCELLED'):
            converter.convert(b'synthetic', '.txt', converter.MIME['.txt'], {}, cancellation_check=check)
        assert checks == 4
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(convert_one, range(jobs)))
    assert len(children) == jobs and all(not alive(child.pid) for child in children)


def test_threaded_conversion_applies_limits_after_exec_and_reaps_failed_children(monkeypatch, tmp_path):
    monkeypatch.setattr(converter, 'conversion_policy', lambda: {})
    original = subprocess.Popen
    children = []

    def spawn(command, **kwargs):
        # 真實啟動限額程式；只把隔離轉檔器換成回報限額後失敗的合成 child。
        assert 'preexec_fn' not in kwargs
        result = tmp_path / str(uuid4())
        script = (
            'import json,resource,sys;from pathlib import Path;'
            'kinds=[resource.RLIMIT_AS,resource.RLIMIT_FSIZE,resource.RLIMIT_NOFILE,resource.RLIMIT_NPROC];'
            f'Path({str(result)!r}).write_text(json.dumps([resource.getrlimit(k) for k in kinds]));'
            'sys.exit(1)'
        )
        process = original(command[:command.index('--') + 1] + [sys.executable, '-c', script], **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(converter.subprocess, 'Popen', spawn)
    def convert_one(_):
        with pytest.raises(converter.NormalizationError, match='NORMALIZATION_FAILED'):
            converter.convert(b'synthetic', '.txt', converter.MIME['.txt'], {})

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(convert_one, range(12)))
    assert len(children) == 12 and all(child.poll() == 1 and not alive(child.pid) for child in children)
    assert len(list(tmp_path.iterdir())) == 12
    for result in tmp_path.iterdir():
        assert json.loads(result.read_text()) == [[value, value] for value in (2 * 1024**3, 100 * 1024**2, 128, 1024)]


def test_conversion_start_failure_is_reported_and_temporary_files_removed(monkeypatch, tmp_path):
    monkeypatch.setattr(converter, 'conversion_policy', lambda: {})
    monkeypatch.setattr(converter.tempfile, 'tempdir', str(tmp_path))
    def fail(*args, **kwargs):
        raise FileNotFoundError('synthetic missing launcher')
    monkeypatch.setattr(converter.subprocess, 'Popen', fail)
    with pytest.raises(FileNotFoundError, match='synthetic missing launcher'):
        converter.convert(b'synthetic', '.txt', converter.MIME['.txt'], {})
    assert list(tmp_path.iterdir()) == []
