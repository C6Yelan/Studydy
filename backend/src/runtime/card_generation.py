"""可續接的卡面精簡工作；每次模型呼叫後保存，不持有資料列鎖等待模型。"""

from copy import deepcopy
from datetime import UTC, datetime
import json
import logging
import time
from threading import Event, Thread
from uuid import uuid4

from sqlalchemy import or_, select

from . import card_compaction as compact
from .generation_jobs import WORKER_LEASE_TTL, cancellable_job
from .semantic_service import _luna_token_count
from .storage.knowledge_structures import _read_verified_document, _view
from .storage.tables import CardSet, Material, database_session

BATCH_SOURCE_TOKENS = 4500


def initial(results=None):
    return {'policy': compact.POLICY, 'stage': 'preparing', 'results': results or {},
            'calls': 0, 'estimated_input_tokens': 0, 'saved_output_tokens': 0,
            'elapsed_seconds': 0, 'error_code': None}


def progress(row):
    state = row.generation or {}
    results = state.get('results', {})
    return {'status': row.status, 'stage': state.get('stage', 'complete'),
            'completed': sum(identity in results for identity in row.concept_ids),
            'total': len(row.concept_ids), 'error_code': state.get('error_code'),
            'retained_original': sum(item.get('rejected', 0) for item in results.values()),
            'cached': sum(bool(item.get('cached')) for item in results.values())}


def _claim(dsn):
    now = datetime.now(UTC)
    with database_session(dsn) as db:
        row = db.scalar(select(CardSet).join(Material, Material.material_id == CardSet.material_id).where(
            CardSet.deleted_at.is_(None), Material.discard_requested_at.is_(None),
            or_(CardSet.status == 'pending', (CardSet.status == 'running') & (CardSet.lease_expires_at < now)),
        ).order_by(CardSet.created_at).with_for_update(of=CardSet, skip_locked=True).limit(1))
        if row is None:
            return None
        row.status = 'running'
        row.lease_token = uuid4()
        row.lease_expires_at = now + WORKER_LEASE_TTL
        return {'id': row.card_set_id, 'owner': row.learner_id, 'material_id': row.material_id,
                'revision': row.knowledge_structure_revision, 'concept_ids': list(row.concept_ids),
                'token': row.lease_token, 'state': deepcopy(row.generation)}


def _save(claim, state, status, dsn):
    with database_session(dsn) as db:
        row = db.scalar(select(CardSet).where(CardSet.card_set_id == claim['id']).with_for_update())
        material = db.get(Material, claim['material_id'])
        if (row is None or row.deleted_at is not None or row.status != 'running' or row.lease_token != claim['token']
                or row.lease_expires_at is None or row.lease_expires_at <= datetime.now(UTC)
                or material is None or material.discard_requested_at is not None):
            return
        row.generation = state
        row.status = status
        row.lease_token = None
        row.lease_expires_at = None


def _prepare(claim, state, dsn):
    with database_session(dsn) as db:
        # 快取只在同一擁有者、教材、不可變版本與文字政策之間重用。
        cached = db.scalars(select(CardSet.generation).where(
            CardSet.learner_id == claim['owner'], CardSet.material_id == claim['material_id'],
            CardSet.knowledge_structure_revision == claim['revision'], CardSet.status == 'ready',
            CardSet.deleted_at.is_(None), CardSet.card_set_id != claim['id'],
        )).all()
        for old in cached:
            if old.get('policy') != compact.POLICY:
                continue
            for identity, result in old.get('results', {}).items():
                if identity in claim['concept_ids'] and identity not in state['results']:
                    state['results'][identity] = {**result, 'cached': True}
        remaining = [identity for identity in claim['concept_ids'] if identity not in state['results']]
        if not remaining:
            state['stage'] = 'complete'
            return
        document = _view(_read_verified_document(db, claim['owner'], claim['material_id'],
                         revision=claim['revision']), claim['material_id'])
    # 同教材、版本及政策下，完全相同的原文沿用已覆核的結果。
    accepted, rejected = {}, {}
    for result in state['results'].values():
        for edit in result['edits']:
            accepted.setdefault(edit['source'], edit)
        for rejection in result['review_rejections']:
            rejected.setdefault(rejection['source'], rejection)
    concepts = {item['concept_id']: item for item in document['concepts']}
    current = {'label': concepts[remaining[0]]['label'], 'concept_sources': {},
               'source_labels': {}, 'sources': [], 'offset': 0,
               'edits': [], 'review_rejections': [], 'format_rejections': []}
    for identity in remaining:
        sources = compact.source_texts(document, identity)
        labels = deepcopy(current['source_labels'])
        pending = list(current['sources'])
        for source in sources:
            names = labels.setdefault(source, [])
            if concepts[identity]['label'] not in names:
                names.append(concepts[identity]['label'])
            if source not in accepted and source not in rejected and source not in pending:
                pending.append(source)
        items = [{'index': i, 'labels': labels[source], 'text': source}
                 for i, source in enumerate(pending)]
        if current['concept_sources'] and _luna_token_count(json.dumps(items, ensure_ascii=False)) > BATCH_SOURCE_TOKENS:
            break
        current['concept_sources'][identity] = sources
        current['source_labels'] = labels
        current['sources'] = pending
    selected = current['source_labels']
    current['edits'] = [edit for source, edit in accepted.items() if source in selected]
    current['review_rejections'] = [value for source, value in rejected.items()
                                    if source in selected and source not in accepted]
    state['current'] = current
    state['stage'] = 'compacting'


def _request(claim, state, prompt, payload, schema, dsn, call):
    estimate = _luna_token_count(prompt + '\nINPUT:\n' + json.dumps(payload, ensure_ascii=False))
    estimate += _luna_token_count(json.dumps(schema, ensure_ascii=False))
    if estimate > 12000:
        raise compact.CompactionError('CARD_CONTEXT_TOO_LARGE')
    state['calls'] += 1
    state['estimated_input_tokens'] += estimate
    started = time.monotonic()
    try:
        with cancellable_job(CardSet, claim['id'], claim['token'], dsn=dsn) as metadata:
            result = call('/semantics', {'task': 'concept_card', 'prompt': prompt,
                                        'request': payload, 'schema': schema, **metadata})
    finally:
        state['elapsed_seconds'] = round(state['elapsed_seconds'] + time.monotonic() - started, 3)
    state['saved_output_tokens'] += _luna_token_count(json.dumps(result, ensure_ascii=False))
    return result


def _advance(claim, state, dsn, call):
    if state['stage'] == 'preparing':
        _prepare(claim, state, dsn)
        return
    current = state['current']
    if state['stage'] == 'compacting':
        remaining = current['sources'][current['offset']:]
        if not remaining:
            for identity, sources in current['concept_sources'].items():
                rejected = [item for item in current['review_rejections'] if item['source'] in sources]
                state['results'][identity] = {
                    'edits': [item for item in current['edits'] if item['source'] in sources],
                    'rejected': len(rejected) + sum(source in sources for source in current['format_rejections']),
                    'cached': False, 'review_rejections': rejected,
                }
            del state['current']
            state['stage'] = 'preparing'
            return
        batch = []
        for source in remaining:
            items = [{'index': i, 'labels': current['source_labels'][text], 'text': text}
                     for i, text in enumerate([*batch, source])]
            if _luna_token_count(json.dumps(items, ensure_ascii=False)) > BATCH_SOURCE_TOKENS:
                break
            batch.append(source)
        if not batch:
            # 單項太長時保留原文並如實記錄，不截斷來源。
            current['offset'] += 1
            current['format_rejections'].append(remaining[0])
            return
        payload = {'items': [{'index': i, 'labels': current['source_labels'][s], 'text': s}
                             for i, s in enumerate(batch)]}
        result = _request(claim, state, compact.COMPACT_PROMPT, payload, compact.EDIT_SCHEMA, dsn, call)
        candidates = compact.validate_edits(result, batch)
        current['format_rejections'].extend(item['source'] for item in candidates if item['rejected'])
        current['pending'] = [{'source': item['source'], 'text': item['text']}
                              for item in candidates if item['source'] != item['text']]
        current['batch_size'] = len(batch)
        state['stage'] = 'checking'
    elif state['stage'] == 'checking':
        candidates = current['pending']
        if candidates:
            payload = {'items': [{'index': i, 'labels': current['source_labels'][item['source']], **item}
                                 for i, item in enumerate(candidates)]}
            result = _request(claim, state, compact.REVIEW_PROMPT, payload, compact.REVIEW_SCHEMA, dsn, call)
            edits, rejected = compact.validate_reviews(result, candidates)
            current['edits'].extend(edits)
            current['review_rejections'].extend(rejected)
        current['offset'] += current.pop('batch_size')
        del current['pending']
        state['stage'] = 'compacting'
    else:
        raise compact.CompactionError('CARD_TEXT_INVALID')


def step(*, dsn=None, call=None):
    from .voice import provider
    claim = _claim(dsn)
    if claim is None:
        return False
    state = deepcopy(claim['state'])
    if state.get('policy') != compact.POLICY:
        # 舊政策的中途結果不能混入新卡面；已發生的呼叫成本仍保留。
        state = {**initial(), **{key: state.get(key, 0) for key in (
            'calls', 'estimated_input_tokens', 'saved_output_tokens', 'elapsed_seconds')}}
    try:
        _advance(claim, state, dsn, call or provider)
        _save(claim, state, 'ready' if state['stage'] == 'complete' else 'pending', dsn)
    except Exception as error:
        code = str(error) if isinstance(error, compact.CompactionError) else 'CARD_GENERATION_FAILED'
        # 新狀態本身可能無法寫入（例如來源含 JSONB 不支援的字元）。
        # 沿用最後可保存的步驟，但保留已發生的請求用量，不能再寫同一份壞狀態。
        failed = deepcopy(claim['state'])
        for key in ('calls', 'estimated_input_tokens', 'saved_output_tokens', 'elapsed_seconds'):
            failed[key] = state.get(key, 0)
        failed['error_code'] = code
        _save(claim, failed, 'failed', dsn)
        logging.getLogger(__name__).warning('CARD_GENERATION_FAILED type=%s', type(error).__name__)
    return True


class CardWorker:
    def __init__(self, dsn):
        self.dsn = dsn
        self.stop_event = Event()
        self.thread = Thread(target=self.loop, name='studydy-cards', daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=2)

    def loop(self):
        while not self.stop_event.is_set():
            try:
                if not step(dsn=self.dsn):
                    self.stop_event.wait(2)
            except Exception:
                logging.getLogger(__name__).warning('CARD_WORKER_FAILED')
                self.stop_event.wait(2)
