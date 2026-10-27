"""教材更新後安全時自動承接；有損失時保留預覽與手動確認。

只保留最近一次移動前的入口快照，原始作答／標記及必要引用不改寫。
Podcast 維持建立時的內容，不承接。
"""

from collections import OrderedDict, defaultdict
from datetime import UTC, datetime
from hashlib import sha256
from threading import Lock
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select, update, delete
from sqlalchemy.dialects.postgresql import insert

from content_identity import canonical_sha256
from knowledge_map.source_identity import map_concepts
from learning_adaptation.learner_progress import LearnerProgressError, derive_learner_progress
from learning_adaptation.study_sessions import _fingerprint as study_fingerprint
from . import card_design as design
from . import card_generation
from .card_sets import CardSetError, discard_card_contents
from .storage import card_sources, knowledge_reads
from .storage.artifacts import _key_digest
from .storage.database import database_session
from .errors import SourceError
from .storage.knowledge_structures import KnowledgeStructureStoreError, _read_verified_document
from .storage.materials import require_material
from .storage.tables import (
    CardReviewState,
    CardReviewEvent,
    AssessmentSet,
    Assessment,
    AnswerEvent,
    MaterialVersionState,
    Material,
    MaterialProcessingRun,
    CardSet,
    CardSetMove,
    KnowledgeStructure,
    MaterialVersionMove,
    Podcast,
    StudySession,
    StudySessionMove,
)


class VersionMoveError(CardSetError):
    pass


def _derived_key(key, item):
    """同一次承接內各新資料列的冪等鍵；重送時由 move 紀錄直接回傳，不會再建立。"""
    return sha256(f"version-move:{key}:{item}".encode()).digest()


def _live_moved_sources(session, owner, material_id):
    """已移到新版的舊卡組；新版卡組被刪除後，舊卡組可以再移一次。"""
    target = CardSet.__table__.alias("target")
    return set(
        session.scalars(
            select(CardSetMove.source_card_set_id)
            .join(target, target.c.card_set_id == CardSetMove.target_card_set_id)
            .where(
                CardSetMove.learner_id == owner,
                target.c.material_id == material_id,
                target.c.deleted_at.is_(None),
            )
        )
    )


def _card_payload(session, owner, material_id, revision, concept_id, meta):
    """與卡片生成相同的單一概念來源輸入（card_generation._inputs）。"""
    row = SimpleNamespace(
        learner_id=owner,
        material_id=material_id,
        knowledge_structure_revision=revision,
        concept_ids=[concept_id],
        card_set_id=None,
        version=0,
        status="pending",
        generation={},
    )
    card_batch = card_sources.batch(session, row, meta, 0, 1)
    return design.source_input(card_batch["cards"][0], card_batch["context"])


def _reusable_designs(session, row, revision, concept_map):
    """來源輸入完全相同（文字、原文節錄、相鄰關係）的卡面直接沿用，不再呼叫模型。

    新版的 Claim／Relation／Evidence 身分不同，依同一套來源輸入的順序對應換成新版 ID。
    """
    state = row.generation or {}
    results = state.get("results", {})
    if state.get("policy") != design.POLICY:
        return {}
    provider = card_generation.provider_identity(card_generation.configured_lock())
    old_meta = knowledge_reads.source_metadata(
        session, row.learner_id, row.material_id, row.knowledge_structure_revision
    )
    new_meta = knowledge_reads.source_metadata(
        session, row.learner_id, row.material_id, revision
    )
    reused = {}
    for new_concept, old_concept in concept_map.items():
        result = results.get(old_concept)
        if not result or not result.get("design") or result["design"].get("provider") != provider:
            continue
        old_payload, old_bindings = _card_payload(
            session, row.learner_id, row.material_id,
            row.knowledge_structure_revision, old_concept, old_meta,
        )
        new_payload, new_bindings = _card_payload(
            session, row.learner_id, row.material_id, revision, new_concept, new_meta,
        )
        if canonical_sha256(old_payload) != canonical_sha256(new_payload) or len(
            old_bindings
        ) != len(new_bindings):
            continue
        maps = {key: {} for key in ("claim_ids", "relation_ids", "evidence_ids")}
        consistent = True
        for old, new in zip(old_bindings, new_bindings):
            for key, mapping in maps.items():
                if len(old[key]) != len(new[key]):
                    consistent = False
                for before, after in zip(old[key], new[key]):
                    if mapping.setdefault(before, after) != after:
                        consistent = False
        if not consistent:
            continue
        try:
            checked = design.checked_design(result["design"])

            def remap(item):
                return {
                    **item,
                    **{key: [maps[key][value] for value in item[key]] for key in maps},
                }

            moved = {
                **{key: value for key, value in checked.items() if key != "revision"},
                "pages": [
                    {
                        **page,
                        "takeaway": remap(page["takeaway"]),
                        "blocks": [remap(block) for block in page["blocks"]],
                        "links": [remap(link) for link in page["links"]],
                    }
                    for page in checked["pages"]
                ],
                "details_claim_ids": [
                    maps["claim_ids"][value] for value in checked["details_claim_ids"]
                ],
            }
        except (KeyError, design.CardDesignError):
            continue
        moved["revision"] = "card-design:sha256:" + canonical_sha256(moved)
        reused[new_concept] = {
            **{key: value for key, value in result.items() if key != "edits"},
            "design": moved,
            "source_digest": canonical_sha256(new_payload),
            "cached": True,
        }
    return reused


def _completed(learner, study, dsn):
    """以與地圖／測驗頁相同的進度推導讀取已完成概念（含先前承接的完成）。"""
    progress = derive_learner_progress(learner, study.study_session_id, dsn=dsn)
    return {s.concept_id for s in progress.concept_states if s.status == "completed"}


# 知識結構版本以內容雜湊命名、發布後不再改變；預覽每次重讀並驗證新舊兩份完整結構，
# 千頁教材需數秒並占住 API。第一次完整驗證後，在本程序保留只由結構推導的名稱、順序與
# 概念對應（不含任何學習紀錄），鍵含使用者、教材與版本；卡組、標記與進度仍每次即時讀取。
_DERIVED_LIMIT = 16
_derived = OrderedDict()
_derived_lock = Lock()


def _remembered(key, compute, enabled):
    if enabled:
        with _derived_lock:
            if key in _derived:
                _derived.move_to_end(key)
                return _derived[key]
    value = compute()
    if enabled:
        with _derived_lock:
            _derived[key] = value
            while len(_derived) > _DERIVED_LIMIT:
                _derived.popitem(last=False)
    return value


def _structure_facts(document):
    return {
        "labels": {c["concept_id"]: c["label"] for c in document["concepts"]},
        "order": [c["concept_id"] for c in document["concepts"]],
        "first_step": next((step["concept_id"] for step in document["initial_learning_path"]), None),
    }


def _empty(material_id, head):
    return {
        "schema": "version-move-preview/v2",
        "material_id": material_id,
        "to_revision": head,
        "preview_digest": None,
        "card_sets": [],
        "study": None,
        "kept_podcasts": 0,
        "automatic_safe": head is not None,
        "blocking_reasons": [],
        "notice_dismissed": False,
        "previous": None,
        "automatic_move": None,
    }


def _plan(session, learner, material, *, dsn, lock=False):
    owner = learner.learner_id
    material_id = material.material_id
    head = material.head_revision
    if head is None:
        return _empty(material_id, None), None
    moved = _live_moved_sources(session, owner, material_id)
    blockers = defaultdict(int)
    unavailable = list(session.scalars(select(CardSet.card_set_id).where(
        CardSet.learner_id==owner, CardSet.material_id==material_id, CardSet.deleted_at.is_(None),
        (CardSet.knowledge_structure_revision!=head)|(CardSet.knowledge_structure_revision.is_(None)),
        (CardSet.status!="ready")|(CardSet.knowledge_structure_revision.is_(None)))))
    blockers["source_unavailable"] += len(set(unavailable)-moved)
    blockers["assessment_in_progress"] += len(list(session.scalars(select(AssessmentSet.set_id).where(
        AssessmentSet.learner_id==owner, AssessmentSet.material_id==material_id,
        AssessmentSet.status.in_(("preparing","partial_ready","ready","in_progress"))))))
    card_rows = [
        row
        for row in session.scalars(
            select(CardSet)
            .where(
                CardSet.learner_id == owner,
                CardSet.material_id == material_id,
                CardSet.deleted_at.is_(None),
                CardSet.status == "ready",
                CardSet.knowledge_structure_revision.is_not(None),
                CardSet.knowledge_structure_revision != head,
            )
            .order_by(CardSet.created_at, CardSet.card_set_id)
        )
        if row.card_set_id not in moved
    ]
    head_study = session.scalar(
        select(StudySession).where(
            StudySession.learner_id == owner,
            StudySession.material_id == material_id,
            StudySession.knowledge_structure_revision == head,
        )
    )
    source_study = None
    if head_study is None or session.get(StudySessionMove, head_study.study_session_id) is None:
        # 最近一個舊版的學習；新版本的學習只承接一次。
        source_study = session.scalar(
            select(StudySession)
            .join(
                KnowledgeStructure,
                (KnowledgeStructure.learner_id == StudySession.learner_id)
                & (KnowledgeStructure.material_id == StudySession.material_id)
                & (KnowledgeStructure.structure_revision == StudySession.knowledge_structure_revision),
            )
            .where(
                StudySession.learner_id == owner,
                StudySession.material_id == material_id,
                StudySession.knowledge_structure_revision != head,
            )
            .order_by(KnowledgeStructure.created_at.desc(), StudySession.started_at.desc())
            .limit(1)
        )
    if not card_rows and source_study is None:
        preview = _empty(material_id, head)
        preview["blocking_reasons"] = [{"code":code,"count":count} for code,count in blockers.items() if count]
        preview["automatic_safe"] = not preview["blocking_reasons"]
        return preview, None

    documents = {}

    def document(revision):
        if revision not in documents:
            documents[revision] = _read_verified_document(
                session, owner, material_id, revision=revision, lock=lock and revision == head
            )
        return documents[revision]

    # 預覽沿用已驗證結構推導出的名稱、順序與概念對應；實際承接（lock）一律重新讀取驗證。
    remember = not lock

    def facts(revision):
        return _remembered(("facts", owner, material_id, revision), lambda: _structure_facts(document(revision)), remember)

    def mapping(revision):
        return _remembered(
            ("mapping", owner, material_id, revision, head),
            lambda: map_concepts(document(revision), document(head)),
            remember,
        )

    head_facts = facts(head)
    new_labels, new_order = head_facts["labels"], head_facts["order"]

    def old_labels(revision):
        return facts(revision)["labels"]

    card_items, internal_cards = [], {}
    for row in card_rows:
        revision = row.knowledge_structure_revision
        try:
            found, labels = mapping(revision), old_labels(revision)
        except (KnowledgeStructureStoreError, SourceError):
            # 舊版本無法核對時仍可手動處理其他項目，但不能宣稱全部安全。
            blockers["source_unavailable"] += 1
            continue
        targets, sources_for, mapped, unmapped = [], defaultdict(list), [], []
        for concept in row.concept_ids:
            info = found.get(concept)
            if info and info["to"]:
                mapped.append({
                    "from_concept_id": concept, "from_label": labels[concept],
                    "to_concept_id": info["to"], "to_label": new_labels[info["to"]],
                    "basis": info["basis"],
                })
                sources_for[info["to"]].append(concept)
                if info["to"] not in targets:
                    targets.append(info["to"])
            else:
                unmapped.append({
                    "concept_id": concept, "label": labels.get(concept, ""),
                    "reason": info["reason"] if info else "not_found",
                })
        blockers["unmapped_cards"] += len(unmapped) + len(mapped) - len(targets)
        states = {
            state.concept_id: state
            for state in session.scalars(
                select(CardReviewState).where(CardReviewState.card_set_id == row.card_set_id)
            )
        }
        concept_map = {}
        for target in targets:
            olds = sources_for[target]
            # 新版合併多張舊卡時，沿用最近一次的標記（學生最新的自我評估）。
            marked = [old for old in olds if old in states]
            concept_map[target] = (
                max(marked, key=lambda old: (states[old].last_reviewed_at, -olds.index(old)))
                if marked else olds[0]
            )
        try:
            reused = _reusable_designs(session, row, head, concept_map) if targets else {}
        except KnowledgeStructureStoreError:
            reused = {}
        card_items.append({
            "card_set_id": row.card_set_id,
            "name": row.name,
            "from_revision": revision,
            "concept_count": len(row.concept_ids),
            "target_count": len(targets),
            "carried_reviews": sum(concept_map[t] in states for t in targets),
            "reused_designs": len(reused),
            "movable": bool(targets),
            "mapped": mapped,
            "unmapped": unmapped,
        })
        internal_cards[row.card_set_id] = {
            "row": row, "targets": targets, "concept_map": concept_map,
            "states": states, "reused": reused,
        }

    study_item = internal_study = None
    if source_study is not None:
        revision = source_study.knowledge_structure_revision
        try:
            found, labels = mapping(revision), old_labels(revision)
            completed = _completed(learner, source_study, dsn)
            already = _completed(learner, head_study, dsn) if head_study is not None else set()
        except (LearnerProgressError, KnowledgeStructureStoreError, SourceError):
            # 舊版進度無法核對時只略過學習進度，卡組仍可承接；不推測完成狀態。
            blockers["source_unavailable"] += 1
            source_study = None
    if source_study is not None:
        by_target = defaultdict(list)
        for concept, info in found.items():
            if info["to"]:
                by_target[info["to"]].append(concept)
        # 新概念對應的所有舊概念都已完成才承接完成；只完成一部分的合併概念仍需在新版學習。
        carried = [
            target for target in new_order
            if by_target[target] and target not in already
            and all(old in completed for old in by_target[target])
        ]
        old_order = facts(revision)["order"]
        not_carried = []
        for concept in old_order:
            if concept not in completed:
                continue
            info = found.get(concept) or {"to": None, "reason": "not_found"}
            if info["to"] is None:
                not_carried.append({"concept_id": concept, "label": labels[concept], "reason": info["reason"]})
            elif info["to"] not in carried and info["to"] not in already:
                not_carried.append({"concept_id": concept, "label": labels[concept], "reason": "merged_unfinished"})
        blockers["unmapped_progress"] += len(not_carried)
        current = found.get(source_study.current_concept_id or "", {}).get("to")
        offered = bool(carried) or (head_study is None and source_study.last_event_number > 0)
        if offered:
            study_item = {
                "study_session_id": source_study.study_session_id,
                "from_revision": revision,
                "target_study_session_id": head_study.study_session_id if head_study else None,
                "completed_count": len(completed),
                "carried": [
                    {
                        "concept_id": target, "label": new_labels[target],
                        "from_labels": [labels[old] for old in by_target[target]],
                    }
                    for target in carried
                ],
                "not_carried": not_carried,
                "current_concept": {"concept_id": current, "label": new_labels[current]} if current else None,
            }
            internal_study = {
                "source": source_study, "head": head_study, "carried": carried,
                "current": current or head_facts["first_step"],
            }

    kept_podcasts = len(session.scalars(
        select(Podcast.podcast_id).where(
            Podcast.learner_id == owner,
            Podcast.material_id == material_id,
            Podcast.knowledge_structure_revision.is_not(None),
            Podcast.knowledge_structure_revision != head,
        )
    ).all())
    digest = None
    if card_items or study_item:
        digest = "version-move:sha256:" + canonical_sha256({
            "to_revision": head,
            "card_sets": [
                [str(item["card_set_id"]), internal_cards[item["card_set_id"]]["row"].version,
                 internal_cards[item["card_set_id"]]["concept_map"],
                 sorted(internal_cards[item["card_set_id"]]["reused"])]
                for item in card_items
            ],
            "study": [
                str(internal_study["source"].study_session_id),
                internal_study["source"].last_event_number,
                str(internal_study["head"].study_session_id) if internal_study["head"] else None,
                internal_study["carried"],
            ] if internal_study else None,
        })
    preview = {
        "schema": "version-move-preview/v2",
        "material_id": material_id,
        "to_revision": head,
        "preview_digest": digest,
        "card_sets": card_items,
        "study": study_item,
        "kept_podcasts": kept_podcasts,
        "automatic_safe": not any(blockers.values()),
        "blocking_reasons": [{"code":code,"count":count} for code,count in blockers.items() if count],
        "notice_dismissed": False,
        "previous": None,
        "automatic_move": None,
    }
    return preview, {
        "cards": internal_cards, "study": internal_study,
        "head_document": document(head) if lock else None,
    }


def preview_version_move(learner, material_id, *, dsn=None):
    with database_session(dsn) as session:
        material = require_material(session, learner.learner_id, material_id, read=True)
        preview, _ = _plan(session, learner, material, dsn=dsn)
        state = session.get(MaterialVersionState,(learner.learner_id,material_id))
        if state:
            preview.update(notice_dismissed=state.notice_dismissed, previous=state.previous,
                automatic_move={"status":state.automatic_status,"move_id":state.automatic_move_id,"error_code":state.automatic_error} if state.automatic_status else None)
        return preview


def execute_version_move(
    learner, material_id, to_revision, card_set_ids, include_study, preview_digest, key, *, automatic=False, dsn=None
):
    owner = learner.learner_id
    digest = _key_digest(key)
    fingerprint = bytes.fromhex(canonical_sha256({
        "material_id": str(material_id),
        "to_revision": to_revision,
        "card_set_ids": sorted(str(identity) for identity in card_set_ids),
        "include_study": include_study,
        "preview_digest": preview_digest,
    }))
    if len(set(card_set_ids)) != len(card_set_ids) or not (card_set_ids or include_study):
        raise VersionMoveError("REQUEST_INVALID")
    with database_session(dsn) as session:
        # 與卡組建立、刪除、教材發布及清除使用同一把教材鎖；同一個鍵的並行重送在此排隊。
        material = require_material(session, owner, material_id)
        existing = session.scalar(
            select(MaterialVersionMove).where(
                MaterialVersionMove.learner_id == owner,
                MaterialVersionMove.idempotency_key_sha256 == digest,
            )
        )
        if existing is not None:
            if bytes(existing.request_fingerprint) != fingerprint:
                raise VersionMoveError("IDEMPOTENCY_CONFLICT")
            return existing.result
        if material.head_revision != to_revision:
            raise VersionMoveError("VERSION_MOVE_STALE")
        preview, plan = _plan(session, learner, material, dsn=dsn, lock=True)
        if preview["preview_digest"] is None or preview["preview_digest"] != preview_digest:
            raise VersionMoveError("VERSION_MOVE_STALE")
        if automatic and not preview["automatic_safe"]:
            raise VersionMoveError("VERSION_MOVE_STALE")
        movable = {item["card_set_id"] for item in preview["card_sets"] if item["movable"]}
        if not set(card_set_ids) <= movable or (include_study and plan["study"] is None):
            raise VersionMoveError("REQUEST_INVALID")
        now = datetime.now(UTC)
        move = MaterialVersionMove(
            move_id=uuid4(), learner_id=owner, material_id=material_id,
            to_revision=to_revision, idempotency_key_sha256=digest,
            request_fingerprint=fingerprint, result={}, created_at=now,
        )
        session.add(move)
        session.flush()
        knowledge_reads.prepare_concept_index(
            session, owner, material_id, to_revision, document=plan["head_document"]
        )
        moved_sets = []
        for item in preview["card_sets"]:
            if item["card_set_id"] not in card_set_ids:
                continue
            card = plan["cards"][item["card_set_id"]]
            source, targets = card["row"], card["targets"]
            generation = card_generation.initial(card["reused"])
            complete = all(target in generation["results"] for target in targets)
            row = CardSet(
                card_set_id=uuid4(), learner_id=owner, material_id=material_id,
                knowledge_structure_revision=to_revision, name=source.name,
                concept_ids=list(targets),
                idempotency_key_sha256=_derived_key(key, source.card_set_id),
                request_fingerprint=bytes.fromhex(canonical_sha256({
                    "moved_from": str(source.card_set_id), "revision": to_revision,
                    "concept_ids": targets,
                })),
                created_at=now, deleted_at=None, version=1,
                status="ready" if complete else "pending",
                generation={**generation, "stage": "complete"} if complete else generation,
            )
            if complete:
                row.page_counts = {
                    target: len(generation["results"][target]["design"]["pages"])
                    for target in targets
                }
                row.card_count = sum(row.page_counts.values())
            session.add(row)
            session.flush()
            for target in targets:
                state = card["states"].get(card["concept_map"][target])
                if state is not None:
                    session.add(CardReviewState(
                        card_set_id=row.card_set_id, concept_id=target, learner_id=owner,
                        review_count=state.review_count, last_rating=state.last_rating,
                        last_reviewed_at=state.last_reviewed_at,
                    ))
            session.add(CardSetMove(
                target_card_set_id=row.card_set_id, source_card_set_id=source.card_set_id,
                move_id=move.move_id, learner_id=owner, concept_map=card["concept_map"],
            ))
            moved_sets.append({
                "from_card_set_id": str(source.card_set_id),
                "card_set_id": str(row.card_set_id),
                "status": row.status,
            })
        study_session_id = None
        if include_study:
            study = plan["study"]
            target = study["head"]
            if target is None:
                target = StudySession(
                    study_session_id=uuid4(), learner_id=owner, material_id=material_id,
                    knowledge_structure_revision=to_revision,
                    current_concept_id=study["current"], no_safe_claim_ids=[],
                    deferred_concept_ids=[], last_applied_guidance_revision=None,
                    last_applied_progress_sha256=None, status="active",
                    idempotency_key_sha256=_derived_key(key, "study"),
                    request_fingerprint=study_fingerprint(material_id, to_revision, study["current"]),
                    started_at=now, completed_at=None, last_event_number=0,
                )
                session.add(target)
                session.flush()
            session.add(StudySessionMove(
                target_study_session_id=target.study_session_id,
                source_study_session_id=study["source"].study_session_id,
                move_id=move.move_id, learner_id=owner,
                completed_concept_ids=list(study["carried"]),
            ))
            study_session_id = str(target.study_session_id)
        result = {
            "schema": "version-move/v1",
            "move_id": str(move.move_id),
            "material_id": str(material_id),
            "to_revision": to_revision,
            "card_sets": moved_sets,
            "study_session_id": study_session_id,
            "created_at": now.isoformat(),
        }
        move.result = result
        state = session.get(MaterialVersionState,(owner,material_id))
        if state is None:
            prior = session.scalar(select(MaterialProcessingRun.base_revision).join(KnowledgeStructure,
                KnowledgeStructure.run_id==MaterialProcessingRun.run_id).where(KnowledgeStructure.learner_id==owner,
                KnowledgeStructure.material_id==material_id,KnowledgeStructure.structure_revision==to_revision))
            state = MaterialVersionState(learner_id=owner,material_id=material_id,target_revision=to_revision,prior_revision=prior)
            session.add(state)
        state.previous = {"move_id":str(move.move_id),"revision":state.prior_revision,
            "card_set_ids":[str(identity) for identity in card_set_ids],
            "study_session_id":str(plan["study"]["source"].study_session_id) if include_study else None}
        state.automatic_status = "completed" if automatic else None
        state.automatic_move_id = move.move_id if automatic else None
        state.automatic_error = None
        session.flush()
        _clean_unreferenced_copies(session,owner,material_id,to_revision,state.previous)
        return result


def _clean_unreferenced_copies(session, owner, material_id, head, previous):
    """只清較早且沒有原始事件／移版鏈引用的副本，原始入口與事件保留。"""
    from .storage.knowledge_structures import _prune_unreferenced_structures
    previous_cards = set(previous['card_set_ids'])
    card_ids = list(session.scalars(select(CardSet.card_set_id).join(
        CardSetMove,CardSetMove.target_card_set_id==CardSet.card_set_id).where(
        CardSet.learner_id==owner,CardSet.material_id==material_id,
        CardSet.knowledge_structure_revision!=head,CardSet.status.notin_(("pending","running")))))
    for identity in card_ids:
        if str(identity) in previous_cards:
            continue
        if session.scalar(select(CardReviewEvent.review_event_id).where(CardReviewEvent.card_set_id==identity).limit(1)):
            continue
        if session.scalar(select(CardSetMove.target_card_set_id).where(CardSetMove.source_card_set_id==identity).limit(1)):
            continue
        discard_card_contents(session.get(CardSet,identity))
    studies = list(session.scalars(select(StudySession.study_session_id).join(
        StudySessionMove,StudySessionMove.target_study_session_id==StudySession.study_session_id).where(
        StudySession.learner_id==owner,StudySession.material_id==material_id,
        StudySession.knowledge_structure_revision!=head)))
    for identity in studies:
        if str(identity)==previous['study_session_id']:
            continue
        if session.scalar(select(StudySessionMove.target_study_session_id).where(StudySessionMove.source_study_session_id==identity).limit(1)):
            continue
        if any(session.scalar(select(model.study_session_id).where(model.study_session_id==identity).limit(1))
               for model in (AnswerEvent,Assessment,AssessmentSet)):
            continue
        session.delete(session.get(StudySession,identity))
    session.flush()
    _prune_unreferenced_structures(session,owner,material_id,head)


def set_notice(learner, material_id, dismissed, *, dsn=None):
    with database_session(dsn) as session:
        require_material(session,learner.learner_id,material_id)
        session.execute(insert(MaterialVersionState).values(learner_id=learner.learner_id,
            material_id=material_id,notice_dismissed=dismissed).on_conflict_do_update(
                index_elements=['learner_id','material_id'],set_={'notice_dismissed':dismissed}))
    return {'schema':'version-move-notice/v1','dismissed':dismissed}


def return_previous(learner, material_id, expected_move_id, *, dsn=None):
    with database_session(dsn) as session:
        require_material(session,learner.learner_id,material_id,read=True)
        state=session.get(MaterialVersionState,(learner.learner_id,material_id))
        if state is None or not state.previous:
            raise VersionMoveError('VERSION_MOVE_PREVIOUS_UNAVAILABLE')
        if state.previous['move_id']!=str(expected_move_id):
            raise VersionMoveError('VERSION_MOVE_PREVIEW_STALE')
        # 返回只提供原入口導航；教材 head 與移動後新增的作答／標記一律不動。
        return {'schema':'version-move-return/v1',**state.previous}


def enqueue_automatic(session, material, revision):
    session.execute(insert(MaterialVersionState).values(
        learner_id=material.learner_id,material_id=material.material_id,
        target_revision=revision,prior_revision=material.head_revision,
        automatic_status='pending' if material.head_revision else None,
    ).on_conflict_do_update(index_elements=['learner_id','material_id'],set_={
        'target_revision':revision,'prior_revision':material.head_revision,
        'automatic_status':'pending' if material.head_revision else None,
        'automatic_move_id':None,'automatic_error':None,
    }))


def run_next(*, dsn=None):
    from .learner_session import TrustedLearner
    with database_session(dsn) as session:
        row=session.execute(select(MaterialVersionState.learner_id,MaterialVersionState.material_id,
            MaterialVersionState.target_revision).join(Material,
                (Material.learner_id==MaterialVersionState.learner_id)&(Material.material_id==MaterialVersionState.material_id))
            .where(MaterialVersionState.automatic_status=='pending',Material.discard_requested_at.is_(None),
                   Material.head_revision==MaterialVersionState.target_revision)
            .order_by(MaterialVersionState.material_id).limit(1)).first()
    if row is None:
        return False
    learner=TrustedLearner(row.learner_id)
    status=error=move_id=None
    try:
        preview=preview_version_move(learner,row.material_id,dsn=dsn)
        if preview['automatic_safe']:
            cards=[item['card_set_id'] for item in preview['card_sets']]
            if cards or preview['study']:
                result=execute_version_move(learner,row.material_id,row.target_revision,cards,bool(preview['study']),
                    preview['preview_digest'],f'automatic:{row.material_id}:{row.target_revision}',automatic=True,dsn=dsn)
                move_id=result['move_id']
            status='completed'
    except (CardSetError,SourceError,KnowledgeStructureStoreError,LearnerProgressError) as failure:
        status,error='failed',str(failure)
    except Exception:
        status,error='failed','VERSION_MOVE_FAILED'
    with database_session(dsn) as session:
        session.execute(update(MaterialVersionState).where(MaterialVersionState.learner_id==row.learner_id,
            MaterialVersionState.material_id==row.material_id,MaterialVersionState.target_revision==row.target_revision,
            MaterialVersionState.automatic_status=='pending').values(automatic_status=status,
                automatic_move_id=move_id,automatic_error=error))
    return True
