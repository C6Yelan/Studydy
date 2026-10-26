"""S2／S3／E1c／R1 的來源與唯讀投影，僅合成資料。"""
from copy import deepcopy
import json
from uuid import UUID

from content_identity import canonical_sha256
from product_fixtures import closed_loop
from test_source_revisions import revisions
from runtime.material_processing import read_material_processing_run
from runtime.storage.database import database_session
from runtime.storage.tables import MaterialProcessingRun, KnowledgeStructure
from runtime.storage.knowledge_structures import read_knowledge_structure
from runtime.storage import knowledge_reads
from runtime.source_display import display_metadata


def test_single_image_uses_existing_display_name_without_changing_stored_name():
    assert display_metadata('Transformer_full_architecture（圖片）.zip', 'application/zip', 1)['display_name'] == 'Transformer_full_architecture'


def test_run_additions_and_published_counts_survive_old_structure_cleanup(revisions):
    owner, material, _, dsn, add, start, execute, _, old, _ = revisions
    extra = add('B.pdf', 'A queue removes the earliest inserted element first.')
    run = start([extra], 'summary', old['revision'])
    assert run.display_summary['existing_source_count'] == 1
    assert [s['display_name'] for s in run.display_summary['added_sources']] == ['B.pdf']
    result = execute()
    assert result.status == 'succeeded'
    changed = result.display_summary['structure_changes']
    assert changed['before'] == {'concepts':len(old['concepts']),'relations':len(old['relations'])}
    current = read_knowledge_structure(owner.learner_id, material, revision=result.output_binding['knowledge_structure_revision'], dsn=dsn)
    assert changed['after'] == {'concepts':len(current.document['concepts']),'relations':len(current.document['relations'])}
    # 摘要已獨立落地，讀取不用重新抓舊結構。
    with database_session(dsn) as db:
        assert db.get(MaterialProcessingRun, run.run_id).display_summary == result.display_summary
    assert read_material_processing_run(owner.learner_id, run.run_id, dsn=dsn).display_summary == result.display_summary


def test_existing_spoken_review_is_projected_without_rewriting_structure(closed_loop, monkeypatch, tmp_path):
    from test_media_source_flow import _client, _draft, _youtube, _add_youtube, _publish, TRANSCRIPT
    from runtime import media_sources
    from runtime.source_normalization import normalize_next, read_sources
    from runtime.review_flags import uncertain_concepts
    from runtime.storage.analysis_archive import _material_directory, _checkpoint_input_digest
    owner, _, _, _, dsn, _ = closed_loop
    client = _client(closed_loop)
    material = UUID(_draft(client, 'review-flags'))
    _youtube(monkeypatch)
    monkeypatch.setattr(media_sources, '_subtitle_segments', lambda *_: TRANSCRIPT)
    assert _add_youtube(client, material, 'https://youtu.be/iG9CE55wbtY').status_code == 202
    assert normalize_next(dsn=dsn)
    source, = read_sources(owner.learner_id, material, dsn=dsn)['sources']
    document, published = _publish(closed_loop, material, source['normalization_id'], 'review-flags', monkeypatch, tmp_path)
    identity = document['concepts'][0]['concept_id']
    with database_session(dsn) as db:
        run = db.get(MaterialProcessingRun, UUID(document['run_id']))
        signature = _checkpoint_input_digest(run)
        before = canonical_sha256(db.get(KnowledgeStructure, (owner.learner_id, material, published.revision)).document)
    root = _material_directory(owner.learner_id, material)/UUID(document['run_id']).hex/'review'
    root.mkdir(parents=True, mode=0o700)
    payloads = {'source': document, 'result': {
        'source_revision':document['revision'], 'applied_revision':published.revision,
        'proposal': {'assignments':[{'concept':0,'action':'needs_review','issue':'uncertain'}]},
        'projection': {'assignments':[{'concept_id':identity,'requested_action':'needs_review'}], 'concept_mapping':{identity:identity}},
    }}
    for name, data in payloads.items():
        (root/f'{name}.json').write_text(json.dumps({'run_id':document['run_id'],'signature':signature,'data':data,'data_sha256':canonical_sha256(data)}))
    with database_session(dsn) as db:
        assert uncertain_concepts(db, owner.learner_id, material, published.revision) == {identity}
        assert knowledge_reads.outline(db, owner.learner_id, material, published.revision)['concepts'][0]['uncertain']
        assert canonical_sha256(db.get(KnowledgeStructure, (owner.learner_id, material, published.revision)).document) == before
    payloads['result']['applied_revision'] = 'unknown'
    data = payloads['result']
    (root/'result.json').write_text(json.dumps({'run_id':document['run_id'],'signature':signature,'data':data,'data_sha256':canonical_sha256(data)}))
    with database_session(dsn) as db:
        assert uncertain_concepts(db, owner.learner_id, material, published.revision) == set()


def test_presentation_migration_applies_and_rolls_back(clean_database_dsn, migrations_dir, tmp_path):
    import shutil
    import psycopg
    from runtime.storage.migrations import load_migrations, run_migrations
    previous = tmp_path/'previous'
    previous.mkdir()
    for path in sorted(migrations_dir.glob('*.sql'))[:-1]:
        shutil.copyfile(path, previous/path.name)
    run_migrations(clean_database_dsn, migrations_dir=previous)
    migrations = load_migrations(migrations_dir)
    with psycopg.connect(clean_database_dsn, autocommit=True) as db:
        with db.transaction(force_rollback=True):
            db.execute(migrations[-1].sql)
            assert db.execute("SELECT count(*) FROM information_schema.columns WHERE (table_name,column_name) IN (('materials','pending_goal'),('material_processing_runs','display_summary'),('topic_scopes','starting_level'),('topic_scopes','learning_goal'),('material_notes','started_at'))").fetchone() == (5,)
        assert db.execute("SELECT count(*) FROM information_schema.columns WHERE table_name='materials' AND column_name='pending_goal'").fetchone() == (0,)
    assert run_migrations(clean_database_dsn, migrations_dir=migrations_dir) == (len(migrations),)
