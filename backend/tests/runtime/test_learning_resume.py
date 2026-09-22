"""目前題組的 owner-bound resume；不再讀回獨立單題或舊重複 session。"""
import pytest
from fastapi.testclient import TestClient
from test_closed_loop_v1 import closed_loop
from test_material_library import library_materials, product_snapshot
from test_assessment_sets import concept_fixture, create, finish, read
from test_assessment_remediation import answer
from test_accounts import _app, ORIGIN, HEADERS

@pytest.fixture
def learning_records(library_materials, closed_loop):
    f=concept_fixture(closed_loop,2)
    identity=create(f);finish(f);answer(f,identity)
    return {**library_materials,'first':f['source'],'structure':f['document'],'active':f['study'],'set_id':identity}


def test_resume_reads_current_sets_without_single_question_projection(learning_records,tmp_path,monkeypatch):
    f=learning_records
    client=TestClient(_app(f['dsn'],tmp_path,monkeypatch),base_url=ORIGIN)
    client.cookies.set('studydy_session',f['token'])
    path=f"/v1/materials/{f['first'].material_id}/knowledge-structures/{f['structure']['revision']}/study-sessions/{f['active'].study_session_id}/resume"
    before=product_snapshot(f['dsn'])
    response=client.get(path,params={'run_id':f['structure']['run_id'],'set_id':str(f['set_id'])})
    assert response.status_code==200
    view=response.json()
    assert view['schema']=='study-resume/v5' and view['selected_set_id']==str(f['set_id'])
    assert not {'assessments','selected_assessment_revision'} & view.keys()
    assert product_snapshot(f['dsn'])==before
    assert client.get(path,params={'run_id':f['structure']['run_id'],'assessment_revision':'retired'}).status_code==400
    client.cookies.set('studydy_session',f['foreign'].raw_token)
    assert client.get(path,params={'run_id':f['structure']['run_id']}).status_code==404
