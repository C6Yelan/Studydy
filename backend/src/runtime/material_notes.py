"""每教材版本的兩種筆記、持久生成與不隨重生消失的個人註記。"""
from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4
import json
import logging

from pydantic import ValidationError
from sqlalchemy import select, or_, text, func
from sqlalchemy.dialects.postgresql import insert

from content_identity import canonical_sha256

from . import note_anchors, note_content, note_sections, luna_calls, luna_test
from .errors import SourceError
from .generation_jobs import JobCancelled, WORKER_LEASE_TTL, lease_is_current
from .local_ai_config import configured_lock
from .semantic_service import request_semantics, semantic_client, SemanticServiceError
from .storage.database import database_session
from .storage import knowledge_reads, knowledge_structures
from .storage.tables import Material, MaterialNotes, NoteAnnotation, KnowledgeStructure
from .text_budget import estimate_o200k_tokens, estimate_prompt_tokens

BATCH_TOKENS = 12000


def _material(db, owner, material_id, *, lock=False):
    statement = select(Material).where(
        Material.material_id == material_id,
        Material.learner_id == owner,
        Material.discard_requested_at.is_(None),
    )
    row = db.scalar(statement.with_for_update() if lock else statement)
    if row is None:
        raise SourceError("RESOURCE_NOT_FOUND")
    return row


def enqueue(db, owner, material_id, revision, kind="summary"):
    now = datetime.now(UTC)
    db.execute(insert(MaterialNotes).values(learner_id=owner, material_id=material_id,
        structure_revision=revision, kind=kind, status="pending", generation=0,
        runtime_lock=configured_lock(), calls=0, attempts=0, created_at=now, updated_at=now)
        .on_conflict_do_nothing())


def read(owner, material_id, revision, kind, *, dsn=None):
    with database_session(dsn) as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        db.info["read_snapshot"] = {}
        _material(db, owner, material_id)
        meta = knowledge_reads.metadata(db, owner, material_id, revision)
        row = db.get(MaterialNotes, (owner, material_id, revision, kind))
        structure = db.get(KnowledgeStructure, (owner, material_id, revision))
        content = row if row and row.generation > 0 and row.paragraphs is not None else None
        if content is None:
            content = db.scalar(select(MaterialNotes).join(KnowledgeStructure,
                (KnowledgeStructure.learner_id == MaterialNotes.learner_id)
                & (KnowledgeStructure.material_id == MaterialNotes.material_id)
                & (KnowledgeStructure.structure_revision == MaterialNotes.structure_revision))
                .where(MaterialNotes.learner_id == owner, MaterialNotes.material_id == material_id,
                       MaterialNotes.kind == kind, MaterialNotes.paragraphs.is_not(None),
                       MaterialNotes.generation > 0, KnowledgeStructure.created_at < structure.created_at)
                .order_by(KnowledgeStructure.created_at.desc(), MaterialNotes.updated_at.desc()).limit(1))
        content_revision = content.structure_revision if content else None
        content_meta = knowledge_reads.metadata(db, owner, material_id, content_revision) if content else meta
        paragraphs = deepcopy(content.paragraphs) if content else []
        published = content.published_metadata or {} if content else {}
        unorganized = published.get("unorganized_concepts", [])
        if kind == "summary":
            full = db.get(MaterialNotes, (owner, material_id, content_revision, "full")) if content else None
            full_missing = ((full.published_metadata or {}).get("unorganized_concepts", [])
                if full and full.generation > 0 and full.paragraphs is not None else [])
            missing_ids = {c["concept_id"] for c in full_missing}
            unorganized = [c for c in unorganized if c["concept_id"] in missing_ids]
        annotations = [{"annotation_id": str(a.annotation_id), "anchor": a.anchor,
            "body": a.body, "original_revision": a.structure_revision,
            "original_paragraph": a.paragraph_snapshot, "updated_at": a.updated_at.isoformat(),
            "block_path": a.block_path, "start": a.start_offset, "end": a.end_offset, "quote": a.quote, "_generation": a.generation}
            for a in db.scalars(select(NoteAnnotation).where(NoteAnnotation.learner_id == owner,
                NoteAnnotation.material_id == material_id, NoteAnnotation.kind == kind)
                .order_by(NoteAnnotation.created_at, NoteAnnotation.annotation_id))]
        return {"schema": "material-notes/v3",
            "content_revision": content_revision, "content_generation": content.generation if content else None,
            "update_available": content_revision is not None and content_revision != revision,
            "updating": bool(row and row.status in ("pending", "running")),
            "started_at": row.started_at if row else None, "updated_at": row.updated_at if row else None, "material_id": str(material_id),
            "knowledge_structure_revision": revision, "run_id": str(structure.run_id),
            "kind": kind, "source_status": content_meta["status"], "status": row.status if row else "not_generated",
            "generation": row.generation if row else 0, "paragraphs": paragraphs,
            "outline": note_content.outline(paragraphs),
            "sections": published.get("sections", []),
            "unorganized_concepts": unorganized,
            "annotations": note_content.annotation_matches(note_anchors.targets(paragraphs, published.get("sections", [])), annotations, content_revision,
                content.generation if content else None),
            "source_resolver": content_meta["source_resolver"], "error_code": row.error_code if row else None,
            "progress": section_progress(row) if row else None}


def generate(owner, material_id, revision, kind, expected_generation, *, dsn=None):
    with database_session(dsn) as db:
        _material(db, owner, material_id, lock=True)
        knowledge_reads.metadata(db, owner, material_id, revision)
        row = db.get(MaterialNotes, (owner, material_id, revision, kind), with_for_update=True)
        if row is None:
            if expected_generation != 0:
                raise SourceError("REVISION_CONFLICT")
            enqueue(db, owner, material_id, revision, kind)
        elif row.status in ("pending", "running"):
            # 同一生成按鈕／網路重送不排第二份工作。
            if expected_generation != row.generation:
                raise SourceError("REVISION_CONFLICT")
        elif expected_generation != row.generation:
            raise SourceError("REVISION_CONFLICT")
        else:
            current_lock = configured_lock()
            contract_changed = (
                row.runtime_lock.get("material_notes") != current_lock["material_notes"]
                or row.runtime_lock.get("semantic_service") != current_lock["semantic_service"]
                or row.runtime_lock.get("material_review", {}).get("generation")
                   != current_lock["material_review"]["generation"]
            )
            if row.status == "ready" or row.section_plan is None or contract_changed:
                # 只清除未發布的工作；不可把舊契約批次混入新請求，已發布內容與註記仍保留。
                row.runtime_lock = current_lock
                row.section_plan = row.section_results = row.progress_stage = None
            row.status, row.error_code, row.attempts = "pending", None, 0
            row.started_at = None
            row.updated_at = datetime.now(UTC)
    return read(owner, material_id, revision, kind, dsn=dsn)


def annotate(owner, material_id, revision, kind, annotation_id, body,
             paragraph_id, expected_generation, *, selection=None, dsn=None):
    with database_session(dsn) as db:
        _material(db, owner, material_id, lock=True)
        knowledge_reads.metadata(db, owner, material_id, revision)
        annotation = db.get(NoteAnnotation, annotation_id, with_for_update=True)
        if annotation is not None:
            if (annotation.learner_id, annotation.material_id, annotation.kind) != (owner, material_id, kind):
                raise SourceError("RESOURCE_NOT_FOUND")
            if body.strip():
                annotation.body, annotation.updated_at = body, datetime.now(UTC)
            else:
                db.delete(annotation)
        elif body.strip():
            row = db.get(MaterialNotes, (owner, material_id, revision, kind), with_for_update=True)
            if row is None or row.generation != expected_generation:
                raise SourceError("REVISION_CONFLICT")
            paragraph = next((p for p in note_anchors.targets(row.paragraphs or [], (row.published_metadata or {}).get("sections", [])) if p["id"] == paragraph_id), None)
            if paragraph is None:
                raise SourceError("REQUEST_INVALID")
            path, start, end, quote = selection or (None,) * 4
            if selection and not note_anchors.valid_range(paragraph, path, start, end, quote):
                raise SourceError("REQUEST_INVALID")
            now = datetime.now(UTC)
            db.add(NoteAnnotation(annotation_id=annotation_id, learner_id=owner,
                material_id=material_id, kind=kind, structure_revision=revision,
                anchor=paragraph["anchor"], paragraph_snapshot=deepcopy({k: v for k, v in paragraph.items() if k != "display_heading"}), body=body,
                block_path=path, start_offset=start, end_offset=end, quote=quote,
                generation=None if quote is None else row.generation, created_at=now, updated_at=now))
    return read(owner, material_id, revision, kind, dsn=dsn)


def queue_existing(*, owner, dsn=None):
    """部署後只補本人尚無複習速覽的目前版本；重跑不重生既有結果。"""
    queued = 0
    with database_session(dsn) as db:
        for m in db.scalars(select(Material).where(Material.learner_id == owner,
            Material.head_revision.is_not(None), Material.discard_requested_at.is_(None))):
            if db.get(MaterialNotes, (owner, m.material_id, m.head_revision, "summary")) is None:
                enqueue(db, owner, m.material_id, m.head_revision)
                queued += 1
    return queued


def _claim(dsn):
    now = datetime.now(UTC)
    with database_session(dsn) as db:
        row = db.scalar(select(MaterialNotes).join(Material,
            (Material.material_id == MaterialNotes.material_id) & (Material.learner_id == MaterialNotes.learner_id))
            .where(Material.discard_requested_at.is_(None), or_(MaterialNotes.status == "pending",
                (MaterialNotes.status == "running") & (MaterialNotes.lease_expires_at < now)))
            .order_by(MaterialNotes.created_at).with_for_update(of=MaterialNotes, skip_locked=True).limit(1))
        if row is None:
            return None
        if row.attempts >= 2:
            row.status, row.error_code = "failed", "NOTE_JOB_INTERRUPTED"
            row.generation_diagnostics = {"code": "NOTE_JOB_INTERRUPTED", "events": []}
            logging.getLogger(__name__).warning("NOTE_GENERATION_FAILED kind=%s diagnostics=%s",
                row.kind, json.dumps(row.generation_diagnostics, ensure_ascii=False))
            row.lease_token = row.lease_expires_at = None
            return {"skipped": True}
        row.status, row.lease_token = "running", uuid4()
        row.started_at = row.started_at or now
        row.lease_expires_at = now + WORKER_LEASE_TTL
        row.attempts += 1
        row.updated_at = now
        return {"key": (row.learner_id, row.material_id, row.structure_revision, row.kind),
                "generation": row.generation,
                "token": row.lease_token, "lock": deepcopy(row.runtime_lock),
                "plan": deepcopy(row.section_plan), "results": deepcopy(row.section_results or [])}


class WorkerStopping(JobCancelled):
    pass


def _check(claim, dsn, stop=None, *, count_call=False):
    if stop is not None and stop.is_set():
        raise WorkerStopping()
    with database_session(dsn) as db:
        # 同 API 的鎖序，阻擋刪除中教材的新發布與過期 worker。
        _material(db, *claim["key"][:2], lock=True)
        row = db.get(MaterialNotes, claim["key"], with_for_update=True)
        now = datetime.now(UTC)
        if row is None or row.status != "running" or not lease_is_current(row, claim["token"], now=now):
            raise JobCancelled()
        if row.lease_expires_at < now + WORKER_LEASE_TTL / 2:
            row.lease_expires_at = now + WORKER_LEASE_TTL
        if count_call:
            row.calls += 1


def _save(claim, dsn, *, paragraphs=None, metadata=None, error=None, stopping=False, diagnostics=None):
    with database_session(dsn) as db:
        material = db.scalar(select(Material).where(Material.material_id == claim["key"][1],
            Material.learner_id == claim["key"][0]).with_for_update())
        row = db.get(MaterialNotes, claim["key"], with_for_update=True)
        if material is None or material.discard_requested_at is not None or row is None or row.status != "running" or not lease_is_current(row, claim["token"], now=datetime.now(UTC)):
            return
        if paragraphs == []:
            old_content = db.scalar(select(MaterialNotes.material_id).where(
                MaterialNotes.learner_id == row.learner_id, MaterialNotes.material_id == row.material_id,
                MaterialNotes.kind == row.kind, MaterialNotes.generation > 0,
                func.jsonb_array_length(MaterialNotes.paragraphs) > 0).limit(1))
            if old_content is not None:
                paragraphs, error = None, "NOTE_NO_SUPPORTED_CONTENT"
                row.section_plan = row.section_results = None
                diagnostics = {"code": error, "events": (diagnostics or {}).get("events", [])}
        if paragraphs is not None:
            row.paragraphs = [{k: v for k, v in p.items() if k != "_explained_claim_ids"} for p in paragraphs]
            row.generation += 1
            row.published_metadata = metadata
            row.section_results = None
            row.progress_stage = "completed"
        row.status = "pending" if stopping else "failed" if error else "ready"
        row.error_code = error
        if stopping:
            row.attempts -= 1
        else:
            # 只保存最近一次有結果（發布或失敗）的摘要碼；重新生成時覆寫，沒有刪減的成功清為 NULL。
            row.generation_diagnostics = diagnostics
        row.lease_token = row.lease_expires_at = None
        row.updated_at = datetime.now(UTC)
    if not stopping:
        luna_calls.completed("failed" if error else "succeeded", error)


def _concepts(claim, dsn):
    owner, material, revision, _ = claim["key"]
    with database_session(dsn) as db:
        view = knowledge_reads.outline(db, owner, material, revision)
        path = knowledge_reads.learning_path(view)
        rows = knowledge_reads.read_concepts(db, owner, material, revision, path)
        by_id = {r.concept_id: r.payload["concept"] for r in rows}
        document = knowledge_structures._read_verified_document(db, owner, material, revision=revision)
        return note_content.with_page_context([by_id[c] for c in path], document)


def model_request(concepts, kind, previous_section=None, previous_paragraphs=()):
    """請求只帶一次原文；短編號在此請求內有效，發布前還原正式身分。
    previous_section 是程式選定的原教材章節標題，不代替 Evidence。"""
    groups, claims, evidence = {}, {}, {}
    for concept in concepts:
        group = groups.setdefault(concept["concept_id"], {"label": concept["label"], "claims": []})
        for claim in concept["claims"]:
            cid = claims.setdefault(claim["claim_id"], f"C{len(claims) + 1}")
            refs, context_refs = [], []
            for target, items in ((refs, claim["evidence"]), (context_refs, claim.get("context_evidence", []))):
                for item in items:
                    source = evidence.setdefault(item["evidence_id"], {
                        "evidence_id": f"E{len(evidence) + 1}",
                        **note_content.evidence_location(item), "quotes": [],
                    })
                    if item["quote"] not in source["quotes"]:
                        source["quotes"].append(item["quote"])
                    if source["evidence_id"] not in target:
                        target.append(source["evidence_id"])
            group["claims"].append({"claim_id": cid, "text": claim["text"], "evidence_ids": refs,
                "context_evidence_ids": context_refs})
    return {"kind": kind, "previous_section": previous_section,
            "previous_content": note_content.previous_content(previous_paragraphs,
                json.dumps(list(groups.values()), ensure_ascii=False)),
            "concepts": list(groups.values()), "evidence": list(evidence.values())}, {
        "claims": {ref: original for original, ref in claims.items()},
        "evidence": {item["evidence_id"]: original for original, item in evidence.items()},
    }


def canonical_response(response, identities):
    parsed = note_content.Response.model_validate(response).model_dump()
    try:
        for p in parsed["paragraphs"]:
            p["claim_ids"] = [identities["claims"][ref] for ref in p["claim_ids"]]
            p["evidence_ids"] = [identities["evidence"][ref] for ref in p["evidence_ids"]]
    except KeyError:
        raise ValueError("NOTE_REFERENCE_INVALID") from None
    return parsed


def batches(concepts, kind, budget):
    """按學習順序以完整重點切批，單一重點不裁切來源。"""
    result, current, seen = [], [], set()
    for concept in concepts:
        for claim in concept["claims"]:
            if claim["claim_id"] in seen:
                continue
            seen.add(claim["claim_id"])
            unit = {"concept_id": concept["concept_id"], "label": concept["label"], "claims": [claim]}
            if estimate_prompt_tokens(note_content.PROMPT, model_request([unit], kind)[0]) > budget:
                raise ValueError("NOTE_INPUT_TOO_LARGE")
            if current and estimate_prompt_tokens(note_content.PROMPT, model_request([*current, unit], kind)[0]) > budget:
                result.append(current)
                current = []
            current.append(unit)
    if current:
        result.append(current)
    return result


def _write(request, identities, part, mode, call, diagnostics):
    """取得一份可用的寫作回應：格式錯誤補正一次；完整筆記的引用錯誤逐段請模型補正一次，
    複習速覽的引用由程式整理後交給內容核對。"""
    format_retried = False
    reference_repair = None
    repaired_references = set()
    repaired_formulas = set()
    response = call(request)
    while True:
        try:
            if reference_repair is not None:
                original, index = reference_repair
                repaired = note_content.Response.model_validate(response).model_dump()["paragraphs"]
                if index >= len(repaired):
                    raise ValueError("NOTE_FORMAT_INVALID")
                repaired = repaired[index]
                # 引用補正只採用出錯段落的編號，正文及其他合法段落不跟著重寫。
                for field in ("claim_ids", "evidence_ids"):
                    original["paragraphs"][index][field] = repaired[field]
                response = original
            if mode == "summary":
                return _summary_references(response, identities, part, diagnostics)
            return note_content.validate(canonical_response(response, identities), part, mode)
        except (ValueError, TypeError, KeyError) as error:
            reference_error = isinstance(error, note_content.ReferenceInvalid)
            formula_error = isinstance(error, note_content.note_latex.FormulaInvalid)
            code = str(error) if str(error).startswith("NOTE_") else "NOTE_FORMAT_INVALID"
            diagnostics.append({"event": "response_invalid", "code": code,
                **({"paragraph": error.paragraph_index} if reference_error else {})})
            formula_position = (error.paragraph_index, error.block_index) if formula_error else None
            if (reference_error and error.paragraph_index in repaired_references
                or formula_error and formula_position in repaired_formulas
                or not reference_error and not formula_error and format_retried):
                # 僅正規化模型 schema 驗證失敗；程式與 provider 例外不交給批次重試。
                if isinstance(error, ValidationError):
                    raise ValueError("NOTE_FORMAT_INVALID") from error
                raise
            if formula_error:
                # 只送該公式與本節引用，不重送整批教材與前稿；內容仍由後續逐段核對。
                pi, bi = formula_position
                paragraph = response["paragraphs"][pi]
                correction = call({
                    "phase": "repair_formula", "latex": error.latex,
                    "heading": paragraph["heading"],
                    "evidence": [e for e in request["evidence"] if e["evidence_id"] in paragraph["evidence_ids"]],
                    "correction": {"problem": code, "instruction": error.detail},
                })
                try:
                    repaired = note_content.FormulaBlock.model_validate(correction)
                except ValidationError:
                    raise ValueError("NOTE_FORMULA_INVALID") from None
                response["paragraphs"][pi]["blocks"][bi]["latex"] = repaired.latex
                repaired_formulas.add(formula_position)
                continue
            format_retried = True
            instruction = ("只修正 previous_response 的 claim_ids／evidence_ids，標題、層級與所有 blocks 保持不變。每個 claim_ids 都須有其對應的 evidence_ids；刪掉不屬於本節 claim_ids 的 evidence_ids，兩種編號都不可重複。"
                if code == "NOTE_REFERENCE_INVALID" else
                "修正前次回應的格式或重點涵蓋問題。")
            request = {**request, "correction": {**request.get("correction", {}),
                "problem": code,
                "instruction": instruction,
                "previous_response": response}}
            if reference_error:
                repaired_references.add(error.paragraph_index)
                reference_repair = (deepcopy(response), error.paragraph_index)
                claims = {original: ref for ref, original in identities["claims"].items()}
                evidence = {original: ref for ref, original in identities["evidence"].items()}
                request["correction"]["reference_error"] = {
                    "paragraph_index": error.paragraph_index,
                    "claims_missing_evidence": [claims[c] for c in error.missing_claims],
                    "evidence_options": {q["claim_id"]: q["evidence_ids"]
                        for c in request["concepts"] for q in c["claims"]
                        if q["claim_id"] in {claims[cid] for cid in error.missing_claims}},
                    "evidence_not_in_paragraph_claims": [evidence[e] for e in error.extra_evidence],
                }
            response = call(request)


def _claim_sources(claim):
    return {e["evidence_id"] for e in [*claim["evidence"], *claim.get("context_evidence", [])]}


def _summary_references(response, identities, part, diagnostics):
    """複習速覽的引用由程式整理：重點沒引用自己的來源就移除該重點，
    來源不屬於本段保留的重點就移除該來源；失去來源支持的文字交給後續核對。"""
    claims = {q["claim_id"]: q for c in part for q in c["claims"]}
    kept = []
    kept_indices = []
    for index, p in enumerate(note_content.Response.model_validate(response).model_dump()["paragraphs"]):
        claim_ids = list(dict.fromkeys(identities["claims"][r] for r in p["claim_ids"] if r in identities["claims"]))
        evidence_ids = list(dict.fromkeys(identities["evidence"][r] for r in p["evidence_ids"] if r in identities["evidence"]))
        claim_ids = [cid for cid in claim_ids
            if set(evidence_ids).intersection(e["evidence_id"] for e in claims[cid]["evidence"])]
        allowed = set().union(*(_claim_sources(claims[cid]) for cid in claim_ids))
        evidence_ids = [eid for eid in evidence_ids if eid in allowed]
        if len(claim_ids) != len(p["claim_ids"]) or len(evidence_ids) != len(p["evidence_ids"]):
            diagnostics.append({"event": "references_trimmed", "paragraph": index,
                "claims_removed": len(p["claim_ids"]) - len(claim_ids),
                "evidence_removed": len(p["evidence_ids"]) - len(evidence_ids),
                "paragraph_dropped": not claim_ids})
        if claim_ids:
            kept.append({**p, "claim_ids": claim_ids, "evidence_ids": evidence_ids})
            kept_indices.append(index)
    if not kept:
        raise ValueError("NOTE_REFERENCE_INVALID")
    try:
        return note_content.validate({"paragraphs": kept}, part, "summary")
    except note_content.note_latex.FormulaInvalid as error:
        error.paragraph_index = kept_indices[error.paragraph_index]
        raise


def generate_part(part, mode, call, diagnostics=None, previous_section=None, previous_paragraphs=()):
    diagnostics = [] if diagnostics is None else diagnostics
    request, identities = model_request(part, mode, previous_section, previous_paragraphs)
    result = _write(request, identities, part, mode, call, diagnostics)
    if mode == "summary":
        # 挑選已按整章限3～5個重點；每個重點最多承載一條核心，刪減後才核對。
        used, compact = set(), []
        for paragraph in result:
            slots = len(set(paragraph["claim_ids"]) - used)
            blocks, core = [], 0
            for block in paragraph["blocks"]:
                if block["type"] == "formula":
                    blocks.append(block)
                    continue
                if block["type"] == "text":
                    if core < slots:
                        blocks.append(block)
                        core += 1
                    continue
                items = block["items"] if block["type"] in ("bullets", "callout") else []
                kept = [{"text": item["text"], "children": []} for item in items[:slots - core]] if core < slots else []
                if kept:
                    blocks.append({"type": "bullets", "items": kept})
                    core += len(kept)
            if blocks != paragraph["blocks"]:
                diagnostics.append({"event": "summary_compacted", "claims": paragraph["claim_ids"]})
            if blocks:
                compact.append({**paragraph, "blocks": blocks})
                if core:
                    used.update(paragraph["claim_ids"])
        result = compact
    return _check_paragraphs(result, part, call, diagnostics, previous_paragraphs, kind=mode)


def _reason_digest(issues):
    # 只保存位置與摘要碼，不將教材或核對理由全文寫入一般日誌。
    return [{"part": i["part"], "reason_sha256": canonical_sha256(i["reason"])[:12]} for i in issues]


def _check_paragraphs(result, part, call, diagnostics, previous_paragraphs=(), *, kind="full"):
    """每段核對一次；移除不符位置及依賴它的子項，已支持內容不重寫。"""
    checked = []
    claims = {q["claim_id"]: q for c in part for q in c["claims"]}
    for index, paragraph in enumerate(result):
        coverage = {}
        issues = note_content.review_paragraph(paragraph, call, claims=claims, kind=kind,
            related_paragraphs=checked, previous_paragraphs=previous_paragraphs, coverage_details=coverage)
        if issues:
            diagnostics.append({"event": "review_rejected", "paragraph": index,
                "issues": _reason_digest(issues)})
        trimmed, retained = note_content.remove_unsupported(paragraph, issues)
        if not trimmed["blocks"]:
            diagnostics.append({"event": "paragraph_dropped", "reason": "no_supported_content",
                "claims": paragraph["claim_ids"]})
            continue
        # 被刪掉的解說位置不能繼續算涵蓋；引用仍保留作正文的來源，不冒充已解說。
        explained = [cid for cid, locations in coverage.items()
            if locations and all(location.startswith("related.") or location in retained for location in locations)]
        trimmed["_explained_claim_ids"] = explained
        checked.append(trimmed)
    return checked


def fit_page_context(concepts, kind, budget, diagnostics):
    """單一重點連同同頁補充原文就超過一批的容量時，只對這個重點省略一般補充原文，保留獨立規範詞及重點自己的 Evidence；
    省略後仍過大由切批回報 NOTE_INPUT_TOO_LARGE。依大小決定，計畫與接續寫作得到相同結果，不必另存。"""
    omitted, result = [], []
    for c in concepts:
        claims = []
        for q in c["claims"]:
            if q.get("context_evidence") and estimate_prompt_tokens(
                    note_content.PROMPT, model_request([{**c, "claims": [q]}], kind)[0]) > budget:
                q = {**q, "context_evidence": [e for e in q["context_evidence"]
                    if " ".join(e["quote"].split()) in note_content.NORMATIVE_WORDS]}
                omitted.append(q["claim_id"])
            claims.append(q)
        result.append({**c, "claims": claims})
    if omitted:
        diagnostics.append({"event": "context_omitted", "claims": sorted(set(omitted))})
    return result


def summary_plan(concepts, sections, call, *, input_budget):
    """每一來源章都挑選核心，候選少仍執行挑選，不強求每個概念都有內容。"""
    selected = set()
    chapter = []
    for section in [*sections, {"level": 1, "claim_ids": []}]:
        if section["level"] == 1 and chapter:
            ids = {cid for item in chapter for cid in item["claim_ids"]}
            scope = [{**c, "claims": [q for q in c["claims"] if q["claim_id"] in ids]} for c in concepts]
            scope = [c for c in scope if c["claims"]]
            if scope:
                selected.update(q["claim_id"] for c in select_summary(scope, call, input_budget=input_budget)
                    for q in c["claims"])
            chapter = []
        chapter.append(section)
    return plan_sections(concepts, [{**section, "claim_ids": [cid for cid in section["claim_ids"] if cid in selected]}
        for section in sections], "summary", input_budget)


def select_summary(concepts, call, *, input_budget):
    """整章挑3～5個核心重點；大章先分批挑選，再從候選中收斂到整章上限。"""
    owner = {}
    for c in concepts:
        for q in c["claims"]:
            owner.setdefault(q["claim_id"], c["concept_id"])
    entries = [(c, [q for q in c["claims"] if owner[q["claim_id"]] == c["concept_id"]]) for c in concepts]
    entries = [(c, claims) for c, claims in entries if claims]
    while entries:
        chosen = set()
        for batch in _selection_batches(entries, input_budget):
            chosen.update(_select_batch(batch, call))
        if len(chosen) <= 5:
            return [{**c, "claims": kept} for c in concepts
                if (kept := [q for q in c["claims"] if q["claim_id"] in chosen])]
        reduced = [(c, [q for q in claims if q["claim_id"] in chosen]) for c, claims in entries]
        reduced = [(c, claims) for c, claims in reduced if claims]
        if sum(len(claims) for _, claims in reduced) == sum(len(claims) for _, claims in entries):
            raise ValueError("NOTE_INPUT_TOO_LARGE")
        entries = reduced
    raise ValueError("NOTE_SOURCE_EMPTY")


def _selection_request(batch):
    concepts, refs = [], {}
    for concept, claims in batch:
        items = []
        for q in claims:
            ref = refs.setdefault(q["claim_id"], f"C{len(refs) + 1}")
            items.append({"ref": ref, "text": q["text"], "sources": list(dict.fromkeys(
                e["source_name"] for e in q["evidence"] if e.get("source_name")))})
        concepts.append({"ref": f"K{len(concepts) + 1}", "label": concept["label"], "claims": items})
    return {"phase": "select_summary", "concepts": concepts, "max_claims": 5}, {ref: cid for cid, ref in refs.items()}


def _selection_batches(entries, budget):
    """依學習順序裝批；先以各項估算累加，再以整批實際估算確認，超過就對半拆。"""
    def size(batch):
        return estimate_prompt_tokens(note_content.SELECTION_PROMPT, _selection_request(batch)[0])

    def fit(batch):
        if size(batch) <= budget:
            return [batch]
        units = [(c, q) for c, claims in batch for q in claims]
        if len(units) == 1:
            raise ValueError("NOTE_INPUT_TOO_LARGE")
        halves = []
        for half in (units[:len(units) // 2], units[len(units) // 2:]):
            grouped = []
            for c, q in half:
                if grouped and grouped[-1][0] is c:
                    grouped[-1][1].append(q)
                else:
                    grouped.append((c, [q]))
            halves.extend(fit(grouped))
        return halves

    base = size([])
    result, current, total = [], [], base
    for concept, claims in entries:
        label = estimate_o200k_tokens(json.dumps({"ref": "K0000", "label": concept["label"], "claims": []}, ensure_ascii=False))
        for q in claims:
            cost = estimate_o200k_tokens(json.dumps(_selection_request([(concept, [q])])[0]["concepts"][0]["claims"][0],
                ensure_ascii=False)) + 2
            same = bool(current) and current[-1][0] is concept
            if current and total + cost + (0 if same else label) > budget:
                result.append(current)
                current, total, same = [], base, False
            if same:
                current[-1][1].append(q)
            else:
                current.append((concept, [q]))
                total += label
            total += cost
    if current:
        result.append(current)
    return [part for batch in result for part in fit(batch)]


def _repair_selection(response, request):
    """Gemma 常違反選取規則；能讀出結構時以確定性規則修補，不讓整份速覽失敗。

    重複或不屬於該概念的 id 移除、重複概念合併、缺少的概念視為空選取；
    超過 5 個依模型順序截斷，不足 3 個依候選原順序補足。結構讀不出時回傳 None。
    """
    picks = response.get("concepts") if isinstance(response, dict) else None
    if not isinstance(picks, list) or not all(
        isinstance(p, dict) and isinstance(p.get("concept"), str)
        and isinstance(p.get("claim_ids"), list) and all(isinstance(i, str) for i in p["claim_ids"])
        for p in picks
    ):
        return None
    expected = {k["ref"]: [q["ref"] for q in k["claims"]] for k in request["concepts"]}
    chosen = []
    for pick in picks:
        allowed = expected.get(pick["concept"], [])
        chosen.extend(ref for ref in pick["claim_ids"] if ref in allowed and ref not in chosen)
    chosen = chosen[:request["max_claims"]]
    candidates = list(dict.fromkeys(ref for refs in expected.values() for ref in refs))
    for ref in candidates:
        if len(chosen) >= min(3, len(candidates)):
            break
        if ref not in chosen:
            chosen.append(ref)
    return chosen


def _select_batch(batch, call):
    request, refs = _selection_request(batch)
    for attempt in range(2):
        response = call(request)
        chosen = _repair_selection(response, request)
        if chosen is not None:
            return {refs[ref] for ref in chosen}
        if attempt:
            raise ValueError("NOTE_SELECTION_INVALID")
        request = {**request, "correction": {
            "instruction": "修正前次選取結果：回覆 {\"concepts\": [{\"concept\": 概念 ref, \"claim_ids\": [候選 ref]}]}；每個概念 ref 恰好回覆一次，claim_ids 可為空；全章合計選3～5個（候選不足3個則全選），只能選各概念自己的候選，不重複。",
            "previous_response": response,
        }}


def finalize(paragraphs, concepts, kind):
    if not paragraphs:
        return []
    checked = note_content.validate({"paragraphs": [{k: p[k] for k in note_content.Paragraph.model_fields}
        for p in paragraphs]}, concepts, kind)
    return [{**p, "_explained_claim_ids": original.get("_explained_claim_ids", [])}
        for p, original in zip(checked, paragraphs)]


def plan_sections(concepts, sections, kind="full", budget=None):
    """來源章節可拆開同一概念的重點；只在章內按容量分批。"""
    result = []
    for section in sections:
        selected = [{**c, "claims": kept} for c in concepts
            if (kept := [q for q in c["claims"] if q["claim_id"] in section["claim_ids"]])]
        result.append({**section, "label": section["heading"], "parts": [
            [{"concept_id": c["concept_id"], "claim_ids": [q["claim_id"] for q in c["claims"]]} for c in part]
            for part in batches(selected, kind, budget or BATCH_TOKENS)]})
    return result


# 格式、引用、公式或核對回應格式不合法時才重試批次；完整筆記另補寫未涵蓋概念一次。
# 契約、容量、provider 等重試也不會改變的錯誤不在此列，直接失敗。
RETRYABLE_PART_ERRORS = {"NOTE_FORMAT_INVALID",
    "NOTE_REFERENCE_INVALID", "NOTE_TABLE_INVALID", "NOTE_FORMULA_INVALID", "NOTE_REVIEW_INVALID"}
PART_ATTEMPTS = 3


def complete_sections(concepts, plan, call, results, on_part, diagnostics=None, kind="full"):
    diagnostics = [] if diagnostics is None else diagnostics
    by_id = {c["concept_id"]: c for c in concepts}
    parts = [(section, part) for section in plan for part in section["parts"]]
    for index in range(len(results), len(parts)):
        section, batch = parts[index]
        selected = [{**by_id[c["concept_id"]], "claims": [q for q in by_id[c["concept_id"]]["claims"]
            if q["claim_id"] in c["claim_ids"]]} for c in batch]
        codes = []
        previous = [p for part in results for p in part]
        generated = None
        while True:
            try:
                if generated is None:
                    generated = (generate_part(selected, kind, call, diagnostics, section["heading"], previous)
                        if selected else [])
                if kind == "full":
                    explained = {cid for p in [*previous, *generated]
                        for cid in p.get("_explained_claim_ids", [])}
                    missing = [c for c in selected
                        if not any(q["claim_id"] in explained
                            for q in by_id[c["concept_id"]]["claims"])]
                    if missing:
                        diagnostics.append({"event": "coverage_supplement", "part": index,
                            "concepts": [c["concept_id"] for c in missing]})
                        generated.extend(generate_part(missing, kind, call, diagnostics,
                            section["heading"], [*previous, *generated]))
                generated = [{**p, "level": section["level"]} for p in generated]
                break
            except (ValueError, note_content.ReviewInvalid) as error:
                if str(error) not in RETRYABLE_PART_ERRORS:
                    raise
                codes.append(str(error))
                if len(codes) == PART_ATTEMPTS:
                    diagnostics.append({"event": "part_attempts", "part": index, "attempts": len(codes),
                        "failed": codes, "succeeded": False})
                    raise
        if codes:
            # 只有一次就通過的批次不另記，沒有刪減或重試的成功仍保存為 NULL。
            diagnostics.append({"event": "part_attempts", "part": index, "attempts": len(codes) + 1,
                "failed": codes, "succeeded": True})
        on_part(index, generated)
        results.append(generated)
    return finalize([p for part in results for p in part], concepts, kind)


def section_progress(row):
    if row.section_plan is None:
        return None
    total = sum(len(s["parts"]) for s in row.section_plan)
    completed = total if row.progress_stage == "completed" else len(row.section_results or [])
    offset, sections, current = 0, 0, None
    for section in row.section_plan:
        offset += len(section["parts"])
        if offset <= completed:
            sections += 1
        elif current is None:
            current = section["label"]
    return {"stage": row.progress_stage, "completed_sections": sections,
            "total_sections": len(row.section_plan), "completed_parts": completed,
            "total_parts": total, "current_section": current}


def _progress(claim, dsn, *, plan=None, part=None, previous=None, stage="generating"):
    with database_session(dsn) as db:
        _material(db, *claim["key"][:2], lock=True)
        row = db.get(MaterialNotes, claim["key"], with_for_update=True)
        if row is None or row.status != "running" or not lease_is_current(row, claim["token"], now=datetime.now(UTC)):
            raise JobCancelled()
        if plan is not None:
            row.section_plan, row.section_results = plan, []
        if part is not None:
            index, paragraphs = part
            if len(row.section_results or []) != index:
                raise JobCancelled()
            row.section_results = [*(previous if previous is not None else row.section_results or []), paragraphs]
        row.progress_stage = stage
        row.updated_at = datetime.now(UTC)


def _complete(claim, concepts, call, dsn, diagnostics, *, input_budget):
    kind = claim["key"][3]
    input_budget -= note_content.PREVIOUS_CONTENT_TOKENS + 128
    concepts = fit_page_context(concepts, kind, BATCH_TOKENS if kind == "full" else input_budget, diagnostics)
    plan = claim["plan"]
    if plan is None:
        with database_session(dsn) as db:
            document = knowledge_structures._read_verified_document(db, *claim["key"][:2], revision=claim["key"][2])
            regions = knowledge_reads.regions(db, *claim["key"][:3])["regions"]
        sections = note_sections.plan(document, concepts, regions)
        plan = (summary_plan(concepts, sections, call, input_budget=input_budget) if kind == "summary"
            else plan_sections(concepts, sections))
        _progress(claim, dsn, plan=plan)
    result = complete_sections(concepts, plan, call, claim["results"],
        lambda index, paragraphs: _progress(claim, dsn, part=(index, paragraphs), previous=claim["results"]), diagnostics, kind)
    _progress(claim, dsn, stage="validating")
    return result, note_sections.published(plan, result, concepts, kind=kind)


def request_contract(payload, policy):
    """依請求階段選提示與回應 schema；核對與挑選的位置／編號以 enum 限定在本次請求。"""
    phase = payload.get("phase")
    if phase == "review_paragraph":
        prompt, schema = policy["review_prompt"], note_content.ContentReview
    elif phase == "repair_formula":
        prompt, schema = policy["prompt"], note_content.FormulaBlock
    elif phase == "select_summary":
        prompt, schema = policy["selection_prompt"], note_content.SummarySelection
    else:
        prompt, schema = policy["prompt"], note_content.Response
    response_schema = note_content.response_schema(schema)
    if phase == "review_paragraph":
        response_schema["$defs"]["ContentCheck"]["properties"]["part"]["enum"] = payload["parts"]
        missing = response_schema["$defs"]["ContentCheck"]["properties"]["missing_evidence"]
        context_refs = [e["ref"] for e in payload.get("reference_context", [])]
        if context_refs:
            missing["items"]["enum"] = context_refs
        else:
            missing["maxItems"] = 0
        response_schema["properties"]["checks"].update(minItems=len(payload["parts"]), maxItems=len(payload["parts"]))
        refs = payload["coverage_claims"]
        response_schema["properties"]["coverage"].update(minItems=len(refs), maxItems=len(refs))
        if refs:
            response_schema["$defs"]["ClaimCoverage"]["properties"]["claim"]["enum"] = refs
            response_schema["$defs"]["ClaimCoverage"]["properties"]["note_parts"]["items"]["enum"] = list(dict.fromkeys(
                location for values in payload["coverage_locations"].values() for location in values))
    elif phase == "select_summary":
        refs = [k["ref"] for k in payload["concepts"]]
        response_schema["properties"]["concepts"].update(minItems=len(refs), maxItems=len(refs))
        fields = response_schema["$defs"]["ConceptSelection"]["properties"]
        fields["concept"]["enum"] = refs
        fields["claim_ids"]["items"]["enum"] = [q["ref"] for k in payload["concepts"] for q in k["claims"]]
    return prompt, response_schema


def step(*, dsn=None, call=None, stop=None):
    """由教材執行緒處理一份；測試可注入受控模型。"""
    claim = _claim(dsn)
    if claim is None:
        return False
    if claim.get("skipped"):
        return True
    trace = luna_calls.start(
        "note_" + claim["key"][3], claim["key"], claim["token"],
        claim["key"][1], claim["key"][0], enabled=luna_test.enabled(claim["lock"]),
    )
    diagnostics = []
    try:
        lock = deepcopy(claim["lock"])
        policy = lock["material_notes"]
        if (policy["version"] != note_content.POLICY or policy["prompt"] != note_content.PROMPT
            or policy["selection_prompt"] != note_content.SELECTION_PROMPT
            or policy["review_prompt"] != note_content.REVIEW_PROMPT):
            raise ValueError("NOTE_CONTRACT_INVALID")
        # 沿現有文字 provider 通道，使用工作建立時封存的獨立筆記契約。
        lock["material_review"] = {**lock["material_review"], "prompt": policy["prompt"], "max_tokens": policy["max_tokens"]}
        from .maintenance_reanalysis import semantic_call_for_revision
        maintenance_call = (
            semantic_call_for_revision(*claim["key"][:3], dsn=dsn)
            if claim["key"][3] == "summary" and claim["generation"] == 0 else None
        )

        def request(payload):
            _check(claim, dsn, stop, count_call=True)
            if call is not None:
                return luna_calls.capture(call(payload))
            prompt, response_schema = request_contract(payload, policy)
            task_lock = {**lock["material_review"], "prompt": prompt}
            with semantic_client() as client:
                return (maintenance_call or request_semantics)(client, runtime_lock={**lock, "material_review": task_lock}, task="material_review",
                    request=payload, response_schema=response_schema,
                    cancellation_check=lambda: _check(claim, dsn, stop))

        concepts = _concepts(claim, dsn)
        result, metadata = _complete(claim, concepts, request, dsn, diagnostics,
            input_budget=lock["semantic_service"]["max_model_len"] - policy["max_tokens"])
        _check(claim, dsn, stop)
        summary = {"events": diagnostics} if diagnostics else None
        if summary:
            # 發布前刪掉的重點／段落只記編號、位置與理由摘要碼；日誌與 DB 存同一份。
            logging.getLogger(__name__).warning("NOTE_GENERATION_ADJUSTED kind=%s diagnostics=%s",
                claim["key"][3], json.dumps(summary, ensure_ascii=False))
        _save(claim, dsn, paragraphs=result, metadata=metadata, diagnostics=summary)
    except WorkerStopping:
        _save(claim, dsn, stopping=True)
    except JobCancelled:
        luna_calls.completed("cancelled")
    except Exception as error:
        code = error.reason_code if isinstance(error, SemanticServiceError) else str(error)
        if code not in {"NOTE_FORMAT_INVALID", "NOTE_REFERENCE_INVALID", "NOTE_TABLE_INVALID", "NOTE_FORMULA_INVALID",
            "NOTE_INPUT_TOO_LARGE", "NOTE_SOURCE_EMPTY", "NOTE_CONTRACT_INVALID", "NOTE_SECTION_PLAN_INVALID", "NOTE_SELECTION_INVALID", "NOTE_REVIEW_INVALID"} and not isinstance(error, SemanticServiceError):
            code = "NOTE_GENERATION_FAILED"
        summary = {"code": code, "error_type": type(error).__name__, "events": diagnostics}
        luna_calls.rejected({"reason": str(error), "diagnostics": diagnostics})
        logging.getLogger(__name__).warning("NOTE_GENERATION_FAILED kind=%s diagnostics=%s",
            claim["key"][3], json.dumps(summary, ensure_ascii=False))
        _save(claim, dsn, error=code, diagnostics=summary)
    finally:
        luna_calls.close(trace)
    return True
