"""以固定輸入與前後結構建立執行摘要，發布後不依賴舊結構繼續存在。"""
from sqlalchemy import select
from .source_display import display_metadata
from .storage.tables import MaterialSourceSet, MaterialProcessingRun, KnowledgeStructure


def run_summary(session, run, document=None):
    source_set = session.get(MaterialSourceSet, run.input_source_set_id)
    items = source_set.manifest['items']
    previous = None
    old_ids = set()
    if run.base_revision:
        previous = session.scalar(select(MaterialProcessingRun).where(
            MaterialProcessingRun.learner_id == run.learner_id,
            MaterialProcessingRun.material_id == run.material_id,
            MaterialProcessingRun.output_binding['knowledge_structure_revision'].astext == run.base_revision))
        if previous:
            old_set = session.get(MaterialSourceSet, previous.input_source_set_id)
            old_ids = {item['source_id'] for item in old_set.manifest['items']}
    result = {
        'added_sources': [{'source_id': item['source_id'],
                           'display_name': display_metadata(item['original_name'], item['media_type'], item['page_count'])['display_name']}
                          for item in items if item['source_id'] not in old_ids],
        'existing_source_count': len(old_ids), 'structure_changes': None,
    }
    before = session.get(KnowledgeStructure, (run.learner_id, run.material_id, run.base_revision)) if run.base_revision else None
    if document is None and run.output_binding:
        saved = session.get(KnowledgeStructure, (run.learner_id, run.material_id,
                            run.output_binding['knowledge_structure_revision']))
        document = saved.document if saved else None
    if document is not None and (before is not None or not run.base_revision):
        result['structure_changes'] = {
            'before': {'concepts': len(before.document['concepts']) if before else 0,
                       'relations': len(before.document['relations']) if before else 0},
            'after': {'concepts': len(document['concepts']), 'relations': len(document['relations'])},
        }
    return result
