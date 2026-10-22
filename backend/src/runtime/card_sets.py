"""保存卡組選材；固定版本的 KS 仍是概念及重點的唯一來源。"""

from .storage.materials import (
    require_material,
    validate_concept_selection,
    MaterialLibraryError,
)

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from content_identity import canonical_sha256
from .storage.artifacts import _key_digest
from .storage.knowledge_structures import (
    _prune_unreferenced_structures,
    _read_verified_document,
    _view,
)
from .storage.tables import CardSet, Material
from .storage.database import database_session
from .storage import card_sources, knowledge_reads
from . import card_generation


class CardSetError(MaterialLibraryError):
    pass


def _summary(row, material, moved_to=None):
    return {
        "card_set_id": row.card_set_id,
        "material_id": row.material_id,
        "material_name": material.display_name,
        "knowledge_structure_revision": row.knowledge_structure_revision,
        "name": row.name,
        "card_count": row.card_count,
        "generation": card_generation.progress(row),
        "version": row.version,
        "created_at": row.created_at,
        "is_current_revision": row.knowledge_structure_revision
        == material.head_revision,
        # 學生已把這個舊版卡組移到新版時，指向仍存在的新版卡組。
        "moved_to_card_set_id": moved_to,
    }


def create_card_set(owner, material_id, revision, name, concept_ids, key, *, dsn=None):
    name = validate_concept_selection(name, concept_ids)
    digest = _key_digest(key)
    fingerprint = bytes.fromhex(
        canonical_sha256(
            {
                "revision": revision,
                "name": name,
                "concept_ids": concept_ids,
            }
        )
    )
    with database_session(dsn) as session:
        # 與發布／prune／教材刪除使用同一父資料鎖，避免選材讀完後來源被清理。
        material = require_material(session, owner, material_id)
        existing = session.scalar(
            select(CardSet).where(
                CardSet.learner_id == owner,
                CardSet.material_id == material_id,
                CardSet.idempotency_key_sha256 == digest,
            )
        )
        if existing:
            if bytes(existing.request_fingerprint) != fingerprint:
                raise CardSetError("IDEMPOTENCY_CONFLICT")
            if existing.deleted_at is not None:
                raise CardSetError("RESOURCE_NOT_FOUND")
            return _summary(existing, material)
        document = _read_verified_document(
            session, owner, material_id, revision=revision
        )
        known = {concept["concept_id"] for concept in document["concepts"]}
        if not set(concept_ids) <= known:
            raise CardSetError("REQUEST_INVALID")
        knowledge_reads.prepare_concept_index(
            session, owner, material_id, revision, document=document
        )
        row = CardSet(
            card_set_id=uuid4(),
            learner_id=owner,
            material_id=material_id,
            knowledge_structure_revision=revision,
            name=name,
            concept_ids=list(concept_ids),
            idempotency_key_sha256=digest,
            request_fingerprint=fingerprint,
            created_at=datetime.now(UTC),
            deleted_at=None,
            version=1,
            status="pending",
            generation=card_generation.initial(),
        )
        session.add(row)
        session.flush()
        return _summary(row, material)


def list_card_sets(owner, *, dsn=None):
    with database_session(dsn) as session:
        rows = session.execute(
            select(CardSet, Material)
            .join(
                Material,
                (Material.material_id == CardSet.material_id)
                & (Material.learner_id == CardSet.learner_id),
            )
            .where(
                CardSet.learner_id == owner,
                CardSet.deleted_at.is_(None),
                Material.discard_requested_at.is_(None),
            )
            .order_by(CardSet.created_at.desc(), CardSet.card_set_id)
        ).all()
        from .version_moves import moved_to

        targets = moved_to(session, owner, [row[0].card_set_id for row in rows])
        return {
            "schema": "card-set-list/v1",
            "card_sets": [
                _summary(*row, targets.get(row[0].card_set_id)) for row in rows
            ],
        }


def _owned_card(session, owner, card_set_id, *, read=False, allow_deleted=False):
    """先鎖教材再讀卡組；刪除收據只有刪除入口可重播。"""
    material_id = session.scalar(
        select(CardSet.material_id).where(
            CardSet.learner_id == owner,
            CardSet.card_set_id == card_set_id,
        )
    )
    if material_id is None:
        raise CardSetError("RESOURCE_NOT_FOUND")
    material = require_material(session, owner, material_id, read=read)
    row = session.get(CardSet, card_set_id)
    if row is None or (not allow_deleted and row.deleted_at is not None):
        raise CardSetError("RESOURCE_NOT_FOUND")
    return row, material


def read_card_set(owner, card_set_id, *, dsn=None):
    with database_session(dsn) as session:
        row, material = _owned_card(session, owner, card_set_id, read=True)
        material_id = material.material_id
        document = _read_verified_document(
            session,
            owner,
            material_id,
            revision=row.knowledge_structure_revision,
        )
        view = _view(document, material_id)
        concepts = {concept["concept_id"]: concept for concept in view["concepts"]}
        from .version_moves import moved_to

        target = moved_to(session, owner, [row.card_set_id]).get(row.card_set_id)
        return {
            "schema": "card-set/v1",
            **_summary(row, material, target),
            "source_resolver": view["source_resolver"],
            "status": view["status"],
            "excluded_pages": view["excluded_pages"],
            "display_edits": {
                identity: item.get("edits", [])
                for identity, item in row.generation.get("results", {}).items()
                if identity in row.concept_ids
            }
            if row.status == "ready"
            else {},
            "cards": [
                {
                    key: concepts[identity][key]
                    for key in ("concept_id", "label", "claims")
                }
                for identity in row.concept_ids
            ],
        }


def update_card_set(
    owner, card_set_id, name, expected_version, concept_ids=None, *, dsn=None
):
    with database_session(dsn) as session:
        row, material = _owned_card(session, owner, card_set_id)
        material_id = material.material_id
        selection = row.concept_ids if concept_ids is None else concept_ids
        name = validate_concept_selection(name, selection)
        document = _read_verified_document(
            session,
            owner,
            material_id,
            revision=row.knowledge_structure_revision,
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
            if row.concept_ids != ordered:
                row.card_count = None
                row.page_counts = {}
                results = {
                    identity: item
                    for identity, item in row.generation.get("results", {}).items()
                    if identity in ordered
                }
                row.generation = card_generation.initial(
                    results, previous=row.generation
                )
                row.status = "pending"
                row.lease_token = None
                row.lease_expires_at = None
            row.name = name
            row.concept_ids = ordered
            row.version += 1
            session.flush()
        return _summary(row, material)


def delete_card_set(owner, card_set_id, *, dsn=None):
    with database_session(dsn) as session:
        row, material = _owned_card(session, owner, card_set_id, allow_deleted=True)
        material_id = material.material_id
        if row.deleted_at is None:
            # 留下最小重送收據，舊建立請求不能在刪除後復活卡組；同時釋放版本引用。
            row.deleted_at = datetime.now(UTC)
            # 刪除時結束未完成的生成，收據不留在 pending／running。
            if row.status in ("pending", "running"):
                row.status = "failed"
            row.knowledge_structure_revision = None
            row.name = ""
            row.concept_ids = []
            row.card_count = None
            row.page_counts = {}
            row.generation = {}
            row.lease_token = None
            row.lease_expires_at = None
            session.flush()
            _prune_unreferenced_structures(
                session, owner, material_id, material.head_revision
            )
    return {"schema": "card-set-deleted/v1", "card_set_id": card_set_id}


def generation_status(owner, card_set_id, *, retry=False, dsn=None):
    with database_session(dsn) as session:
        row, _ = _owned_card(session, owner, card_set_id, read=not retry)
        if retry and row.status == "failed":
            row.generation = {**row.generation, "error_code": None}
            row.status = "pending"
        return card_generation.progress(row)


def save_card_count(
    owner, card_set_id, count, expected_version, *, concept_counts=None, dsn=None
):
    """保存擁有者瀏覽器的排版結果；舊選材或未完成精簡的張數不得寫入。"""
    with database_session(dsn) as session:
        row, material = _owned_card(session, owner, card_set_id)
        if row.version != expected_version or row.status != "ready":
            raise CardSetError("CARD_SET_CONFLICT")
        if concept_counts:
            if not set(concept_counts) <= set(row.concept_ids) or any(
                type(n) is not int or not 1 <= n <= 1_000_000
                for n in concept_counts.values()
            ):
                raise CardSetError("REQUEST_INVALID")
            merged = {**concept_counts, **row.page_counts}
            if sum(merged.values()) > 1_000_000:
                raise CardSetError("REQUEST_INVALID")
            row.page_counts = merged
            if len(merged) == len(row.concept_ids):
                row.card_count = sum(merged.values())
            return _summary(row, material)
        if type(count) is not int or not len(row.concept_ids) <= count <= 1_000_000:
            raise CardSetError("REQUEST_INVALID")
        # 同版本的第一份排版結果為準；重送不修改卡組版本，也不覆蓋已保存張數。
        if row.card_count is None:
            row.card_count = count
        return _summary(row, material)


def read_card_study(owner, card_set_id, *, offset=0, expected_version=None, dsn=None):
    with database_session(dsn) as session:
        row, material = _owned_card(session, owner, card_set_id)
        material_id = material.material_id
        if expected_version is not None and (
            row.version != expected_version or row.status != "ready"
        ):
            raise CardSetError("CARD_SET_CONFLICT")
        if not 0 <= offset < len(row.concept_ids) or offset % 8:
            raise CardSetError("REQUEST_INVALID")
        meta = knowledge_reads.source_metadata(
            session, owner, material_id, row.knowledge_structure_revision
        )
        if expected_version is not None:
            return card_sources.batch(session, row, meta, offset, 8)
        from .version_moves import moved_to

        target = moved_to(session, owner, [row.card_set_id]).get(row.card_set_id)
        return {
            "schema": "card-study/v1",
            **_summary(row, material, target),
            "source_resolver": meta["source_resolver"],
            "status": meta["status"],
            "navigation": card_sources.navigation(row, meta),
            "batch": card_sources.batch(session, row, meta, 0, 8)
            if row.status == "ready"
            else None,
        }
