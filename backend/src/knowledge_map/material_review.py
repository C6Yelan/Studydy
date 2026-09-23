"""以已保存的來源檢核完整教學單元；產生可審查投影，不改正式 KS／作答。"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Literal
import re
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pdf_evidence.ocr_page_evidence import canonical_sha256
from .semantic_projection import _project_claim
from .structure_rules import (
    RELATION_BASIS, RELATION_PRIORITY, _CODE_OR_FORMULA, _TECHNICAL,
    _id, _path, _revision,
)
from .structure_validation import validate_knowledge_structure


POLICY = 'material-review/v1'


class ReviewError(ValueError):
    pass


class _Closed(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class _Supported(_Closed):
    evidence: list[int] = Field(min_length=1)
    reason: str = Field(min_length=1)


class Assignment(_Supported):
    concept: int
    action: Literal['keep', 'group', 'example', 'metadata', 'needs_review']
    target: int | None
    issue: Literal[
        'none', 'fragment', 'case', 'author', 'layout', 'scale', 'incomplete', 'uncertain'
    ]


class AliasEdit(_Supported):
    concept: int
    remove: list[str] = Field(min_length=1)


class ClaimEdit(_Supported):
    claim: int
    meaning: str = Field(min_length=1)


class RelationEdit(_Supported):
    relation: int
    action: Literal['reverse', 'remove', 'retype']
    relation_type: Literal['prerequisite', 'part_of', 'application', 'example', 'contrast'] | None


class Proposal(_Closed):
    assignments: list[Assignment]
    alias_edits: list[AliasEdit]
    claim_edits: list[ClaimEdit]
    relation_edits: list[RelationEdit]


def response_schema() -> dict:
    return Proposal.model_json_schema()


def _name(value: str) -> str:
    # 僅用於名稱比對；不改寫原始字面值、Claims 或 Evidence。
    return ''.join(unicodedata.normalize('NFKC', value).split()).casefold()


@dataclass
class ReviewUnit:
    source_digest: str
    payload: dict
    concepts: list[dict]
    claims: list[dict]
    evidence: list[dict]
    relations: list[dict]


def _pack_review(view: dict, concepts: list[dict], evidence: list[dict], title: str) -> ReviewUnit:
    selected = {concept['concept_id'] for concept in concepts}
    claims = list({
        claim['claim_id']: claim
        for concept in concepts for claim in concept['claims']
    }.values())
    relations = [
        relation for relation in view['relations']
        if relation['source_concept_id'] in selected
        and relation['target_concept_id'] in selected
    ]
    concept_handles = {concept['concept_id']: index for index, concept in enumerate(concepts)}
    claim_handles = {claim['claim_id']: index for index, claim in enumerate(claims)}
    evidence_handles = {item['evidence_id']: index for index, item in enumerate(evidence)}
    sections = {
        section['section_id']: section['title']
        for section in view['document_tree']['sections']
    }
    section_titles = {_name(section_title) for section_title in sections.values()}
    direction_rules = {
        'prerequisite': '必要先備→後續知識',
        'part_of': '部分→整體',
        'application': '概念→具體用途',
        'example': '抽象概念→具體實例',
        'contrast': '兩端自身是比較對象',
    }
    payload = {
        'policy': POLICY,
        'title': title,
        'concepts': [
            {
                'h': index,
                'label': concept['label'],
                'aliases': concept['aliases'],
                'claims': [claim_handles[claim['claim_id']] for claim in concept['claims']],
                'evidence': sorted({
                    evidence_handles[item['evidence_id']]
                    for claim in concept['claims'] for item in claim['evidence']
                }),
                'section_titles': [sections[section_id] for section_id in concept['section_ids']],
                'is_section_topic': _name(concept['label']) in section_titles,
            }
            for index, concept in enumerate(concepts)
        ],
        'claims': [
            {
                'h': index,
                'text': claim['text'],
                'evidence': [evidence_handles[item['evidence_id']] for item in claim['evidence']],
            }
            for index, claim in enumerate(claims)
        ],
        'evidence': [
            {
                'h': index,
                'source_name': item['source_name'],
                'page': item['normalized_page'],
                'kind': item['kind'],
                'text': item['quote'],
            }
            for index, item in enumerate(evidence)
        ],
        'relations': [
            {
                'h': index,
                'source': concept_handles[relation['source_concept_id']],
                'target': concept_handles[relation['target_concept_id']],
                'source_label': concepts[concept_handles[relation['source_concept_id']]]['label'],
                'target_label': concepts[concept_handles[relation['target_concept_id']]]['label'],
                'direction_rule': direction_rules[relation['type']],
                'type': relation['type'],
                'reason': relation['learner_reason'],
                'evidence': [
                    evidence_handles[reference]
                    for reference in relation['evidence_refs'] if reference in evidence_handles
                ],
            }
            for index, relation in enumerate(relations)
        ],
    }
    return ReviewUnit(
        canonical_sha256(view), payload,
        deepcopy(concepts), deepcopy(claims), deepcopy(evidence), deepcopy(relations),
    )


def combine_reviews(view: dict, reviews: list[tuple[ReviewUnit, dict]]) -> tuple[ReviewUnit, dict]:
    """彙整互不重疊的小節提案；衝突範圍不得以最後一份覆蓋前一份。"""
    selected = set()
    evidence = {}
    for unit, response in reviews:
        if unit.source_digest != canonical_sha256(view):
            raise ReviewError('REVIEW_SOURCE_CHANGED')
        validate_proposal(unit, response)
        concept_ids = {concept['concept_id'] for concept in unit.concepts}
        if selected & concept_ids:
            raise ReviewError('REVIEW_SCOPES_OVERLAP')
        selected.update(concept_ids)
        evidence.update({item['evidence_id']: item for item in unit.evidence})
    if not selected:
        raise ReviewError('REVIEW_SCOPE_EMPTY')

    merged = _pack_review(
        view,
        [concept for concept in view['concepts'] if concept['concept_id'] in selected],
        sorted(
            evidence.values(),
            key=lambda item: (
                item['source_id'], item['normalized_page'],
                item['block_order'], item['evidence_id'],
            ),
        ),
        '已檢核小節的整體整理候選',
    )
    concept_handles = {
        concept['concept_id']: index for index, concept in enumerate(merged.concepts)
    }
    claim_handles = {claim['claim_id']: index for index, claim in enumerate(merged.claims)}
    evidence_handles = {item['evidence_id']: index for index, item in enumerate(merged.evidence)}
    relation_handles = {
        relation['relation_id']: index for index, relation in enumerate(merged.relations)
    }
    proposal = {key: [] for key in ['assignments', 'alias_edits', 'claim_edits', 'relation_edits']}
    for unit, response in reviews:
        for kind, rows in response.items():
            for item in rows:
                row = deepcopy(item)
                if 'concept' in row:
                    row['concept'] = concept_handles[unit.concepts[row['concept']]['concept_id']]
                if row.get('target') is not None:
                    row['target'] = concept_handles[unit.concepts[row['target']]['concept_id']]
                if 'claim' in row:
                    row['claim'] = claim_handles[unit.claims[row['claim']]['claim_id']]
                if 'relation' in row:
                    relation_id = unit.relations[row['relation']]['relation_id']
                    row['relation'] = relation_handles[relation_id]
                row['evidence'] = [
                    evidence_handles[unit.evidence[handle]['evidence_id']]
                    for handle in row['evidence']
                ]
                proposal[kind].append(row)
    validate_proposal(merged, proposal)
    return merged, proposal


def validate_proposal(unit: ReviewUnit, value: dict) -> Proposal:
    try:
        proposal = Proposal.model_validate(value)
    except ValidationError:
        raise ReviewError('REVIEW_RESPONSE_INVALID') from None
    assignments = {a.concept: a for a in proposal.assignments}
    if (
        len(assignments) != len(proposal.assignments)
        or set(assignments) != set(range(len(unit.concepts)))
    ):
        raise ReviewError('REVIEW_CONCEPT_COVERAGE_INVALID')
    evidence_handles = set(range(len(unit.evidence)))
    owned = [
        {item['evidence_id'] for claim in concept['claims'] for item in claim['evidence']}
        for concept in unit.concepts
    ]

    def support(edit):
        if (
            len(set(edit.evidence)) != len(edit.evidence)
            or not set(edit.evidence) <= evidence_handles
            or not edit.reason.strip()
        ):
            raise ReviewError('REVIEW_EVIDENCE_INVALID')
        return {unit.evidence[handle]['evidence_id'] for handle in edit.evidence}

    for a in proposal.assignments:
        refs = support(a)
        if not refs & owned[a.concept]:
            raise ReviewError('REVIEW_CONCEPT_SUPPORT_INVALID')
        if a.action in {'group', 'example'}:
            if (
                a.target not in assignments or a.target == a.concept
                or assignments[a.target].action == 'metadata'
            ):
                raise ReviewError('REVIEW_TARGET_INVALID')
        elif a.target is not None:
            raise ReviewError('REVIEW_TARGET_INVALID')
        if a.action == 'metadata' and a.issue not in {'author', 'layout', 'scale'}:
            raise ReviewError('REVIEW_METADATA_REASON_INVALID')
    # 模型偶爾提出多層歸屬。循環無法解釋，拒絕；非循環的父節點則在投影時保留，
    # 不把整個章節沿著鏈條壓成一個過大的學習點。
    for key in assignments:
        seen = set()
        while assignments[key].action in {'group', 'example'}:
            if key in seen:
                raise ReviewError('REVIEW_GROUPING_CYCLE')
            seen.add(key)
            key = assignments[key].target
    for changes, key, limit in [
        (proposal.alias_edits, 'concept', len(unit.concepts)),
        (proposal.claim_edits, 'claim', len(unit.claims)),
        (proposal.relation_edits, 'relation', len(unit.relations)),
    ]:
        seen = set()
        for edit in changes:
            handle = getattr(edit, key)
            if handle not in range(limit) or handle in seen:
                raise ReviewError('REVIEW_EDIT_ID_INVALID')
            seen.add(handle)
            refs = support(edit)
            if key == 'concept':
                if (
                    len(set(edit.remove)) != len(edit.remove)
                    or not set(edit.remove) <= set(unit.concepts[handle]['aliases'])
                    or not refs & owned[handle]
                ):
                    raise ReviewError('REVIEW_ALIAS_EDIT_INVALID')
            elif key == 'claim' and not edit.meaning.strip():
                raise ReviewError('REVIEW_CLAIM_EDIT_INVALID')
            elif key == 'relation':
                relation = unit.relations[handle]
                endpoints = {relation['source_concept_id'], relation['target_concept_id']}
                endpoint_refs = {
                    item['evidence_id']
                    for concept in unit.concepts if concept['concept_id'] in endpoints
                    for claim in concept['claims'] for item in claim['evidence']
                }
                allowed_refs = endpoint_refs
                if edit.action == 'remove':
                    # 移除錯掛關係時，同頁章節標題可證明真正的脈絡；仍須引用至少一端。
                    pages = {
                        (item['source_id'], item['normalized_page'])
                        for item in unit.evidence if item['evidence_id'] in endpoint_refs
                    }
                    allowed_refs = endpoint_refs | {
                        item['evidence_id'] for item in unit.evidence
                        if item['kind'] == 'heading'
                        and (item['source_id'], item['normalized_page']) in pages
                    }
                # reverse/remove 可以重述原型別，但不能同時偷偷更換型別。
                invalid_type = (edit.relation_type is None if edit.action == 'retype'
                                else edit.relation_type not in (None, relation['type']))
                if not refs & endpoint_refs or not refs <= allowed_refs or invalid_type:
                    raise ReviewError('REVIEW_RELATION_EDIT_INVALID')
    return proposal


def _prose_glosses(text: str) -> str:
    """僅辨識中文後的英文括注；f(x)、(x)、數學及程式括號仍受保護。"""

    def replace(match):
        prefix = text[:match.start()].rstrip()
        return '' if prefix and '\u3400' <= prefix[-1] <= '\u9fff' else match.group()

    return re.sub(r'\([A-Za-z]{2,}(?:[ -][A-Za-z]+)*\)', replace, text)


def _corrected_claim(edit: ClaimEdit, sources: list[dict]) -> str | None:
    text = ' '.join(item['quote'] for item in sources)
    meaning = ' '.join(edit.meaning.split())
    numbers = lambda value: set(re.findall(r'\d+(?:\.\d+)?[%％]?', value))
    words = lambda value: set(re.findall(r'[A-Za-z][A-Za-z0-9_]*', value))
    if numbers(meaning) != numbers(text) or not words(meaning) <= words(text):
        return None
    # 圖表的方向／不等式也是教材條件，不能改寫成不帶方向的泛稱。
    symbols = lambda value: set(re.findall(r'[↑↓←→↔⇐⇒⇔≤≥≠±∓]', value))
    if symbols(meaning) != symbols(text):
        return None
    evidence = {
        item['evidence_id']: {'exact_text': item['quote']}
        for item in sources
    }
    projected = _project_claim({
        'meaning': meaning,
        'source_spans': [
            {'evidence_id': item['evidence_id'], 'quote': item['quote']}
            for item in sources
        ],
    }, evidence)
    if projected is None:
        return None
    if projected['projection'] != 'source_literal_repair':
        return projected['text']
    # 原保護把 (PDCA) 等英文括注也視為公式。本階段只允許字面值不變的散文整理，
    # 不放寬任何 code/formula Evidence、符號、數值或識別詞的保護。
    if any(item['kind'] in {'code', 'formula'} for item in sources):
        return None
    if (
        words(meaning) != words(text)
        or set(_TECHNICAL.findall(meaning)) != set(_TECHNICAL.findall(text))
    ):
        return None
    if any(_CODE_OR_FORMULA.search(_prose_glosses(item['quote'])) for item in sources):
        return None
    if _CODE_OR_FORMULA.search(_prose_glosses(meaning)):
        return None
    return meaning


def project_review(view: dict, unit: ReviewUnit, value: dict) -> dict:
    """保留完整來源與原 IDs；投影是整理提案，不冒充原 revision 的新內容。"""
    if canonical_sha256(view) != unit.source_digest:
        raise ReviewError('REVIEW_SOURCE_CHANGED')
    proposal = validate_proposal(unit, value)
    concepts = {c['concept_id']: c for c in view['concepts']}
    root = {key: key for key in concepts}
    roles = {key: 'keep' for key in concepts}
    blocked = []
    findings = []
    assignments = []
    prerequisites = {
        relation[endpoint]
        for relation in view['relations'] if relation['type'] == 'prerequisite'
        for endpoint in ['source_concept_id', 'target_concept_id']
    }
    parents = {assignment.target for assignment in proposal.assignments
               if assignment.action in {'group', 'example'}}
    for a in proposal.assignments:
        key = unit.concepts[a.concept]['concept_id']
        action = a.action
        if a.concept in parents and action in {'group', 'example'}:
            blocked.append({
                'concept_id': key,
                'reason': 'PARENT_KEPT_AS_LEARNING_UNIT',
                'proposed_action': action,
            })
            action = 'needs_review'
        if key in prerequisites and action in {'group', 'example'}:
            blocked.append({
                'concept_id': key,
                'reason': 'PREREQUISITE_ENDPOINT_KEPT',
                'proposed_action': action,
            })
            action = 'needs_review'
        if action == 'metadata' and (
            unit.payload['concepts'][a.concept]['is_section_topic'] or key in prerequisites
        ):
            blocked.append({
                'concept_id': key,
                'reason': 'PROTECTED_TOPIC_OR_PREREQUISITE',
                'proposed_action': action,
            })
            action = 'needs_review'
        roles[key] = action
        if action in {'group', 'example'}:
            root[key] = unit.concepts[a.target]['concept_id']
        if action == 'needs_review':
            findings.append({'concept_id': key, 'reason': a.reason})
        target = unit.concepts[a.target] if a.target is not None else None
        assignments.append({
            'concept_id': key,
            'action': action,
            'requested_action': a.action,
            'target_concept_id': (
                target['concept_id'] if target and action in {'group', 'example'} else None
            ),
            'requested_target_concept_id': target['concept_id'] if target else None,
            'reason': a.reason,
            'model_evidence_ids': [unit.evidence[handle]['evidence_id'] for handle in a.evidence],
            'source_evidence_ids': sorted({
                item['evidence_id']
                for claim in unit.concepts[a.concept]['claims'] for item in claim['evidence']
            }),
            'target_evidence_ids': sorted({
                item['evidence_id'] for claim in target['claims'] for item in claim['evidence']
            }) if target else [],
        })
    units = []
    for key, concept in concepts.items():
        if roles[key] in {'group', 'example', 'metadata'}:
            continue
        members = [k for k in concepts if root[k] == key and roles[k] != 'example']
        examples = [k for k in concepts if root[k] == key and roles[k] == 'example']
        units.append({
            'concept_id': key,
            'label': concept['label'],
            'aliases': list(concept['aliases']),
            'member_concept_ids': members,
            'example_concept_ids': examples,
            'claim_ids': list(dict.fromkeys(
                claim['claim_id'] for member in members for claim in concepts[member]['claims']
            )),
        })
    by_unit = {u['concept_id']: u for u in units}
    alias_changes = []
    for edit in proposal.alias_edits:
        original = unit.concepts[edit.concept]
        alias_changes.append({
            'concept_id': original['concept_id'],
            'remove': edit.remove,
            'reason': edit.reason,
            'evidence_ids': [unit.evidence[handle]['evidence_id'] for handle in edit.evidence],
        })
        if original['concept_id'] in by_unit:
            by_unit[original['concept_id']]['aliases'] = [
                alias for alias in original['aliases'] if alias not in edit.remove
            ]
    # 文字修正仍要通過既有技術字面值保護；不能把 fallback 當成修正成功。
    claim_changes = []
    for edit in proposal.claim_edits:
        sources = [unit.evidence[h] for h in edit.evidence]
        claim_id = unit.claims[edit.claim]['claim_id']
        source_text = _name(' '.join(item['quote'] for item in sources))
        meaning = _name(edit.meaning)
        owners = {
            concept['concept_id'] for concept in unit.concepts
            if any(claim['claim_id'] == claim_id for claim in concept['claims'])
        }
        introduced = [
            concept['label'] for concept in unit.concepts
            if concept['concept_id'] not in owners
            and _name(concept['label']) in meaning
            and _name(concept['label']) not in source_text
        ]
        if introduced:
            blocked.append({
                'claim_id': claim_id,
                'reason': 'CLAIM_REFERENT_NOT_CITED',
                'concepts': introduced,
            })
            continue
        corrected = _corrected_claim(edit, sources)
        if corrected is None:
            blocked.append({'claim_id': claim_id, 'reason': 'CLAIM_CORRECTION_NOT_SUPPORTED'})
        else:
            claim_changes.append({
                'original_claim_id': claim_id,
                'proposed_text': corrected,
                'evidence_ids': [item['evidence_id'] for item in sources],
                'reason': edit.reason,
            })
    relation_changes = {
        unit.relations[edit.relation]['relation_id']: edit
        for edit in proposal.relation_edits
    }
    relations, internalized, excluded = [], [], []
    for original in view['relations']:
        row = deepcopy(original)
        edit = relation_changes.get(row['relation_id'])
        if edit:
            applied = True
            if edit.action == 'remove':
                if row['type'] == 'prerequisite':
                    blocked.append({
                        'relation_id': row['relation_id'],
                        'reason': 'PREREQUISITE_REMOVAL_REQUIRES_REVIEW',
                    })
                    applied = False
                else:
                    excluded.append({'relation_id': row['relation_id'], 'reason': edit.reason})
                    continue
            elif edit.action == 'reverse':
                row['source_concept_id'], row['target_concept_id'] = (
                    row['target_concept_id'], row['source_concept_id']
                )
            else:
                if row['type'] == 'prerequisite' and edit.relation_type != 'prerequisite':
                    blocked.append({
                        'relation_id': row['relation_id'],
                        'reason': 'PREREQUISITE_REMOVAL_REQUIRES_REVIEW',
                    })
                    applied = False
                else:
                    row['type'] = edit.relation_type
            if applied:
                row['learner_reason'] = edit.reason
        s, t = row['source_concept_id'], row['target_concept_id']
        if roles[s] == 'metadata' or roles[t] == 'metadata':
            excluded.append({'relation_id': row['relation_id'], 'reason': 'metadata_endpoint'})
            continue
        source, target = root[s], root[t]
        if source == target:
            if row['type'] == 'prerequisite':
                raise ReviewError('REVIEW_COLLAPSES_PREREQUISITE')
            if row['type'] == 'example' and roles[s] == 'example' and root[s] == t:
                findings.append({
                    'relation_id': row['relation_id'],
                    'reason': 'EXAMPLE_DIRECTION_OR_TYPE_NEEDS_REVIEW',
                })
            internalized.append({
                'relation_id': row['relation_id'],
                'unit_id': source,
                'source_concept_id': s,
                'target_concept_id': t,
                'type': row['type'],
                'reason': row['learner_reason'],
            })
            continue
        relations.append({
            'original_relation_id': row['relation_id'],
            'source_concept_id': source,
            'target_concept_id': target,
            'type': row['type'],
            'reason': row['learner_reason'],
        })
    try:
        path = _path(units, relations)
    except ValueError:
        raise ReviewError('REVIEW_PREREQUISITE_CYCLE') from None
    metadata = [key for key in concepts if roles[key] == 'metadata']
    accounted = [
        key for review_unit in units
        for key in review_unit['member_concept_ids'] + review_unit['example_concept_ids']
    ] + metadata
    if len(accounted) != len(set(accounted)) or set(accounted) != set(concepts):
        raise ReviewError('REVIEW_CONTENT_LOST')
    return {
        'schema': 'material-review-projection/v1',
        'policy': POLICY,
        'source_revision': view['knowledge_structure_revision'],
        'source_sha256': unit.source_digest,
        'status': 'needs_review',
        'publication_authorized': False,
        'learning_units': units,
        'metadata_concept_ids': metadata,
        'relations': relations,
        'assignments': assignments,
        'internalized_relations': internalized,
        'excluded_relations': excluded,
        'learning_path': path,
        'alias_changes': alias_changes,
        'claim_changes': claim_changes,
        'blocked_changes': blocked,
        'findings': findings,
        'preserved_claim_ids': sorted({
            claim['claim_id']
            for concept in concepts.values() for claim in concept['claims']
        }),
        'preserved_evidence_ids': sorted({
            item['evidence_id']
            for concept in concepts.values()
            for claim in concept['claims'] for item in claim['evidence']
        }),
        'checks': {
            'all_concepts_accounted_for': True,
            'source_unchanged': canonical_sha256(view) == unit.source_digest,
            'path_covers_all_learning_units': len(path) == len(units),
            'dangling_relations': 0,
        },
    }


def apply_review(document: dict, view: dict, unit: ReviewUnit, response: dict) -> tuple[dict, dict]:
    """建立有新內容 hash 的正式候選；舊節點及修正對照由呼叫端封存。"""
    if (
        not validate_knowledge_structure(document)
        or view['knowledge_structure_revision'] != document['revision']
    ):
        raise ReviewError('REVIEW_SOURCE_CHANGED')
    projection = project_review(view, unit, response)
    result = deepcopy(document)
    old = {c['concept_id']: c for c in document['concepts']}
    evidence = {e['evidence_id']: e for e in document['evidence']}
    edits = {e['original_claim_id']: e for e in projection['claim_changes']}
    remap, claim_remap, concepts = {}, {}, []
    rejected_claim_edits = set()
    for group in projection['learning_units']:
        claims = {}
        for key in group['member_concept_ids'] + group['example_concept_ids']:
            for original in old[key]['claims']:
                claim = deepcopy(original)
                if edit := edits.get(claim['claim_id']):
                    # 原引用仍保留；補正的引用只能來自原文 Evidence。
                    refs = list(dict.fromkeys(claim['evidence_refs'] + edit['evidence_ids']))
                    spans = [
                        {'evidence_id': ref, 'quote': evidence[ref]['exact_text']}
                        for ref in refs
                    ]
                    projected = _project_claim({
                        'meaning': edit['proposed_text'],
                        'source_spans': spans,
                    }, evidence)
                    if projected is None or projected['projection'] != 'semantic_meaning':
                        rejected_claim_edits.add(original['claim_id'])
                    else:
                        claim.update(projected, evidence_refs=refs)
                        claim['claim_id'] = _id('claim', {
                            key: value for key, value in claim.items() if key != 'claim_id'
                        })
                claim_remap[original['claim_id']] = claim['claim_id']
                claims[claim['claim_id']] = claim
        refs = list(dict.fromkeys(e for q in claims.values() for e in q['evidence_refs']))
        aliases = sorted(set(group['aliases']) - {group['label']})
        identity = {
            'label': group['label'],
            'aliases': aliases,
            'claim_ids': list(claims),
            'evidence_refs': refs,
        }
        key = _id('concept', identity)
        for member in group['member_concept_ids'] + group['example_concept_ids']:
            remap[member] = key
        concepts.append({
            'concept_id': key,
            'label': group['label'],
            'aliases': aliases,
            'claims': list(claims.values()),
            'evidence_refs': refs,
            'section_ids': list(dict.fromkeys(evidence[e]['section_id'] for e in refs)),
            'source_pages': sorted({evidence[e]['page'] for e in refs}),
        })
    order = {e: i for i, e in enumerate(evidence)}
    concepts.sort(key=lambda c: (min(order[e] for e in c['evidence_refs']), c['concept_id']))
    original_relations = {r['relation_id']: r for r in document['relations']}
    relations, seen = [], set()
    for proposed in projection['relations']:
        row = deepcopy(original_relations[proposed['original_relation_id']])
        row.update(
            source_concept_id=remap[proposed['source_concept_id']],
            target_concept_id=remap[proposed['target_concept_id']],
            type=proposed['type'],
            learner_reason=proposed['reason'],
            inference_basis=RELATION_BASIS[proposed['type']],
        )
        key = (frozenset((row['source_concept_id'], row['target_concept_id'])), row['type'])
        if key in seen:
            continue
        seen.add(key)
        row['relation_id'] = _id('relation', {k: v for k, v in row.items() if k != 'relation_id'})
        relations.append(row)
    relations.sort(key=lambda relation: (
        RELATION_PRIORITY[relation['type']],
        relation['source_concept_id'],
        relation['target_concept_id'],
        relation['relation_id'],
    ))
    result.update(
        concepts=concepts,
        relations=relations,
        initial_learning_path=_path(concepts, relations),
    )
    for key in sorted(rejected_claim_edits):
        projection['blocked_changes'].append({'claim_id': key, 'reason': 'CANONICAL_LITERAL_GUARD'})
    projection['claim_changes'] = [
        edit for edit in projection['claim_changes']
        if edit['original_claim_id'] not in rejected_claim_edits
        and edit['original_claim_id'] in claim_remap
    ]
    for section in result['document_tree']['sections']:
        section['concept_ids'] = [
            concept['concept_id'] for concept in concepts
            if concept['section_ids'][0] == section['section_id']
        ]
    if projection['findings'] or projection['blocked_changes']:
        result['source_review_required'] = True
        reasons = result['status']['reason_codes']
        if 'SOURCE_REVIEW_SUGGESTED' not in reasons:
            reasons.append('SOURCE_REVIEW_SUGGESTED')
        result['status'].update(processing='partial', quality='needs_review', decision='review')
    result['revision'] = _revision(result)
    if not validate_knowledge_structure(result):
        raise ReviewError('REVIEW_STRUCTURE_INVALID')
    projection['applied_revision'] = result['revision']
    projection['concept_mapping'] = remap
    projection['claim_mapping'] = claim_remap
    return result, projection
