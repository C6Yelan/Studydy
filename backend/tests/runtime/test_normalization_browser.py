"""真 API／DB／converter；不啟動語意 worker，任何模型 HTTP 都拒絕。"""
from threading import Event,Thread
import httpx
import runtime.api.app as api
from browser_e2e_runner import PORT,local_api,main as run_browser
from test_closed_loop_v1 import closed_loop
from test_source_normalization import normalizer
from runtime.source_normalization import normalize_next


def test_real_normalization_browser(closed_loop,normalizer,monkeypatch):
    settings=closed_loop[2];dsn=closed_loop[4];attempts=[];errors=[]
    def reject(*args,**kwargs):attempts.append('unexpected-model-http');raise AssertionError('MODEL_NOT_ALLOWED')
    monkeypatch.setattr(httpx.HTTPTransport,'handle_request',reject)
    monkeypatch.setenv('STUDYDY_E2E_NORMALIZATION_REAL','true')
    app=api.create_app(api.ApiSettings(profile='local',public_origin=f'http://127.0.0.1:{PORT}',secure_cookie=False,local_config=settings,dsn=dsn))
    stop=Event()
    def convert_only():
        while not stop.is_set():
            try:normalize_next(dsn=dsn)
            except Exception as e:errors.append(type(e).__name__);return
            stop.wait(.1)
    thread=Thread(target=convert_only);thread.start()
    try:
        with local_api(app):assert run_browser('e2e/normalization-real.spec.ts',production=True)==0
    finally:stop.set();thread.join(timeout=65)
    assert not thread.is_alive() and errors==[] and attempts==[]
