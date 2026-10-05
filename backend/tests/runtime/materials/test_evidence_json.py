"""保存真實 PDF 可能出現的零字元，不能刪字或重算既有來源 hash。"""
from copy import deepcopy
from sqlalchemy import select,text
from product_fixtures import closed_loop
from runtime.storage.tables import KnowledgeStructure,Podcast,VoiceConversation,VoiceTurn,database_session
from runtime.storage.evidence_json import EvidenceJSONB
from runtime import podcasts,voice
from pdf_evidence.ocr_page_evidence import canonical_sha256


def test_lossless_json_database_roundtrip_preserves_literals_and_hash(closed_loop):
    owner,source,_,document,dsn,_=closed_loop
    changed=deepcopy(document)
    changed['evidence'][0]['exact_text']='\x00 source \\u0000 \ue0000 \ue000E and x != 0'
    expected=canonical_sha256(changed)
    with database_session(dsn) as db:
        row=db.get(KnowledgeStructure,(owner.learner_id,source.material_id,document['revision']))
        row.document=changed
    with database_session(dsn) as db:
        result=db.scalar(select(KnowledgeStructure.document).where(KnowledgeStructure.material_id==source.material_id))
        assert result==changed and canonical_sha256(result)==expected
        assert db.scalar(text("SELECT document->>'schema' FROM knowledge_structures WHERE material_id=:id"),{'id':source.material_id})=='knowledge-structure/v1'
        assert db.scalar(text("SELECT document->>'_studydy_json_storage' FROM knowledge_structures WHERE material_id=:id"),{'id':source.material_id})=='nul-object/v1'


def test_voice_and_podcast_evidence_arrays_roundtrip(closed_loop):
    owner,source,_,document,dsn,_=closed_loop
    c=voice.create(owner.learner_id,source.material_id,'conversation',dsn=dsn)
    turn=voice.add_turn(owner.learner_id,c['conversation_id'],'q','合成問題',dsn=dsn)
    payload={'text':'supported text','citations':[{'quote':'a\x00b \\u0000 \ue000'}]}
    with database_session(dsn) as db:db.get(VoiceTurn,turn['turn_id']).answer=payload
    with database_session(dsn) as db:assert db.get(VoiceTurn,turn['turn_id']).answer==payload
    saved=podcasts.create_podcast(owner.learner_id,source.material_id,document['revision'],
        '合成 NUL 測試',[c['concept_id'] for c in document['concepts']],'nul-podcast',delivery='solo',dsn=dsn)
    episodes=[{'claims':[{'text':'a\x00b','evidence':[{'quote':'\ue0000'}]}]}]
    with database_session(dsn) as db:db.get(Podcast,saved['podcast_id']).episodes=episodes
    with database_session(dsn) as db:assert db.get(Podcast,saved['podcast_id']).episodes==episodes
