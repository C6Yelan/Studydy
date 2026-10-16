"""僅測試的並行可行性原型；不接入產品排程、不使用真實模型或產品資料。"""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from copy import deepcopy
from threading import Barrier, BoundedSemaphore, Event, Lock
from uuid import uuid4
import json

import pytest

from knowledge_map.material_review import _pack_review, combine_reviews, project_review, ReviewError
from runtime.material_review import _checked_review
from test_material_review import view, proposal, wire_proposal
from test_podcast_process import provider_server, provider, post, wait_until


def inputs(groups=16):
    template = view()
    full = deepcopy(template)
    full['concepts'], full['relations'], full['initial_learning_path'] = [], [], []
    for group in range(groups):
        prefix = f'g{group}-'
        for c in deepcopy(template['concepts']):
            c['concept_id'] = prefix + c['concept_id']
            for q in c['claims']:
                q['claim_id'] = prefix + q['claim_id']
                for e in q['evidence']:
                    e['evidence_id'] = prefix + e['evidence_id']
                    e['page'] = e['normalized_page'] = group + 1
            full['concepts'].append(c)
        for r in deepcopy(template['relations']):
            for key in ('relation_id', 'source_concept_id', 'target_concept_id'):
                r[key] = prefix + r[key]
            r['evidence_refs'] = [prefix + e for e in r['evidence_refs']]
            full['relations'].append(r)
    full['initial_learning_path'] = [
        {'position': i + 1, 'concept_id': c['concept_id'], 'reason': 'document_order'}
        for i, c in enumerate(full['concepts'])
    ]
    units = []
    for group in range(groups):
        concepts = full['concepts'][group * 5:(group + 1) * 5]
        evidence = [e for c in concepts for q in c['claims'] for e in q['evidence']]
        units.append(_pack_review(full, concepts, evidence, f'synthetic-{group}'))
    return full, units


class Archive:
    """獨立暫存目錄；持久化回應並在寫入前檢查本次取消，不冒充產品 DB 測試。"""
    def __init__(self, root):
        self.root = root
        root.mkdir()
        self.cancelled = Event()
        self.mutex = Lock()
        self.requests = []

    def check(self):
        if self.cancelled.is_set():
            raise RuntimeError('test_cancelled')

    def load_review(self, key, *, validate_response):
        path = self.root / f'cache-{key}.json'
        if path.exists():
            response = json.loads(path.read_text())
            validate_response(response)
            return response
        return None

    def prepare_review_call(self, index, key, request, *, attempt=0):
        self.check()
        with self.mutex:
            self.requests.append((index, attempt))

    def save_review(self, name, data):
        with self.mutex:
            self.check()
            path = self.root / (name + '.json')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data))


def parallel(units, archive, width):
    """測試專用：有界提交、失敗停止派送並排空在途批次，按原順序合併。"""
    results, pending = {}, {}
    iterator = iter(enumerate(units, 1))
    with ThreadPoolExecutor(max_workers=width) as pool:
        def submit():
            item = next(iterator, None)
            if item is None:
                return
            index, unit = item
            f = pool.submit(_checked_review, unit, index,
                {'material_review': {'prompt': 'synthetic'}}, archive, None, archive.check)
            pending[f] = (index, unit)
        for _ in range(width):
            submit()
        try:
            while pending:
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                # 先核對這輪完成的全部結果，再送下一輪，避免已知失敗後繼續派送。
                for f in done:
                    index, unit = pending.pop(f)
                    response, _ = f.result()
                    results[index] = (unit, response)
                for _ in done:
                    submit()
        except BaseException:
            archive.cancelled.set()
            for f in pending:
                f.cancel()
            raise
    return [results[i] for i in sorted(results)]


def projection(full, reviews):
    unit, response = combine_reviews(full, reviews)
    return project_review(full, unit, response)


@pytest.mark.parametrize('width', [1, 4, 8])
def test_parallel_review_matches_serial_merge_and_cached_resume(tmp_path, monkeypatch, width):
    full, units = inputs()
    before = deepcopy((full, units))
    gate = Barrier(width)
    mutex = Lock()
    active = peak = calls = 0

    def model(client, **kwargs):
        nonlocal active, peak, calls
        with mutex:
            active += 1
            peak = max(peak, active)
            calls += 1
        try:
            gate.wait(timeout=5)
            return wire_proposal(proposal())
        finally:
            with mutex:
                active -= 1
    monkeypatch.setattr('runtime.material_review.request_semantics', model)
    archive = Archive(tmp_path / 'archive')
    reviewed = parallel(units, archive, width)
    assert peak == width and calls == 16 and active == 0
    assert projection(full, reviewed) == projection(full, [(u, proposal()) for u in units])
    assert (full, units) == before
    assert len(list(archive.root.glob('cache-*.json'))) == 16
    monkeypatch.setattr('runtime.material_review.request_semantics', lambda *a, **k: pytest.fail('cached batch replayed'))
    assert projection(full, parallel(units, archive, width)) == projection(full, reviewed)


def test_one_bad_alias_is_repaired_without_replaying_other_parallel_batches(tmp_path, monkeypatch):
    full, units = inputs(8)
    calls = {}
    mutex = Lock()
    def model(client, **kwargs):
        request = kwargs['request']
        with mutex:
            calls[request['title']] = calls.get(request['title'], 0) + 1
        response = proposal()
        if request['title'] == 'synthetic-3' and 'review_correction' not in request:
            response['alias_edits'] = [{'concept': 4, 'remove': ['EFFECTIVENESS'],
                'evidence': [4], 'reason': '合成大小寫錯誤'}]
        return wire_proposal(response)
    monkeypatch.setattr('runtime.material_review.request_semantics', model)
    reviewed = parallel(units, Archive(tmp_path / 'archive'), 8)
    assert sum(calls.values()) == 9 and calls['synthetic-3'] == 2
    assert projection(full, reviewed) == projection(full, [(u, proposal()) for u in units])


def test_failure_drains_inflight_work_and_resume_reuses_successes(tmp_path, monkeypatch):
    _, units = inputs(16)
    archive = Archive(tmp_path / 'archive')
    # 先建立一個真正通過既有 validator 的已完成批次。
    monkeypatch.setattr('runtime.material_review.request_semantics', lambda *a, **k: wire_proposal(proposal()))
    parallel(units[:1], archive, 1)
    gate = Barrier(8)
    calls = []
    mutex = Lock()
    def fail(client, **kwargs):
        name = kwargs['request']['title']
        with mutex:
            calls.append(name)
        if 'review_correction' in kwargs['request']:
            assert name == 'synthetic-1'
            return {'invalid': True}
        gate.wait(timeout=5)
        if name == 'synthetic-1':
            return {'invalid': True}
        assert archive.cancelled.wait(5)
        return wire_proposal(proposal())  # 晚到結果必須被 archive 的取消檢查拒絕。
    monkeypatch.setattr('runtime.material_review.request_semantics', fail)
    with pytest.raises(ReviewError, match='REVIEW_RESPONSE_INVALID'):
        parallel(units, archive, 8)
    assert len(calls) == 9 and calls.count('synthetic-1') == 2  # 八個在途批次，失敗批次僅補正一次。
    assert len(list(archive.root.glob('cache-*.json'))) == 1
    assert not any(archive.root.glob('call-00000[3-9]/response.json'))
    archive.cancelled.clear()
    resumed = []
    def model(client, **kwargs):
        resumed.append(kwargs['request']['title'])
        return wire_proposal(proposal())
    monkeypatch.setattr('runtime.material_review.request_semantics', model)
    assert len(parallel(units, archive, 8)) == 16
    assert len(resumed) == 15 and 'synthetic-0' not in resumed


@pytest.mark.parametrize('slots', [1, 4, 8])
def test_isolated_http_review_lock_capacity(provider_server, monkeypatch, slots):
    # slots=1 保留目前程式；4/8 只替換本測試程序的鎖，正式服務完全不變。
    if slots != 1:
        monkeypatch.setattr(provider, 'TEXT_LOCK', BoundedSemaphore(slots))
    release, mutex = Event(), Lock()
    active = peak = entered = 0
    def generate(prompt, schema, **kwargs):
        nonlocal active, peak, entered
        with mutex:
            active += 1
            entered += 1
            peak = max(peak, active)
        try:
            assert release.wait(8)
            return wire_proposal(proposal())
        finally:
            with mutex:
                active -= 1
    monkeypatch.setattr(provider, 'luna', generate)
    def call():
        return post(provider_server, '/semantics', {'job_id': str(uuid4()), 'job_token': str(uuid4()),
            'task': 'material_review', 'prompt': 'synthetic', 'request': {}, 'schema': {}})
    with ThreadPoolExecutor(max_workers=9) as pool:
        pending = [pool.submit(call) for _ in range(9)]
        try:
            wait_until(lambda: entered == slots)
            assert active == peak == slots
        finally:
            release.set()
        assert all(f.result()[0] == 200 for f in pending)
    assert entered == 9 and peak == slots
