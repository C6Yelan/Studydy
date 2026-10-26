"""知識地圖的完整概念大綱 Markdown。"""
from .export_documents import document_metadata, md_text

RELATIONS = {'prerequisite': '先備', 'part_of': '組成', 'application': '應用', 'example': '例子', 'contrast': '對照'}


def markdown(view, regions):
    concepts = {c['concept_id']: c for c in view['concepts']}
    lines = ['# 知識地圖概念大綱', '', *(md_text(line) for line in document_metadata(view)), '']
    for region in regions['regions']:
        lines += [f'## {md_text(region["label"])}', '']
        for identity in region['concept_ids']:
            c = concepts[identity]
            lines.append(f'- {md_text(c["label"])}（{c["claim_count"]} 個重點）')
        lines.append('')
    lines += ['## 概念關係', '']
    for r in view['relations']:
        lines.append(f'- {md_text(concepts[r["source_concept_id"]]["label"])} → {md_text(concepts[r["target_concept_id"]]["label"])}：{RELATIONS[r["type"]]}')
    return '\n'.join(lines)
