"""含完整重點、來源與每章 Mermaid 的概念大綱。"""
from html import escape
import re
from .export_documents import document_metadata, source_text

RELATIONS = {'prerequisite': '先備', 'part_of': '組成', 'application': '應用', 'example': '例子', 'contrast': '對照'}


def _text(value):
    return re.sub(r'([\\`*\[\]<>|])', r'\\\1', str(value))


def markdown(view, regions):
    concepts = {c['concept_id']: c for c in view['concepts']}
    nodes = {identity: f'c{index}' for index, identity in enumerate(concepts)}
    lines = ['# 知識地圖概念大綱', '', *(_text(line) for line in document_metadata(view)), '']
    for region in regions['regions']:
        lines += [f'## {_text(region["label"])}', '', '```mermaid', 'flowchart LR']
        selected = set(region['concept_ids'])
        for identity in region['concept_ids']:
            label = escape(concepts[identity]['label'], quote=True).replace('\n', '<br/>')
            lines.append(f'  {nodes[identity]}["{label}"]')
        for relation in view['relations']:
            source, target = relation['source_concept_id'], relation['target_concept_id']
            if source in selected and target in selected:
                lines.append(f'  {nodes[source]} -->|{RELATIONS[relation["type"]]}| {nodes[target]}')
        lines += ['```', '']
        for identity in region['concept_ids']:
            concept = concepts[identity]
            lines += [f'### {_text(concept["label"])}', '']
            for claim in concept['claims']:
                lines += [claim['text'], '']
                lines += ['來源：' + '；'.join(dict.fromkeys(source_text(e) for e in claim['evidence'])), '']
    lines += ['## 概念關係', '']
    for relation in view['relations']:
        lines.append(f'- {_text(concepts[relation["source_concept_id"]]["label"])} → {_text(concepts[relation["target_concept_id"]]["label"])}：{RELATIONS[relation["type"]]}')
    return '\n'.join(lines)
