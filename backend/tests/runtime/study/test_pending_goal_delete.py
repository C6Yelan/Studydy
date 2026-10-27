from datetime import date, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from product_fixtures import closed_loop, HEADERS, ORIGIN, _app
from runtime.source_normalization import create_draft
from runtime.storage.database import database_session
from runtime.storage.tables import Material


def test_delete_pending_goal_is_owned_repeatable_and_rejects_published_head(closed_loop,tmp_path,monkeypatch):
    owner, published, _, _, dsn, token=closed_loop
    client=TestClient(_app(dsn,tmp_path,monkeypatch),base_url=ORIGIN)
    material=create_draft(owner.learner_id,'Draft','goal-delete',dsn=dsn)
    url=f'/v1/materials/{material}/goal'
    assert client.delete(url,headers=HEADERS).status_code==401
    client.cookies.set('studydy_session',token)
    goal={'schema':'learning-goal-update/v1','target_date':(date.today()+timedelta(days=7)).isoformat(),'target_percent':80}
    assert client.put(url,headers=HEADERS,json=goal).status_code==200
    assert client.delete(url).status_code==403
    for _ in range(2):
        response=client.delete(url,headers=HEADERS)
        assert response.status_code==200,response.text
        body=response.json()
        assert body['schema']=='learning-goal/v2' and body['goal'] is None and body['pending_goal'] is None
    with database_session(dsn) as db:assert db.get(Material,material).pending_goal is None
    assert client.delete(f'/v1/materials/{uuid4()}/goal',headers=HEADERS).status_code==404
    published_url=f'/v1/materials/{published.material_id}/goal'
    assert client.put(published_url,headers=HEADERS,json=goal).status_code==200
    before=client.get(published_url).json()
    rejected=client.delete(published_url,headers=HEADERS)
    assert rejected.status_code==409 and rejected.json()['reason_code']=='LEARNING_GOAL_PUBLISHED'
    assert client.get(published_url).json()==before
