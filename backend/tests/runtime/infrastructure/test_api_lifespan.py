"""API 啟停失敗仍清理已啟動的背景工作；不連線 DB 或模型。"""

import pytest
from fastapi.testclient import TestClient

import runtime.api.app as api
import runtime.workers as workers


START_ORDER = ("runtime", "voice", "scene", "video", "research", "card", "podcast")
STOP_ORDER = ("podcast", "scene", "video", "research", "card", "voice", "runtime")


def application(monkeypatch, *, fail_start=None, fail_stop=None):
    active = set()
    events = []

    class Worker:
        def __init__(self, name):
            self.name = name

        def start(self):
            events.append(("start", self.name))
            if self.name == "runtime":
                # RuntimeWorkers.start 可能已開執行緒，才在恢復或 readiness 階段失敗。
                active.add(self.name)
            if self.name == fail_start:
                raise RuntimeError("synthetic startup failure")
            active.add(self.name)
            return self

        def stop(self):
            events.append(("stop", self.name))
            active.discard(self.name)
            if self.name == fail_stop:
                raise RuntimeError("synthetic shutdown failure")

    monkeypatch.setattr(workers, "RuntimeWorkers", lambda *_: Worker("runtime"))
    monkeypatch.setattr(
        workers, "BackgroundWorker",
        lambda *_, error_code, **__: Worker(error_code.removesuffix("_WORKER_FAILED").lower()),
    )
    monkeypatch.setattr(api, "runtime_binding", lambda _: {})
    settings = api.ApiSettings(
        profile="local",
        public_origin="http://127.0.0.1:4173",
        secure_cookie=False,
        local_config={},
        dsn=None,
    )
    return api.create_app(settings), active, events


@pytest.mark.parametrize("failed", START_ORDER)
def test_partial_startup_closes_every_started_worker(monkeypatch, failed):
    app, active, events = application(monkeypatch, fail_start=failed)
    with pytest.raises(RuntimeError, match="synthetic startup failure"):
        with TestClient(app):
            pytest.fail("failed startup must not serve requests")
    assert active == set()
    started = set(START_ORDER[: START_ORDER.index(failed)]) | {"runtime"}
    assert [name for action, name in events if action == "stop"] == [
        name for name in STOP_ORDER if name in started
    ]


def test_normal_start_and_stop_orders_are_preserved(monkeypatch):
    app, active, events = application(monkeypatch)
    with TestClient(app) as client:
        assert active == set(START_ORDER)
        assert client.get("/v1/openapi.json").status_code == 200
    assert active == set()
    assert events == [
        *(("start", name) for name in START_ORDER),
        *(("stop", name) for name in STOP_ORDER),
    ]


def test_stop_failure_does_not_leave_other_workers_running(monkeypatch):
    app, active, events = application(monkeypatch, fail_stop="scene")
    with pytest.raises(RuntimeError, match="synthetic shutdown failure"):
        with TestClient(app):
            pass
    assert active == set()
    assert [name for action, name in events if action == "stop"] == list(STOP_ORDER)
