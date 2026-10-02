"""保存卡組選材；固定版本的 KS 仍是概念及重點的唯一來源。"""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from pdf_evidence.ocr_page_evidence import canonical_sha256
from .storage.artifacts import _key_digest
from .storage.knowledge_structures import _prune_unreferenced_structures, _read_verified_document, _view
from .storage.tables import CardSet, Material, database_session


class CardSetError(RuntimeError):
    pass


def _material(session, owner, material_id, *, read=False):
    material = session.scalar(select(Material).where(
        Material.learner_id == owner, Material.material_id == material_id,
        Material.discard_requested_at.is_(None),
    ).with_for_update(read=read))
    if material is None:
        raise CardSetError("RESOURCE_NOT_FOUND")
    return material


def _summary(row, material):
    return {
        "card_set_id": row.card_set_id,
        "material_id": row.material_id,
        "material_name": material.display_name,
        "knowledge_structure_revision": row.knowledge_structure_revision,
        "name": row.name,
        "card_count": len(row.concept_ids),
        "version": row.version,
        "created_at": row.created_at,
        "is_current_revision": row.knowledge_structure_revision == material.head_revision,
    }


def _validate_selection(name, concept_ids):
    if (
        not isinstance(name, str) or not 1 <= len(name.strip()) <= 200
        or any(ord(char) < 32 or ord(char) == 127 for char in name)
        or not isinstance(concept_ids, list) or not concept_ids
        or any(not isinstance(value, str) for value in concept_ids)
        or len(set(concept_ids)) != len(concept_ids)
    ):
        raise CardSetError("REQUEST_INVALID")
    return name.strip()


def create_card_set(owner, material_id, revision, name, concept_ids, key, *, dsn=None):
    name = _validate_selection(name, concept_ids)
    digest = _key_digest(key)
    fingerprint = bytes.fromhex(canonical_sha256({
        "revision": revision, "name": name, "concept_ids": concept_ids,
    }))
    with database_session(dsn) as session:
        # 與發布／prune／教材刪除使用同一父資料鎖，避免選材讀完後來源被清理。
        material = _material(session, owner, material_id)
        existing = session.scalar(select(CardSet).where(
            CardSet.learner_id == owner, CardSet.material_id == material_id,
            CardSet.idempotency_key_sha256 == digest,
        ))
        if existing:
            if bytes(existing.request_fingerprint) != fingerprint:
                raise CardSetError("IDEMPOTENCY_CONFLICT")
            if existing.deleted_at is not None:
                raise CardSetError("RESOURCE_NOT_FOUND")
            return _summary(existing, material)
        document = _read_verified_document(session, owner, material_id, revision=revision)
        known = {concept["concept_id"] for concept in document["concepts"]}
        if not set(concept_ids) <= known:
            raise CardSetError("REQUEST_INVALID")
        row = CardSet(
            card_set_id=uuid4(), learner_id=owner, material_id=material_id,
            knowledge_structure_revision=revision, name=name,
            concept_ids=list(concept_ids),
            idempotency_key_sha256=digest, request_fingerprint=fingerprint,
            created_at=datetime.now(UTC), deleted_at=None,
            version=1,
        )
        session.add(row)
        session.flush()
        return _summary(row, material)


def list_card_sets(owner, *, dsn=None):
    with database_session(dsn) as session:
        rows = session.execute(select(CardSet, Material).join(
            Material, (Material.material_id == CardSet.material_id)
            & (Material.learner_id == CardSet.learner_id),
        ).where(
            CardSet.learner_id == owner, CardSet.deleted_at.is_(None),
            Material.discard_requested_at.is_(None),
        ).order_by(CardSet.created_at.desc(), CardSet.card_set_id)).all()
        return {"schema": "card-set-list/v1", "card_sets": [_summary(*row) for row in rows]}


def _set_material_id(session, owner, card_set_id):
    material_id = session.scalar(select(CardSet.material_id).where(
        CardSet.learner_id == owner, CardSet.card_set_id == card_set_id,
    ))
    if material_id is None:
        raise CardSetError("RESOURCE_NOT_FOUND")
    return material_id


def read_card_set(owner, card_set_id, *, dsn=None):
    with database_session(dsn) as session:
        material_id = _set_material_id(session, owner, card_set_id)
        material = _material(session, owner, material_id, read=True)
        row = session.get(CardSet, card_set_id)
        if row is None or row.deleted_at is not None:
            raise CardSetError("RESOURCE_NOT_FOUND")
        document = _read_verified_document(
            session, owner, material_id, revision=row.knowledge_structure_revision,
        )
        view = _view(document, material_id)
        concepts = {concept["concept_id"]: concept for concept in view["concepts"]}
        return {
            "schema": "card-set/v1", **_summary(row, material),
            "source_resolver": view["source_resolver"], "status": view["status"],
            "excluded_pages": view["excluded_pages"],
            "cards": [{key: concepts[identity][key] for key in ("concept_id", "label", "claims")}
                      for identity in row.concept_ids],
        }


def update_card_set(owner, card_set_id, name, expected_version, concept_ids=None, *, dsn=None):
    with database_session(dsn) as session:
        material_id = _set_material_id(session, owner, card_set_id)
        material = _material(session, owner, material_id)
        row = session.get(CardSet, card_set_id)
        if row is None or row.deleted_at is not None:
            raise CardSetError("RESOURCE_NOT_FOUND")
        selection = row.concept_ids if concept_ids is None else concept_ids
        name = _validate_selection(name, selection)
        document = _read_verified_document(
            session, owner, material_id, revision=row.knowledge_structure_revision,
        )
        known = {concept["concept_id"] for concept in document["concepts"]}
        if not set(selection) <= known:
            raise CardSetError("REQUEST_INVALID")
        # 保存已確認的選取順序，讓學習路徑推薦不被教材原始排列覆蓋。
        ordered = list(selection)
        unchanged = row.name == name and row.concept_ids == ordered
        # 回應遺失後可重送相同結果；其他舊版本編輯必須重新讀取。
        if row.version != expected_version:
            if row.version == expected_version + 1 and unchanged:
                return _summary(row, material)
            raise CardSetError("CARD_SET_CONFLICT")
        if not unchanged:
            row.name = name
            row.concept_ids = ordered
            row.version += 1
            session.flush()
        return _summary(row, material)


def delete_card_set(owner, card_set_id, *, dsn=None):
    with database_session(dsn) as session:
        material_id = _set_material_id(session, owner, card_set_id)
        material = _material(session, owner, material_id)
        row = session.get(CardSet, card_set_id)
        if row is None:
            raise CardSetError("RESOURCE_NOT_FOUND")
        if row.deleted_at is None:
            # 留下最小重送收據，舊建立請求不能在刪除後復活卡組；同時釋放版本引用。
            row.deleted_at = datetime.now(UTC)
            row.knowledge_structure_revision = None
            row.name = ""
            row.concept_ids = []
            session.flush()
            _prune_unreferenced_structures(session, owner, material_id, material.head_revision)
    return {"schema": "card-set-deleted/v1", "card_set_id": card_set_id}
