from fastapi.testclient import TestClient

from product_fixtures import HEADERS, ORIGIN, _app, closed_loop, library_materials, product_snapshot


def test_card_set_http_lifecycle_is_source_bound_and_not_learning(library_materials, tmp_path, monkeypatch):
    f=library_materials
    client=TestClient(_app(f['dsn'],tmp_path,monkeypatch),base_url=ORIGIN)
    path=f"/v1/materials/{f['first'].material_id}/card-sets"
    assert client.get(path).status_code==401
    client.cookies.set('studydy_session',f['token'])
    assert client.get(path).json()['card_sets']==[]
    before=product_snapshot(f['dsn'])
    document=f['structure']; revision=document['revision']
    body={'schema':'card-set-create/v1','knowledge_structure_revision':revision,'name':'重點卡組',
          'concept_ids':[c['concept_id'] for c in document['concepts']], 'ordering_policy':'published_order'}
    assert client.post(path,json=body,headers={'Idempotency-Key':'create'}).status_code==403
    assert client.post(path,json={**body,'learner_id':str(f['foreign'].learner_id)},headers={**HEADERS,'Idempotency-Key':'create'}).status_code==400
    response=client.post(path,json=body,headers={**HEADERS,'Idempotency-Key':'create'})
    assert response.status_code==201
    row=response.json(); url=path+'/'+row['card_set_id']
    assert client.post(path,json=body,headers={**HEADERS,'Idempotency-Key':'create'}).json()==row
    assert client.post(path,json={**body,'name':'Different'},headers={**HEADERS,'Idempotency-Key':'create'}).status_code==409
    for invalid in [[],['concept:sha256:'+'f'*64]]:
        assert client.post(path,json={**body,'concept_ids':invalid},headers={**HEADERS,'Idempotency-Key':'invalid'}).status_code==400
    assert client.post(path,json={**body,'knowledge_structure_revision':'knowledge-structure:sha256:'+'f'*64},headers={**HEADERS,'Idempotency-Key':'missing'}).status_code==404
    projected=client.get(url+'/cards'); assert projected.status_code==200
    assert projected.headers['cache-control']=='private, no-store'
    data=projected.json(); assert data['card_set']==row
    canonical=client.get(f"/v1/materials/{f['first'].material_id}/knowledge-structures/{revision}").json()
    assert data['cards']['cards']==canonical['concepts']
    assert data['cards']['selection']['knowledge_structure_revision']==revision
    evidence=data['cards']['cards'][0]['claims'][0]['evidence'][0]
    assert client.get(data['cards']['source_resolver']+'/'+evidence['evidence_id']+'/source').status_code==200
    edit={**body,'schema':'card-set-edit/v1','expected_version':1,'name':'新名稱'}
    updated=client.post(url+'/edit',headers=HEADERS,json=edit)
    assert updated.status_code==200 and updated.json()['version']==2
    assert client.post(url+'/edit',headers=HEADERS,json={**edit,'name':'lost update'}).status_code==409
    assert client.post(url+'/edit',headers=HEADERS,json={**edit,'knowledge_structure_revision':f['second_structure']['revision']}).status_code==409
    assert client.post(url+'/position',headers=HEADERS,json={'schema':'card-set-position/v1','current_position':1,'expected_version':2}).status_code==400
    assert client.get(url).json()==updated.json()
    client.cookies.clear();client.cookies.set('studydy_session',f['foreign'].raw_token)
    for suffix in ['', '/cards']:
        assert client.get(url+suffix).status_code==404
    assert client.post(url+'/edit',headers=HEADERS,json=edit).status_code==404
    assert client.post(url+'/position',headers=HEADERS,json={'schema':'card-set-position/v1','current_position':0,'expected_version':2}).status_code==404
    assert client.delete(url+'?version=2',headers=HEADERS).status_code==404
    client.cookies.clear();client.cookies.set('studydy_session',f['token'])
    assert client.get(url.replace(str(f['first'].material_id),str(f['uploaded'].material_id))).status_code==404
    assert client.delete(url+'?version=1',headers=HEADERS).status_code==409
    assert client.delete(url+'?version=2',headers=HEADERS).status_code==204
    assert client.get(url).status_code==404
    assert client.get(path).json()['card_sets']==[]
    assert product_snapshot(f['dsn'])==before
