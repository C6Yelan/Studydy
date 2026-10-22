"""教材審查共用合成資料、暫存 archive 與真實 HTTP 預設單槽行為。"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event, Lock
from uuid import uuid4
import json

from knowledge_map.material_review import _pack_review
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


def test_isolated_http_review_lock_capacity(provider_server, monkeypatch):
    slots = 1
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
