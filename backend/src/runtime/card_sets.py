"""使用者選材與續讀位置；不保存知識副本，也不更新學習權威。"""
from datetime import UTC, datetime
from hashlib import sha256
import json
from uuid import uuid4
from unicodedata import category

from sqlalchemy import delete, select

from knowledge_map.concept_cards import project_concept_cards
from .storage.knowledge_structures import _read_verified_document, _view
from .storage.tables import CardSet, CardSetItem, Material, database_session


class CardSetError(RuntimeError):
    pass


def _material(session, owner, material_id, *, write=False):
    # 與發布／prune／教材刪除使用相同父資料鎖，避免檢查引用後才出現新卡組。
    row = session.scalar(select(Material).where(
        Material.learner_id == owner, Material.material_id == material_id,
        Material.discard_requested_at.is_(None),
    ).with_for_update(read=not write))
    if row is None:
        raise CardSetError("RESOURCE_NOT_FOUND")


def _row(session, owner, material_id, card_set_id):
    row = session.scalar(select(CardSet).where(
        CardSet.learner_id == owner, CardSet.material_id == material_id,
        CardSet.card_set_id == card_set_id,
    ))
    if row is None:
        raise CardSetError("RESOURCE_NOT_FOUND")
    return row


def _ids(session, row):
    return list(session.scalars(select(CardSetItem.concept_id).where(
        CardSetItem.card_set_id == row.card_set_id,
    ).order_by(CardSetItem.position)))


def _public(session, row):
    return {
        "schema": "card-set/v1", "card_set_id": str(row.card_set_id),
        "material_id": str(row.material_id),
        "knowledge_structure_revision": row.knowledge_structure_revision,
        "name": row.name, "ordering_policy": row.ordering_policy,
        "concept_ids": _ids(session, row), "current_position": row.current_position,
        "version": row.version, "created_at": row.created_at, "updated_at": row.updated_at,
    }


def _selection(document, name, concept_ids, policy):
    if (not isinstance(name, str) or not 1 <= len(name.strip()) <= 200
            or any(category(c) in {"Cc", "Cs"} for c in name) or policy != "published_order"
            or not isinstance(concept_ids, list) or not concept_ids
            or not all(isinstance(id, str) for id in concept_ids)):
        raise CardSetError("REQUEST_INVALID")
    selected = set(concept_ids)
    ordered = [c["concept_id"] for c in document["concepts"] if c["concept_id"] in selected]
    if len(ordered) != len(selected):
        raise CardSetError("REQUEST_INVALID")
    return name.strip(), ordered


def _replace_items(session, row, ids):
    session.execute(delete(CardSetItem).where(CardSetItem.card_set_id == row.card_set_id))
    session.add_all(CardSetItem(card_set_id=row.card_set_id, position=i, concept_id=id)
                    for i, id in enumerate(ids))
    session.flush()


def list_card_sets(owner, material_id, *, dsn=None):
    with database_session(dsn) as session:
        _material(session, owner, material_id)
        rows = session.scalars(select(CardSet).where(
            CardSet.learner_id == owner, CardSet.material_id == material_id,
        ).order_by(CardSet.updated_at.desc(), CardSet.card_set_id)).all()
        return {"schema": "card-set-list/v1", "material_id": str(material_id),
                "card_sets": [_public(session, row) for row in rows]}


def create_card_set(owner, material_id, revision, name, concept_ids, policy, key, *, dsn=None):
    if not isinstance(key, str) or not 1 <= len(key.encode()) <= 256:
        raise CardSetError("REQUEST_INVALID")
    digest = sha256(key.encode()).digest()
    with database_session(dsn) as session:
        _material(session, owner, material_id, write=True)
        document = _read_verified_document(session, owner, material_id, revision=revision)
        name, ids = _selection(document, name, concept_ids, policy)
        fingerprint = sha256(json.dumps([revision, name, ids, policy], ensure_ascii=False).encode()).digest()
        existing = session.scalar(select(CardSet).where(
            CardSet.learner_id == owner, CardSet.material_id == material_id,
            CardSet.idempotency_key_sha256 == digest,
        ))
        if existing:
            if bytes(existing.request_fingerprint) != fingerprint:
                raise CardSetError("IDEMPOTENCY_CONFLICT")
            return _public(session, existing)
        now = datetime.now(UTC)
        row = CardSet(card_set_id=uuid4(), learner_id=owner, material_id=material_id,
            knowledge_structure_revision=revision, name=name, ordering_policy=policy,
            current_position=0, version=1, idempotency_key_sha256=digest,
            request_fingerprint=fingerprint, created_at=now, updated_at=now)
        session.add(row); session.flush()
        _replace_items(session, row, ids)
        return _public(session, row)


def read_card_set(owner, material_id, card_set_id, *, cards=False, dsn=None):
    with database_session(dsn) as session:
        _material(session, owner, material_id)
        row = _row(session, owner, material_id, card_set_id)
        saved = _public(session, row)
        if not cards:
            return saved
        document = _read_verified_document(session, owner, material_id, revision=row.knowledge_structure_revision)
        projection = project_concept_cards(_view(document, material_id), str(material_id),
                                          saved["concept_ids"], document["evidence"])
        return {"schema": "card-set-cards/v1", "card_set": saved, "cards": projection}


def edit_card_set(owner, material_id, card_set_id, revision, name, concept_ids, policy, expected_version, *, dsn=None):
    with database_session(dsn) as session:
        _material(session, owner, material_id, write=True)
        row = _row(session, owner, material_id, card_set_id)
        if row.knowledge_structure_revision != revision:
            raise CardSetError("REVISION_CONFLICT")
        document = _read_verified_document(session, owner, material_id, revision=revision)
        name, ids = _selection(document, name, concept_ids, policy)
        old_ids = _ids(session, row)
        # 回應遺失後重送相同目標可直接接回；不同目標不能覆蓋較新版本。
        if (row.name, old_ids, row.ordering_policy) == (name, ids, policy):
            return _public(session, row)
        if row.version != expected_version:
            raise CardSetError("CARD_SET_CONFLICT")
        row.name = name
        if ids != old_ids:
            _replace_items(session, row, ids)
            row.current_position = 0
        row.version += 1; row.updated_at = datetime.now(UTC)
        session.flush()
        return _public(session, row)


def set_card_position(owner, material_id, card_set_id, position, expected_version, *, dsn=None):
    with database_session(dsn) as session:
        _material(session, owner, material_id, write=True)
        row = _row(session, owner, material_id, card_set_id)
        if type(position) is not int or not 0 <= position < len(_ids(session, row)):
            raise CardSetError("REQUEST_INVALID")
        # 即使位置相同也先驗版本，避免舊頁在內容編輯後誤認新 sequence。
        if row.version != expected_version:
            raise CardSetError("CARD_SET_CONFLICT")
        if row.current_position != position:
            row.current_position = position
            row.version += 1; row.updated_at = datetime.now(UTC)
            session.flush()
        return _public(session, row)


def delete_card_set(owner, material_id, card_set_id, expected_version, *, dsn=None):
    with database_session(dsn) as session:
        _material(session, owner, material_id, write=True)
        row = _row(session, owner, material_id, card_set_id)
        if row.version != expected_version:
            raise CardSetError("CARD_SET_CONFLICT")
        session.delete(row)
        # 移除引用後，舊 KS 在下一次正常 prune 才重新具備清理資格。
