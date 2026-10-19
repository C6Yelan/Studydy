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
