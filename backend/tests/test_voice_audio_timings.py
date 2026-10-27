from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

from test_podcast_process import provider_server, wait_until
from test_podcast_provider import provider
from runtime import podcast_process, provider_http


def test_audio_timings_measure_real_provider_queue_and_synthesis(provider_server,monkeypatch):
    started=Event();release=Event()
    def audio(body,**kwargs):
        started.set();assert release.wait(3)
        return b'synthetic-wav',None
    monkeypatch.setattr(provider,'audio',audio)
    job={'job_id':str(uuid4()),'job_token':str(uuid4())}
    timings={}
    provider.LOCK.acquire()
    try:
        with ThreadPoolExecutor() as pool:
            future=pool.submit(provider_http.provider,'/audio',job,timings=timings)
            wait_until(lambda: bool(podcast_process.progress({'jobs':[job]})['jobs']))
            assert podcast_process.progress({'jobs':[job]})['jobs'][0]['status']=='queued'
            provider.LOCK.release()
            assert started.wait(3)
            assert podcast_process.progress({'jobs':[job]})['jobs'][0]['status']=='running'
            release.set()
            assert future.result(timeout=3)==b'synthetic-wav'
    finally:
        release.set()
    assert timings['tts_queue_ms']>0 and timings['tts_synthesis_ms']>0
