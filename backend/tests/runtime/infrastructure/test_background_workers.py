"""輪詢節奏、失敗續跑與研究排程；不呼叫模型或資料庫。"""

import pytest

from runtime import workers


@pytest.mark.parametrize("drain, delays", [(False, [1, 1, 1, 1]), (True, [0, 0, 2, 2])])
def test_polling_preserves_busy_idle_and_error_delays(drain, delays, caplog):
    results = iter([True, False, RuntimeError("synthetic failure")])
    calls = []

    def step():
        result = next(results)
        calls.append(result)
        if isinstance(result, Exception):
            raise result
        return result

    waits = []

    class StopAfterThreeSteps:
        def wait(self, delay):
            waits.append(delay)
            return len(waits) == 4

    worker = workers.BackgroundWorker(step, name="test", error_code="TEST_WORKER_FAILED", drain=drain)
    worker.stop_event = StopAfterThreeSteps()
    worker.loop()
    assert waits == delays
    assert len(calls) == 3
    assert "TEST_WORKER_FAILED" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    assert "synthetic failure" not in caplog.text


@pytest.mark.parametrize("topic_progress", [False, True])
@pytest.mark.parametrize("research_progress", [False, True])
def test_research_runs_both_steps_in_order(monkeypatch, topic_progress, research_progress):
    from runtime import research, topics

    calls = []
    results = []

    def topic_step(**_):
        calls.append("topic")
        return topic_progress

    def research_step(**_):
        calls.append("research")
        return research_progress

    class Worker:
        def __init__(self, step=None, **kwargs):
            self.step = step if kwargs.get("name") == "studydy-research" else None

        def start(self):
            if self.step:
                results.append(self.step())
            return self

        def stop(self):
            pass

    monkeypatch.setattr(topics, "step", topic_step)
    monkeypatch.setattr(research, "step", research_step)
    monkeypatch.setattr(workers, "BackgroundWorker", Worker)
    monkeypatch.setattr(workers, "RuntimeWorkers", lambda *_: Worker())
    with workers.run_workers(None, {}):
        pass
    assert calls == ["topic", "research"]
    assert results == [topic_progress or research_progress]


def test_background_stop_reports_a_thread_still_running():
    from threading import Event
    entered, release = Event(), Event()

    def blocked():
        entered.set()
        release.wait(5)

    worker = workers.BackgroundWorker(blocked, name="test", error_code="TEST_WORKER_FAILED", drain=True)
    worker.start()
    try:
        assert entered.wait(1)
        assert worker.is_alive()
        with pytest.raises(RuntimeError, match="TEST_WORKER_STOP_FAILED"):
            worker.stop()
        assert worker.thread.is_alive()
        assert not worker.is_alive()
    finally:
        release.set()
        worker.thread.join(timeout=2)
        worker.stop()


def test_configured_worker_counts_start_and_stop_every_thread(monkeypatch):
    from threading import Event
    from runtime import card_generation, podcast_videos, podcast_worker, research, topics

    release = Event()
    monkeypatch.setenv("STUDYDY_CARD_WORKERS", "3")
    monkeypatch.setenv("STUDYDY_PODCAST_WORKERS", "2")
    monkeypatch.setenv("STUDYDY_VIDEO_WORKERS", "3")
    monkeypatch.delenv("STUDYDY_RESEARCH_WORKERS", raising=False)
    for module in (card_generation, podcast_videos, podcast_worker, research, topics):
        monkeypatch.setattr(module, "step", lambda **_: release.wait(0.05) and False)

    class Runtime:
        def start(self):
            pass

        def stop(self):
            pass

        def is_alive(self):
            return True

    monkeypatch.setattr(workers, "RuntimeWorkers", lambda *_: Runtime())
    started = []
    original = workers.BackgroundWorker.start

    def record(self):
        started.append(self)
        return original(self)

    monkeypatch.setattr(workers.BackgroundWorker, "start", record)
    with workers.run_workers(None, {}) as ready:
        assert ready()
        names = sorted(worker.thread.name for worker in started)
        assert [n for n in names if n.startswith("studydy-cards")] == [
            "studydy-cards", "studydy-cards-2", "studydy-cards-3"]
        assert [n for n in names if n.startswith("studydy-podcast-worker")] == [
            "studydy-podcast-worker"]
        coordinator = next(w for w in started if w.thread.name == "studydy-podcast-worker")
        assert coordinator.step.keywords["workers"] == 2
        assert [n for n in names if n.startswith("studydy-research")] == ["studydy-research"]
        video = [w for w in started if w.thread.name.startswith("podcast-video-worker")]
        assert sorted(w.thread.name for w in video) == [
            "podcast-video-worker", "podcast-video-worker-2", "podcast-video-worker-3"]
        assert all(w.step.keywords["workers"] == 3 for w in video)
    release.set()
    assert all(not worker.thread.is_alive() for worker in started)


@pytest.mark.parametrize("key", ["CARD", "PODCAST", "RESEARCH", "VIDEO"])
@pytest.mark.parametrize("value", ["0", "-1", "two", ""])
def test_invalid_worker_count_is_rejected_before_start(monkeypatch, key, value):
    monkeypatch.setenv(f"STUDYDY_{key}_WORKERS", value)
    monkeypatch.setattr(workers, "RuntimeWorkers", lambda *_: pytest.fail("started"))
    with pytest.raises(ValueError, match=f"^{key}_WORKER_COUNT_INVALID$"):
        with workers.run_workers(None, {}):
            pass
