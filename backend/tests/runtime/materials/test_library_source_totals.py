import io
import zipfile

import pymupdf
from sqlalchemy import event

from product_fixtures import closed_loop
from sources.test_media_source_flow import _client, _draft, _png, _wave, _fake_stt, _youtube, _add_youtube, TRANSCRIPT
from runtime import media_sources
from runtime.source_normalization import upload_source, normalize_next, remove_staged_source
from runtime.storage.database import _database_engine
from runtime.storage.materials import read_material_library


def test_library_totals_keep_units_and_unknown_media_without_extra_source_queries(closed_loop, monkeypatch):
    learner, _, _, _, dsn, _=closed_loop
    owner=learner.learner_id
    client=_client(closed_loop)
    material=_draft(client,'totals')
    _youtube(monkeypatch)
    monkeypatch.setattr(media_sources,'_subtitle_segments',lambda *_: TRANSCRIPT)
    assert _add_youtube(client,material,'https://youtu.be/iG9CE55wbtY').status_code==202
    assert normalize_next(dsn=dsn)
    _fake_stt(monkeypatch,[{'start':0.0,'end':1.0,'text':'A stack removes the latest item first.'}])
    upload_source(owner,material,_wave(10),'lecture.wav','audio/wav','audio',dsn=dsn)
    assert normalize_next(dsn=dsn)
    engine=_database_engine(dsn)
    def measured():
        queries=[]
        def record(_connection,_cursor,statement,*_):
            if statement.lstrip().upper().startswith('SELECT'):queries.append(statement)
        event.listen(engine,'before_cursor_execute',record)
        try:
            item=read_material_library(owner,material_id=material,dsn=dsn)[0]
        finally:
            event.remove(engine,'before_cursor_execute',record)
        return item,len(queries)
    first,queries=measured()
    assert first['source_totals']=={'page_count':0,'image_count':0,'media_seconds':210}
    with pymupdf.open() as pdf:
        for i in range(3):pdf.new_page().insert_text((72,72),f'Document page {i+1}.')
        document=pdf.tobytes()
    upload_source(owner,material,document,'notes.pdf','application/pdf','document',dsn=dsn)
    assert normalize_next(dsn=dsn)
    images=io.BytesIO()
    with zipfile.ZipFile(images,'w') as archive:
        archive.writestr('0001.png',_png('Stack'))
        archive.writestr('0002.png',_png('Queue'))
    image_id=upload_source(owner,material,images.getvalue(),'images.zip','application/zip','images',dsn=dsn)
    assert normalize_next(dsn=dsn)
    mixed,mixed_queries=measured()
    assert mixed['source_count']==4
    assert mixed['source_totals']=={'page_count':3,'image_count':2,'media_seconds':210}
    # 加入來源仍用原批次來源查詢；合計不逐來源再查 DB 或重讀 mapping。
    assert mixed_queries==queries
    response=client.get('/v1/materials')
    assert response.status_code==200,response.text
    assert next(item for item in response.json()['materials'] if item['material_id']==str(material))['source_totals']==mixed['source_totals']
    pending=upload_source(owner,material,_wave(2),'unknown.wav','audio/wav','unknown',dsn=dsn)
    assert measured()[0]['source_totals']=={'page_count':3,'image_count':2,'media_seconds':None}
    remove_staged_source(owner,material,pending,dsn=dsn)
    remove_staged_source(owner,material,image_id,dsn=dsn)
    assert measured()[0]['source_totals']=={'page_count':3,'image_count':0,'media_seconds':210}


def test_library_totals_without_media_are_zero(closed_loop):
    owner,source,_,_,dsn,_=closed_loop
    item=read_material_library(owner.learner_id,material_id=source.material_id,dsn=dsn)[0]
    assert item['source_totals']=={'page_count':1,'image_count':0,'media_seconds':0}
    client=_client(closed_loop)
    empty=_draft(client,'empty-totals')
    assert read_material_library(owner.learner_id,material_id=empty,dsn=dsn)[0]['source_totals']=={'page_count':0,'image_count':0,'media_seconds':0}
