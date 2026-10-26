"""補充研究的逐項取得與既有來源／revision 交接。"""

from .storage.materials import require_material
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4
import re
from sqlalchemy import and_, or_, select, func, case
from sqlalchemy.orm import object_session
from . import research_sources, luna_calls
from .source_normalization import upload_source
from .errors import SourceError
from .source_revisions import create_revision
from .storage.tables import (
    Material,
    MaterialResearch as Research,
    SourceNormalization,
    MaterialProcessingRun,
    TopicScope,
    KnowledgeStructure,
)
from .storage.database import database_session
from .provider_http import provider
from .generation_jobs import cancellable_job, lease_is_current
from . import research_workflow
from . import research_documents

ACTIVE = ("searching", "acquiring", "normalizing")


def _owned(db, owner, identity, *, read=False, allow_deleted=False):
    row = db.get(Research, identity)
    if not row or row.learner_id != owner:
        raise SourceError("RESOURCE_NOT_FOUND")
    material = (
        require_material(db, owner, row.material_id, read=read)
        if row.material_id
        else None
    )
    if not material:
        scope = db.scalar(
            select(TopicScope)
            .where(TopicScope.topic_id == row.topic_id, TopicScope.learner_id == owner)
            .with_for_update(read=read)
        )
        if not scope:
            raise SourceError("RESOURCE_NOT_FOUND")
    db.refresh(row)
    if row.deleted_at is not None and not allow_deleted:
        raise SourceError("RESOURCE_NOT_FOUND")
    return row, material


# 回應只送學習者畫面用到的欄位；搜尋管道、初篩、存取紀錄與內部計數留在資料庫供除錯。
CANDIDATE_FIELDS = (
    "id",
    "kind",
    "title",
    "authors",
    "year",
    "url",
    "license",
    "license_url",
    "state",
    "error_code",
)


def _candidate_view(item):
    value = {k: deepcopy(item[k]) for k in CANDIDATE_FIELDS if k in item}
    if item.get("acquisition"):
        # 引用需標示授權，保留授權全文與取得時間。
        value["acquisition"] = {
            k: item["acquisition"][k]
            for k in ("license_text", "acquired_at")
            if k in item["acquisition"]
        }
    if item.get("review"):
        value["review"] = {
            k: item["review"][k]
            for k in ("phase", "relevance", "reason", "scope")
            if k in item["review"]
        }
    return value


def _view(row):
    value = {
        k: deepcopy(getattr(row, k))
        for k in (
            "research_id",
            "material_id",
            "query",
            "status",
            "selection",
            "error_code",
            "run_id",
            "created_at",
        )
    }
    # 只送可選或已選的來源：未通過核對與重複的搜尋結果不在畫面上出現，也不再隨輪詢送出。
    candidates = []
    for item in row.candidates:
        eligible = (
            item.get("eligible", False)
            and research_workflow.source_allowed(item)
            and not (row.search_state and item.get("review", {}).get("phase") != "body")
        )
        selected = item["id"] in row.selection
        if not selected and (not eligible or item.get("duplicate_of")):
            continue
        candidates.append({**_candidate_view(item), "eligible": eligible})
    value["candidates"] = candidates
    value["progress"] = research_workflow.progress(
        row.search_state, row.candidates, running=row.status == "searching"
    )
    return value


def listing(owner, material_id, *, dsn=None):
    with database_session(dsn) as db:
        require_material(db, owner, material_id, read=True)
        # 任務卡只讀數量與分析狀態；不為列表載入候選全文或逐筆查詢 run。
        columns = (
            Research.research_id,
            Research.query,
            Research.status,
            Research.selection,
            Research.run_id,
            Research.created_at,
            func.jsonb_array_length(
                case(
                    (
                        func.jsonb_typeof(Research.candidates) == "array",
                        Research.candidates,
                    ),
                    else_=Research.candidates["items"],
                )
            ).label("candidate_count"),
            MaterialProcessingRun.status.label("run_status"),
        )
        rows = (
            db.execute(
                select(*columns)
                .outerjoin(
                    MaterialProcessingRun,
                    MaterialProcessingRun.run_id == Research.run_id,
                )
                .where(
                    Research.learner_id == owner,
                    Research.material_id == material_id,
                    Research.deleted_at.is_(None),
                )
                .order_by(Research.created_at.desc())
            )
            .mappings()
            .all()
        )
        return {"researches": [dict(row) for row in rows]}


def read(owner, identity, *, dsn=None, allow_deleted=False):
    with database_session(dsn) as db:
        row, material = _owned(
            db, owner, identity, read=True, allow_deleted=allow_deleted
        )
        value = _view(row)
        value["is_current_revision"] = (
            row.base_revision == material.head_revision if material else None
        )
        value["approved_scope"] = (
            deepcopy(db.get(TopicScope, row.topic_id).proposal)
            if row.topic_id and row.search_state.get("schema") == "material-research/v3"
            else None
        )
        if row.deleted_at is not None:
            value["deleted"] = True
        if row.run_id:
            run = db.get(MaterialProcessingRun, row.run_id)
            value["run"] = (
                {
                    "status": run.status,
                    "output_binding": {
                        "knowledge_structure_revision": run.output_binding.get(
                            "knowledge_structure_revision"
                        )
                    }
                    if run.output_binding
                    else None,
                }
                if run
                else None
            )
        return value


def delete(owner, identity, *, dsn=None):
    with database_session(dsn) as db:
        row, _ = _owned(db, owner, identity, allow_deleted=True)
        if row.material_id is None:
            raise SourceError("REQUEST_INVALID")
        if row.deleted_at is None:
            row.deleted_at = datetime.now(UTC)
            if row.status != "submitted":
                row.status = "cancelled"
            # 舊 worker 不得再發布；候選與 canonical source_id 留下供來源回查。
            row.lease_token = row.lease_expires_at = None
            research_documents.discard(db, row.research_id)
        result = {"research_id": row.research_id, "status": "deleted"}
    luna_calls.discard("research", identity, owner)
    return result


def action(owner, identity, action, selected=None, *, dsn=None):
    with database_session(dsn) as db:
        row, material = _owned(db, owner, identity)
        if row.topic_id and db.get(TopicScope, row.topic_id).status != "approved":
            raise SourceError("TOPIC_CONFLICT")
        if (
            not row.topic_id
            or (row.search_state or {}).get("schema") != "material-research/v3"
        ) and action == "retry" and not row.selection:
            raise SourceError("RESEARCH_SCOPE_REQUIRED")
        if action == "acquire":
            if not row.material_id:
                raise SourceError("REQUEST_INVALID")
            if row.status == "acquiring" and row.selection == selected:
                return _view(row)
            if row.status not in ("selecting", "ready", "failed"):
                raise SourceError("REQUEST_INVALID")
            research_workflow.validate_selection(row.candidates, selected)
            row.selection = list(selected)
            candidates = deepcopy(row.candidates)
            for c in candidates:
                if c["id"] in selected and c["state"] == "failed":
                    c["state"] = "candidate"
                    c.pop("error_code", None)
            row.candidates = candidates
            row.status = "acquiring"
        elif action == "cancel":
            if row.status == "submitted":
                raise SourceError("REQUEST_INVALID")
            row.status = "cancelled"
            row.staged_content = None
            row.staged_metadata = None
        elif action == "retry":
            if row.status == "submitted":
                run = db.get(MaterialProcessingRun, row.run_id)
                if (
                    not run
                    or run.status not in ("succeeded", "partial")
                    or not any(
                        c["id"] in row.selection and c["state"] == "failed"
                        for c in row.candidates
                    )
                ):
                    raise SourceError("SOURCE_NOT_READY")
                # 部分成果已發布後，只把剩餘來源接到目前版本；原 run／原檔仍保留。
                row.base_revision = material.head_revision
                row.run_id = None
                row.status = "failed"
            if row.status not in ("failed", "cancelled"):
                raise SourceError("REQUEST_INVALID")
            row.search_state = {**row.search_state, "luna_attempt": str(uuid4())}
            if row.selection:
                candidates = deepcopy(row.candidates)
                for c in candidates:
                    if (
                        c["id"] in row.selection
                        and c["state"] == "failed"
                        and not c.get("source_id")
                    ):
                        c["state"] = "candidate"
                        c.pop("error_code", None)
                row.candidates = candidates
            row.status = "acquiring" if row.selection else "searching"
        else:
            raise SourceError("REQUEST_INVALID")
        if action in ("retry", "acquire") and row.selection:
            candidates = deepcopy(row.candidates)
            for c in candidates:
                if (
                    c["id"] not in row.selection
                    or not c.get("source_id")
                    or c["state"] not in ("failed", "candidate")
                ):
                    continue
                job = db.scalar(
                    select(SourceNormalization)
                    .where(SourceNormalization.source_id == UUID(c["source_id"]))
                    .with_for_update()
                )
                if job and job.status == "failed":
                    job.status = "pending"
                    job.error_code = None
                    job.attempt = 0
                    job.updated_at = datetime.now(UTC)
                if job:
                    c["state"] = "normalizing"
                    c.pop("error_code", None)
            row.candidates = candidates
        row.error_code = None
        row.lease_token = row.lease_expires_at = None
        result = _view(row)
    if action in {"cancel", "retry"}:
        luna_calls.discard("research", identity, owner)
    return result


def submit(owner, identity, config, *, dsn=None, attempt_token=None):
    with database_session(dsn) as db:
        row, _ = _owned(db, owner, identity)
        keep_snapshots = [
            c["_document_snapshot"]["sha256"]
            for c in row.candidates
            if c["id"] in row.selection
            and c["state"] == "failed"
            and not c.get("source_id")
            and c.get("_document_snapshot")
        ]
        if row.status == "submitted":
            research_documents.discard(db, identity, keep_sha256=keep_snapshots)
            return _view(row)
        if row.status != "ready":
            raise SourceError("SOURCE_NOT_READY")
        candidates = {c["id"]: c for c in row.candidates}
        items = [candidates[i] for i in row.selection]
        if any(c["state"] not in ("ready", "failed") for c in items):
            raise SourceError("SOURCE_NOT_READY")
        base = (
            db.scalar(
                select(KnowledgeStructure).where(
                    KnowledgeStructure.learner_id == owner,
                    KnowledgeStructure.material_id == row.material_id,
                    KnowledgeStructure.structure_revision == row.base_revision,
                )
            )
            if row.base_revision
            else None
        )
        included = (
            {
                item["source_id"]
                for item in base.document["input_binding"]["manifest"]["items"]
            }
            if base
            else set()
        )
        items = [
            c
            for c in items
            if c["state"] == "ready" and c.get("source_id") not in included
        ]
        if not items:
            raise SourceError("SOURCE_NOT_READY")
        material_id, base_revision = row.material_id, row.base_revision
        normalizations = list(dict.fromkeys(UUID(c["normalization_id"]) for c in items))
    from content_identity import canonical_sha256

    create_revision(
        owner,
        material_id,
        normalizations,
        "research:" + str(identity) + ":" + canonical_sha256(base_revision),
        config,
        base_revision=base_revision,
        dsn=dsn,
        research_id=identity,
        research_token=attempt_token,
    )
    with database_session(dsn) as db:
        research_documents.discard(db, identity, keep_sha256=keep_snapshots)
    luna_calls.discard("research", identity, owner, outcome="succeeded")
    return read(owner, identity, dsn=dsn)


def claim(*, dsn=None):
    with database_session(dsn) as db:
        now = datetime.now(UTC)
        row = db.scalar(
            select(Research)
            .outerjoin(Material, Material.material_id == Research.material_id)
            .outerjoin(TopicScope, TopicScope.topic_id == Research.topic_id)
            .where(
                or_(
                    Research.status.in_(ACTIVE),
                    and_(
                        Research.status == "ready",
                        Research.topic_id.is_not(None),
                        TopicScope.material_id.is_(None),
                    ),
                ),
                or_(Research.topic_id.is_(None), TopicScope.status == "approved"),
                or_(
                    Research.material_id.is_(None),
                    and_(
                        Material.material_id.is_not(None),
                        Material.discard_requested_at.is_(None),
                    ),
                ),
                or_(
                    Research.lease_expires_at.is_(None), Research.lease_expires_at < now
                ),
            )
            .order_by(Research.lease_expires_at.asc().nullsfirst(), Research.created_at)
            .with_for_update(of=Research, skip_locked=True)
            .limit(1)
        )
        if not row:
            return None
        if "luna_attempt" not in row.search_state:
            row.search_state = {**row.search_state, "luna_attempt": str(uuid4())}
        row.lease_token = uuid4()
        row.lease_expires_at = now + timedelta(minutes=30)
        return {
            **{
                k: deepcopy(getattr(row, k))
                for k in (
                    "research_id",
                    "material_id",
                    "base_revision",
                    "query",
                    "mode",
                    "status",
                    "selection",
                    "search_query",
                    "cursor",
                    "error_code",
                    "run_id",
                    "created_at",
                    "topic_id",
                )
            },
            "owner": row.learner_id,
            "lease_token": row.lease_token,
            "candidates": deepcopy(row.candidates),
            "material_title": db.get(Material, row.material_id).display_name
            if row.material_id
            else db.get(TopicScope, row.topic_id).proposal["title"],
            "approved_scope": deepcopy(db.get(TopicScope, row.topic_id).proposal)
            if row.topic_id
            else None,
            "search_state": deepcopy(row.search_state),
            "staged_content": bytes(row.staged_content) if row.staged_content else None,
            "staged_metadata": deepcopy(row.staged_metadata),
        }


def save(state, mutate, *, dsn=None, release=True):
    with database_session(dsn) as db:
        if state["material_id"]:
            material = db.scalar(
                select(Material)
                .where(Material.material_id == state["material_id"])
                .with_for_update()
            )
            if not material or material.discard_requested_at:
                return False
        else:
            topic = db.scalar(
                select(TopicScope)
                .where(TopicScope.topic_id == state["topic_id"])
                .with_for_update()
            )
            if not topic or topic.status != "approved":
                return False
        row = db.scalar(
            select(Research)
            .where(Research.research_id == state["research_id"])
            .with_for_update()
        )
        if (
            not row
            or not lease_is_current(row, state["lease_token"])
            or row.status != state["status"]
        ):
            return False
        mutate(row)
        if release:
            # 已輪詢的工作排到後面，轉檔中的舊研究不阻塞新搜尋。
            row.lease_token = None
            waiting = (
                (row.search_state or {}).get("waiting_until")
                if row.status == "searching"
                else None
            )
            row.lease_expires_at = (
                datetime.fromtimestamp(waiting, UTC) if waiting else datetime.now(UTC)
            )
        outcome = row.status
        error_code = row.error_code
    if outcome in {"submitted", "failed", "cancelled"}:
        luna_calls.completed("failed" if outcome == "failed" else "cancelled" if outcome == "cancelled" else "succeeded", error_code)
    return True


def _advance_search(state, job, *, dsn):
    """讀取本次需求的既有脈絡，推進搜尋並將正文快照與進度一起保存。"""
    if not state["approved_scope"]:
        raise SourceError("RESEARCH_SCOPE_REQUIRED")
    if (
        state["material_id"]
        and (state["search_state"] or {}).get("stage", "planning") == "planning"
    ):
        from .retrieval import search

        with database_session(dsn) as db:
            passages = search(
                db,
                state["owner"],
                state["material_id"],
                state["base_revision"],
                [state["query"]],
                neighbors=False,
            )
            # 只送與本次需求相關的概念線索，不列出整份教材的數千概念。
            state["existing_concepts"] = list(
                dict.fromkeys(hint for p in passages for hint in p["hints"])
            )[:80]

    def existing(queries):
        from .retrieval import search

        with database_session(dsn) as db:
            return search(
                db,
                state["owner"],
                state["material_id"],
                state["base_revision"],
                queries,
                limit=8,
            )

    result = research_workflow.advance(
        state,
        lambda path, body: provider(path, {**job, **body}),
        read_document=lambda receipt, digest: research_documents.read(
            state["research_id"], receipt, digest, dsn=dsn
        ),
        retrieve_existing=existing
        if state["material_id"] and state["base_revision"]
        else None,
    )

    def progressed(row):
        research_documents.persist(
            object_session(row), row.research_id, result["candidates"]
        )
        for key, value in result.items():
            setattr(row, key, value)

    save(state, progressed, dsn=dsn)


def _acquire_next_source(state, *, dsn):
    """先保存取得的原檔，再交接來源正規化；不重做已封存的下載。"""
    items = [c for c in state["candidates"] if c["id"] in state["selection"]]
    candidate = next((c for c in items if c["state"] == "candidate"), None)
    if candidate is None:
        save(state, lambda r: setattr(r, "status", "normalizing"), dsn=dsn)
        return True
    try:
        if not research_workflow.source_allowed(candidate):
            raise SourceError("RESEARCH_SOURCE_TYPE_UNCONFIRMED")
        metadata = state["staged_metadata"]
        if state["staged_content"] and metadata and metadata["id"] == candidate["id"]:
            data = state["staged_content"]
            media = metadata["media"]
            suffix = metadata["suffix"]
        else:
            if candidate.get("_document_snapshot"):
                data, media, suffix, metadata = research_documents.read(
                    state["research_id"],
                    candidate["_document_snapshot"],
                    candidate.get("inspection_sha256"),
                    dsn=dsn,
                )
            else:
                data, media, suffix, metadata = research_sources.acquire(candidate)
            metadata.update(
                id=candidate["id"],
                media=media,
                suffix=suffix,
                acquired_at=metadata.get("acquired_at")
                or datetime.now(UTC).isoformat(),
            )

            def stage(row):
                row.staged_content = data
                row.staged_metadata = metadata

            if not save(state, stage, dsn=dsn, release=False):
                return True
        name = (
            "補充｜" + re.sub(r"[\x00-\x1f/\\]", " ", candidate["title"])[:150] + suffix
        )
        from pdf_evidence.analysis_scope import requested_scope

        try:
            selection_scope = requested_scope(data, candidate["review"]["quotes"])
        except (ValueError, KeyError) as error:
            raise SourceError("SOURCE_SCOPE_EMPTY") from error
        source_id = upload_source(
            state["owner"],
            state["material_id"],
            data,
            name,
            media,
            "research:" + str(state["research_id"]) + ":" + candidate["id"],
            dsn=dsn,
            research_attempt=(state["research_id"], state["lease_token"]),
            analysis_scope=selection_scope,
        )
        candidate.update(
            state="normalizing", source_id=str(source_id), acquisition=metadata
        )
    except Exception as error:
        candidate.update(
            state="failed",
            error_code=str(error)
            if isinstance(error, SourceError)
            else "RESEARCH_DOWNLOAD_FAILED",
        )

    def acquired(row):
        row.candidates = state["candidates"]
        row.staged_content = None
        row.staged_metadata = None

    save(state, acquired, dsn=dsn)
    return True


def _synchronize_normalization(state, *, dsn):
    """沿來源工作的實際結果推進研究，不把部分失敗當作整批成功。"""
    items = [c for c in state["candidates"] if c["id"] in state["selection"]]
    previous_states = [c["state"] for c in items]
    with database_session(dsn) as db:
        for c in items:
            if c["state"] != "normalizing":
                continue
            job = db.scalar(
                select(SourceNormalization)
                .where(SourceNormalization.source_id == UUID(c["source_id"]))
                .order_by(SourceNormalization.created_at.desc())
            )
            if job and job.status == "ready":
                c.update(state="ready", normalization_id=str(job.normalization_id))
            elif job and job.status in ("failed", "cancelled"):
                c.update(
                    state="failed",
                    error_code=job.error_code or "RESEARCH_NORMALIZATION_FAILED",
                )

    def normalized(row):
        row.candidates = state["candidates"]
        if all(c["state"] in ("ready", "failed") for c in items):
            row.status = (
                "ready" if any(c["state"] == "ready" for c in items) else "failed"
            )
            row.error_code = (
                "RESEARCH_ITEMS_FAILED"
                if any(c["state"] == "failed" for c in items)
                else None
            )

    save(state, normalized, dsn=dsn)
    return previous_states != [c["state"] for c in items] or all(
        c["state"] in ("ready", "failed") for c in items
    )


def step(*, dsn=None, config=None):
    state = claim(dsn=dsn)
    if not state:
        return False
    trace = luna_calls.start(
        "research", state["research_id"], state["search_state"]["luna_attempt"],
        state["material_id"], state["owner"],
    )
    try:
        with cancellable_job(
            Research,
            state["research_id"],
            state["lease_token"],
            dsn=dsn,
            statuses=(state["status"],),
        ) as job:
            if state["status"] == "searching":
                _advance_search(state, job, dsn=dsn)
            elif state["status"] == "ready":
                if config is None:
                    raise SourceError("RESEARCH_CONFIGURATION_INVALID")
                submit(
                    state["owner"],
                    state["research_id"],
                    config,
                    dsn=dsn,
                    attempt_token=state["lease_token"],
                )
            elif state["status"] == "acquiring":
                return _acquire_next_source(state, dsn=dsn)
            else:
                return _synchronize_normalization(state, dsn=dsn)
    except Exception as error:
        error_code = (
            str(error) if isinstance(error, SourceError) else "RESEARCH_SEARCH_FAILED"
        )
        luna_calls.rejected(str(error))

        def failed(row):
            row.status = "failed"
            row.error_code = error_code

        save(state, failed, dsn=dsn)
    finally:
        luna_calls.close(trace)
    return True
