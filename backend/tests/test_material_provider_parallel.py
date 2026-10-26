"""真 HTTP 驗證 provider 八個教材槽位；不呼叫真實模型。"""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from uuid import uuid4

from test_podcast_process import provider_server, provider, post, wait_until


def test_provider_allows_eight_material_calls_and_keeps_ninth_waiting(monkeypatch,provider_server):
    release=Event();mutex=Lock();active=0;peak=0;entered=0
    def generate(prompt,schema,**kwargs):
        nonlocal active,peak,entered
        with mutex:active+=1;entered+=1;peak=max(peak,active)
        try:
            assert release.wait(8)
            return {'concepts':[],'relations':[]}
        finally:
            with mutex:active-=1
    monkeypatch.setattr(provider,'luna',generate)
    def call():return post(provider_server,'/semantics',{'job_id':str(uuid4()),'job_token':str(uuid4()),'task':'material_semantics','prompt':'synthetic','request':{},'schema':{}})
    with ThreadPoolExecutor(max_workers=9) as pool:
        futures=[pool.submit(call) for _ in range(9)]
        try:
            wait_until(lambda:entered==8)
            assert active==peak==8
        finally:release.set()
        assert all(f.result()[0]==200 for f in futures)
    assert entered==9 and peak==8 and active==0


def test_temporary_background_cap_reserves_one_slot_for_other_text(monkeypatch, provider_server):
    from threading import BoundedSemaphore
    monkeypatch.setattr(provider, 'TEST_BACKGROUND_SLOTS', BoundedSemaphore(2))
    release, mutex = Event(), Lock()
    active = {'background': 0, 'other': 0}
    peak = {'background': 0, 'other': 0}
    def generate(prompt, schema, **kwargs):
        kind = 'background' if prompt.startswith('background') else 'other'
        with mutex:
            active[kind] += 1
            peak[kind] = max(peak[kind], active[kind])
        try:
            assert release.wait(8)
            return {'synthetic': True}
        finally:
            with mutex: active[kind] -= 1
    monkeypatch.setattr(provider, 'luna', generate)
    def call(task):
        kind = 'background' if task in ('material_review', 'material_semantics') else 'other'
        return post(provider_server, '/semantics', {'job_id': str(uuid4()), 'job_token': str(uuid4()),
            'task': task, 'prompt': kind, 'request': {}, 'schema': {},
            'tags': {'feature': 'material_analysis' if task == 'material_semantics' else 'material_review' if task == 'material_review' else 'assessment'}})
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(call, task) for task in
            ('material_review', 'material_semantics', 'material_review', 'material_semantics', 'assessment', 'assessment')]
        try:
            wait_until(lambda: active == {'background': 2, 'other': 1})
            assert sum(active.values()) == 3
        finally:
            release.set()
        assert all(f.result()[0] == 200 for f in futures)
    assert peak == {'background': 2, 'other': 1}


def test_chapters_with_review_task_use_single_text_slot(monkeypatch, provider_server):
    from threading import BoundedSemaphore
    monkeypatch.setattr(provider, 'TEST_BACKGROUND_SLOTS', BoundedSemaphore(2))
    release, mutex = Event(), Lock()
    counts = {'chapters': 0, 'material_review': 0}
    entered = {'chapters': 0, 'material_review': 0}
    def generate(prompt, schema, **kwargs):
        prompt = prompt.split("\nINPUT:\n", 1)[0]
        with mutex:
            counts[prompt] += 1
            entered[prompt] += 1
        try:
            assert release.wait(8)
            return {'synthetic': True}
        finally:
            with mutex:
                counts[prompt] -= 1
    monkeypatch.setattr(provider, 'luna', generate)
    def call(feature):
        return post(provider_server, '/semantics', {'job_id': str(uuid4()), 'job_token': str(uuid4()),
            'task': 'material_review', 'prompt': feature, 'request': {}, 'schema': {},
            'tags': {'feature': feature}})
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(call, f) for f in ('chapters', 'chapters', 'material_review', 'material_review')]
        try:
            wait_until(lambda: entered['material_review'] == 2 and entered['chapters'] == 1)
            assert counts == {'chapters': 1, 'material_review': 2}
        finally:
            release.set()
        assert all(f.result()[0] == 200 for f in futures)
