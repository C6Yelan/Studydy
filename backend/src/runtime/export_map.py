"""依章節列出概念、完整重點、相關概念與來源頁碼的概念大綱；每章附 Mermaid 關係圖供預覽器顯示。"""
from html import escape
import re
from .export_documents import document_metadata

RELATIONS = {'prerequisite': '先備', 'part_of': '組成', 'application': '應用', 'example': '例子', 'contrast': '對照'}
TIMED = re.compile(r'\.(mp3|m4a|wav|mp4|youtube)$', re.I)


def _text(value):
    return re.sub(r'([\\`*\[\]<>|])', r'\\\1', str(value))


def _sources(concept):
    # 同一概念的所有重點合併成一行，只留檔名與頁碼（影音為段）；區塊編號對讀者沒有意義。
    pages = {}
    for claim in concept['claims']:
        for evidence in claim['evidence']:
            name = evidence.get('source_name') or (evidence.get('source_id') or '來源')[:8]
            pages.setdefault(name, set()).add(evidence.get('normalized_page') or evidence['page'])
    return '；'.join(
        f'{_text(name)} 第 {"、".join(str(page) for page in sorted(found))} {"段" if TIMED.search(name) else "頁"}'
        for name, found in pages.items())


def markdown(view, regions):
    concepts = {c['concept_id']: c for c in view['concepts']}
    nodes = {identity: f'c{index}' for index, identity in enumerate(concepts)}
    related = {}
    for relation in view['relations']:
        related.setdefault(relation['source_concept_id'], []).append(
            f'{RELATIONS[relation["type"]]} → {_text(concepts[relation["target_concept_id"]]["label"])}')
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
            lines += [f'### {_text(concept["label"])}', '', *(f'- {claim["text"]}' for claim in concept['claims']), '']
            if identity in related:
                lines += ['相關：' + '；'.join(related[identity]), '']
            if sources := _sources(concept):
                lines += [f'來源：{sources}', '']
    return '\n'.join(lines)
