"""現行模型快照與完整性檢查的責任邊界；只用合成資料與隔離 DB。"""
from collections import Counter
from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID

import pytest

from learning_adaptation import assessment_sets as sets, assessments
from learning_adaptation.learner_progress import derive_learner_progress
from pdf_evidence.ocr_page_evidence import canonical_sha256
from runtime import material_processing
from runtime.material_runtime import lock_matches_binding
from runtime.source_resolver import resolve_evidence_source, verify_source_files
from runtime.storage import artifacts, source_artifacts
from runtime.storage.knowledge_structures import read_knowledge_structure, KnowledgeStructureStoreError
from runtime.storage.tables import Assessment, AssessmentSet, MaterialProcessingRun, database_session
from test_assessment_sets import closed_loop, concept_fixture, create, read, model_for
from test_assessment_remediation import finish, answer


def test_each_product_uses_its_own_execution_snapshot(closed_loop, monkeypatch):
    learner, source, settings, document, dsn, token = closed_loop
    configured = deepcopy(settings)
    configured['runtime_lock']['semantic_service'].update(model_id='example/material-model', revision='a' * 40)
    f = concept_fixture((learner, source, configured, document, dsn, token), 1)
    f['settings']['runtime_lock']['semantic_service'].update(model_id='example/question-model', revision='b' * 40)
    root = create(f)
    finish(f)
    before = read(f, root)
    assert before['status'] == 'ready'
    assert f['document']['provenance']['model_id'] == 'example/material-model'
    with database_session(dsn) as session:
        group = session.get(AssessmentSet, root)
        assert group.runtime_lock_document['semantic_service']['model_id'] == 'example/question-model'
    # 目前設定再改變，也不得冒充已保存產物的生成身分。
    f['settings']['runtime_lock']['semantic_service']['model_id'] = 'example/next-model'
    assert read(f, root) == before
    assert read_knowledge_structure(learner.learner_id, f['source'].material_id,
                                    revision=f['document']['revision'], dsn=dsn).document == f['document']
    with database_session(dsn) as session:
        run = session.get(MaterialProcessingRun, f['run'].run_id)
        corrupt = deepcopy(run.runtime_lock_document)
        corrupt['semantic_service']['model_id'] = 'example/unrelated-model'
        run.runtime_lock_document = corrupt
    with pytest.raises(KnowledgeStructureStoreError):
        read_knowledge_structure(learner.learner_id, f['source'].material_id,
                                 revision=f['document']['revision'], dsn=dsn)


def test_binding_cannot_claim_a_model_absent_from_its_lock(closed_loop):
    settings = closed_loop[2]
    binding = material_processing.runtime_binding(settings)
    assert lock_matches_binding(settings['runtime_lock'], binding)
    binding['model_id'] = 'example/forged-model'
    binding['runtime_binding_sha256'] = canonical_sha256({k: v for k, v in binding.items() if k != 'runtime_binding_sha256'})
    assert not lock_matches_binding(settings['runtime_lock'], binding)


def test_rehashed_question_provenance_must_match_its_set_snapshot(closed_loop):
    f = concept_fixture(closed_loop, 1)
    root = create(f); finish(f)
    view = read(f, root)
    with database_session(f['dsn']) as session:
        row = session.get(Assessment, view['assessment_revisions'][0])
        expected = assessments._provenance(session.get(AssessmentSet, root))
        changed = SimpleNamespace(**{c.name: deepcopy(getattr(row, c.name)) for c in Assessment.__table__.columns})
    changed.generation_provenance['model_id'] = 'example/forged-question-model'
    public, private, provenance = (getattr(changed, name) for name in
                                   ('public_document', 'private_answer_document', 'generation_provenance'))
    core = lambda document: {k: v for k, v in document.items() if k != 'assessment_revision'}
    revision = 'assessment:sha256:' + canonical_sha256({
        'public': core(public), 'private_sha256': canonical_sha256(core(private)),
        'provenance_sha256': canonical_sha256(core(provenance)),
    })
    changed.assessment_revision = revision
    for document in (public, private, provenance):
        document['assessment_revision'] = revision
    with pytest.raises(assessments.AssessmentError, match='ASSESSMENT_UNAVAILABLE'):
        assessments._stored(changed, expected)


def test_map_progress_and_set_reads_do_not_hash_source_files(closed_loop, monkeypatch):
    f = concept_fixture(closed_loop, 2)
    root = create(f); finish(f); answer(f, root)
    def unexpected(*_args, **_kwargs):
        pytest.fail('metadata read opened a source file')
    for module in (artifacts, source_artifacts):
        monkeypatch.setattr(module, '_verify_file', unexpected)
    read_knowledge_structure(f['learner'].learner_id, f['source'].material_id,
                             revision=f['document']['revision'], dsn=f['dsn'])
    assert read(f, root)['answered_count'] == 2
    assert derive_learner_progress(f['learner'], f['study'].study_session_id, dsn=f['dsn']).event_watermark == 2


def test_evidence_locator_hashes_only_the_mapping_it_uses(closed_loop, monkeypatch):
    learner, source, _, document, dsn, _ = closed_loop
    checked = []
    original = source_artifacts._verify_file
    def count(path, digest, size):
        checked.append(path.name)
        return original(path, digest, size)
    monkeypatch.setattr(source_artifacts, '_verify_file', count)
    resolved = resolve_evidence_source(learner.learner_id, source.material_id,
                                       document['revision'], document['evidence'][0]['evidence_id'], dsn=dsn)
    assert resolved['normalized_page'] == 1
    mapping = document['input_binding']['manifest']['items'][0]['mapping_artifact_id']
    assert checked == [UUID(mapping).hex]


@pytest.mark.parametrize('role', ['original', 'normalized', 'mapping'])
def test_file_tampering_fails_when_used_and_at_publication(closed_loop, role):
    learner, source, settings, document, dsn, _ = closed_loop
    binding = document['input_binding']
    identity = UUID(binding['manifest']['items'][0][role + '_artifact_id'])
    path = artifacts._object_path(artifacts._root(), identity)
    contents = path.read_bytes()
    path.chmod(0o600)
    path.write_bytes(bytes([contents[0] ^ 1]) + contents[1:])
    assert read_knowledge_structure(learner.learner_id, source.material_id,
                                    revision=document['revision'], dsn=dsn).document == document
    with pytest.raises(artifacts.ArtifactError):
        with source_artifacts.open_verified_artifact(learner.learner_id, identity, dsn=dsn):
            pytest.fail('corrupt bytes were exposed')
    with pytest.raises(artifacts.ArtifactError):
        verify_source_files(learner.learner_id, binding, dsn=dsn)
    # 真正的發布入口也必須拒絕，而不是只測底層 hash helper。
    from product_fixtures import seed_run, publish_fixture_structure
    from test_closed_loop_v1 import _structure
    run = seed_run(learner.learner_id, source.material_id, source.artifact_id, 'corrupt-publication', settings, dsn=dsn)
    claim = material_processing.claim_next_material_processing_run(dsn=dsn)
    assert claim.run.run_id == run.run_id
    for stage in ('evidence', 'semantics', 'publishing'):
        material_processing._record_progress(run.run_id, stage, 1, 1, dsn=dsn)
    candidate = _structure(str(run.run_id), source.sha256, settings['runtime_lock'])
    with pytest.raises(KnowledgeStructureStoreError):
        publish_fixture_structure(learner.learner_id, source.material_id, run.run_id, candidate, dsn=dsn)


def test_prior_questions_are_validated_once_and_set_snapshot_is_shared(closed_loop, monkeypatch):
    f = concept_fixture(closed_loop, 2)
    root = create(f); finish(f)
    checks = Counter(); snapshots = []
    stored, provenance = assessments._stored, assessments._provenance
    def counted(row, expected):
        checks[row.assessment_revision] += 1
        return stored(row, expected)
    def snapshot(group):
        snapshots.append(group.set_id)
        return provenance(group)
    monkeypatch.setattr(assessments, '_stored', counted)
    monkeypatch.setattr(assessments, '_provenance', snapshot)
    group = read(f, root)
    assert snapshots == [root] and set(checks.values()) == {1}
    answer(f, root)
    create(f, key='next-round')
    checks.clear(); snapshots.clear()
    work = sets.claim_set_work(dsn=f['dsn'])
    sets.execute_set_work(work, dsn=f['dsn'], semantic_call=model_for(f))
    assert all(checks[revision] == 1 for revision in group['assessment_revisions'])
