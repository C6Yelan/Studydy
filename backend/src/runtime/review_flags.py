"""唯讀審查封存推導逐字稿疑義；旗標不寫入不可變知識結構。"""
from sqlalchemy import select
from knowledge_map.claim_literals import transcript_evidence_ids
from .storage.tables import MaterialProcessingRun
from .storage.analysis_archive import _material_directory, _checkpoint_input_digest, _read_envelope


def uncertain_concepts(session, owner, material_id, revision):
    cache = session.info.setdefault('uncertain_concepts', {})
    key = (owner, material_id, revision)
    if key in cache:
        return cache[key]
    run = session.scalar(select(MaterialProcessingRun).where(
        MaterialProcessingRun.learner_id == owner,
        MaterialProcessingRun.material_id == material_id,
        MaterialProcessingRun.output_binding['knowledge_structure_revision'].astext == revision))
    if run is None:
        return set()
    inherited = uncertain_concepts(session, owner, material_id, run.base_revision) if run.base_revision else set()
    result = set(inherited)
    root = _material_directory(owner, material_id) / run.run_id.hex / 'review'
    signature = _checkpoint_input_digest(run)
    try:
        source = _read_envelope(root/'source.json', run.run_id, signature)['data']
        review = _read_envelope(root/'result.json', run.run_id, signature)['data']
        projection, proposal = review.get('projection'), review.get('proposal')
        if not projection or not proposal or source['revision'] != review['source_revision']:
            cache[key] = result
            return result
        assignments = proposal['assignments']
        mapped_assignments = projection['assignments']
        if len(assignments) != len(mapped_assignments):
            cache[key] = result
            return result
        remap = projection['concept_mapping']
        final_map = {}
        if review['applied_revision'] != revision:
            consolidated = _read_envelope(root/'consolidation/result.json', run.run_id, signature)['data']
            if consolidated['source_revision'] != review['applied_revision'] or consolidated['applied_revision'] != revision:
                cache[key] = result
                return result
            final_map = consolidated['concept_mapping']
        spoken = transcript_evidence_ids(source)
        concepts = {c['concept_id']: c for c in source['concepts']}
        for assignment, mapped in zip(assignments, mapped_assignments):
            original = mapped['concept_id']
            concept = concepts.get(original)
            if (assignment['issue'] != 'uncertain' or mapped['requested_action'] != assignment['action']
                    or concept is None or not any(q['evidence_refs'] and set(q['evidence_refs']) <= spoken for q in concept['claims'])):
                continue
            reviewed = remap.get(original)
            if reviewed is not None:
                result.add(final_map.get(reviewed, reviewed))
    except (OSError, ValueError, KeyError, TypeError):
        # 沒有足夠封存證據時不猜測疑義或概念對應；原 needs_review 狀態不變。
        pass
    cache[key] = result
    return result
