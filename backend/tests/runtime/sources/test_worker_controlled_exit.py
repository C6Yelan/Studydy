"""單 worker 在真實 run/lease 下退出 blocking fake，且維持發布隔離。"""

from threading import Event
import sys
from uuid import uuid4

import pytest

from pdf_evidence import material_pipeline
from pdf_evidence.local_ai_process import LocalAIProcess
import runtime.material_processing as processing
import runtime.workers as workers
from runtime.storage.tables import Material, MaterialProcessingRun, database_session
from test_inference_controlled_exit import blocked_http
from test_source_revisions import revisions
from product_fixtures import closed_loop


@pytest.mark.parametrize('inference', ['ocr', 'semantic'])
@pytest.mark.parametrize('intent', ['cancel', 'shutdown', 'lost-token'])
def test_material_worker_exits_blocking_inference_and_fences_publication(revisions, monkeypatch, inference, intent):
    learner, material, settings, dsn, add, start, _, _, old, _ = revisions
    second = add('B.pdf', '' if inference == 'ocr' else 'A queue removes the first inserted element first.')
    run = start([second], 'controlled-exit', old['revision'])
    original_analysis = processing.analyze_material
    monkeypatch.setattr(processing, 'analyze_material', material_pipeline.analyze_material)
    monkeypatch.setattr(processing, '_LEASE_HEARTBEAT_SECONDS', .05)
    monkeypatch.setattr(workers, '_SHUTDOWN_WAIT_SECONDS', 2)
    # fixture 自行完成 normalization，避免背景 worker 搶走 fixture 正在建立的來源。
    monkeypatch.setattr(workers, 'normalize_next', lambda **_: False)
    entered = Event()
    children = []
    def start_child(_settings):
        child = LocalAIProcess([sys.executable, '-c', 'import sys, time; sys.stdin.buffer.readline(); time.sleep(600)'],
                               request_limit=96 * 1024 * 1024, response_limit=100)
        exchange = child._exchange
        def blocked(encoded):
            entered.set()
            return exchange(encoded)
        child._exchange = blocked
        children.append(child)
        return child
    monkeypatch.setattr(material_pipeline, 'start_ocr_process', start_child)
    completed = Event()
    results = []
    execute = workers.execute_claimed_material_processing_run
    def record(*args, **kwargs):
        result = execute(*args, **kwargs)
        results.append(result)
        completed.set()
        return result
    monkeypatch.setattr(workers, 'execute_claimed_material_processing_run', record)
    with blocked_http(monkeypatch) as server:
        worker = workers.RuntimeWorkers(dsn, settings)
        worker.start()
        try:
            assert (entered if inference == 'ocr' else server.entered).wait(5)
            if intent == 'shutdown':
                worker.stop()
            elif intent == 'cancel':
                processing.request_material_processing_cancellation(learner.learner_id, run.run_id,
                                                                    update_only=True, dsn=dsn)
            else:
                with database_session(dsn) as session:
                    session.get(MaterialProcessingRun, run.run_id).worker_token = uuid4()
            assert completed.wait(3)
            assert results[0].status == ('cancelled' if intent == 'cancel' else 'running')
            assert results[0].output_binding is None
            with database_session(dsn) as session:
                assert session.get(Material, material).head_revision == old['revision']
            for child in children:
                assert child._process.poll() is not None
                assert child._process.stdout.closed and child._process.stdin.closed
            if intent == 'cancel':
                # 同一個 worker 繼續處理下一份工作，不需重啟程序。
                monkeypatch.setattr(processing, 'analyze_material', original_analysis)
                third = add('C.pdf', 'A linked list links its nodes with pointers.')
                completed.clear()
                next_run = start([third], 'after-cancel', old['revision'])
                assert completed.wait(5)
                assert results[-1].run_id == next_run.run_id
                assert results[-1].status == 'succeeded'
            worker.stop()
            worker.stop()
            assert not worker._thread.is_alive()
            before = len(results)
            server.release.set()
            assert len(results) == before
        finally:
            server.release.set()
            for child in children:
                child.abort()
            worker.stop()
