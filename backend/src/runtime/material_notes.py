"""每教材版本的兩種筆記、持久生成與不隨重生消失的個人註記。"""
from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4
import json
import logging

from sqlalchemy import select, or_, text
from sqlalchemy.dialects.postgresql import insert

from content_identity import canonical_sha256

from . import note_content
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
        paragraphs = deepcopy(row.paragraphs or []) if row else []
        annotations = [{"annotation_id": str(a.annotation_id), "anchor": a.anchor,
            "body": a.body, "original_revision": a.structure_revision,
            "original_paragraph": a.paragraph_snapshot, "updated_at": a.updated_at.isoformat()}
            for a in db.scalars(select(NoteAnnotation).where(NoteAnnotation.learner_id == owner,
                NoteAnnotation.material_id == material_id, NoteAnnotation.kind == kind)
                .order_by(NoteAnnotation.created_at, NoteAnnotation.annotation_id))]
        return {"schema": "material-notes/v1", "material_id": str(material_id),
            "knowledge_structure_revision": revision, "run_id": str(structure.run_id),
            "kind": kind, "source_status": meta["status"], "status": row.status if row else "not_generated",
            "generation": row.generation if row else 0, "paragraphs": paragraphs,
            "annotations": note_content.annotation_matches(paragraphs, annotations),
            "source_resolver": meta["source_resolver"], "error_code": row.error_code if row else None,
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
            row.updated_at = datetime.now(UTC)
    return read(owner, material_id, revision, kind, dsn=dsn)


def annotate(owner, material_id, revision, kind, annotation_id, body,
             paragraph_id, expected_generation, *, dsn=None):
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
            paragraph = next((p for p in row.paragraphs or [] if p["id"] == paragraph_id), None)
            if paragraph is None:
                raise SourceError("REQUEST_INVALID")
            now = datetime.now(UTC)
            db.add(NoteAnnotation(annotation_id=annotation_id, learner_id=owner,
                material_id=material_id, kind=kind, structure_revision=revision,
                anchor=paragraph["anchor"], paragraph_snapshot=deepcopy(paragraph), body=body,
                created_at=now, updated_at=now))
    return read(owner, material_id, revision, kind, dsn=dsn)


def queue_existing(*, owner, dsn=None):
    """部署後只補本人尚無重點整理的目前版本；重跑不重生既有結果。"""
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
        row.lease_expires_at = now + WORKER_LEASE_TTL
        row.attempts += 1
        row.updated_at = now
        return {"key": (row.learner_id, row.material_id, row.structure_revision, row.kind),
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


def _save(claim, dsn, *, paragraphs=None, error=None, stopping=False, diagnostics=None):
    with database_session(dsn) as db:
        material = db.scalar(select(Material).where(Material.material_id == claim["key"][1],
            Material.learner_id == claim["key"][0]).with_for_update())
        row = db.get(MaterialNotes, claim["key"], with_for_update=True)
        if material is None or material.discard_requested_at is not None or row is None or row.status != "running" or not lease_is_current(row, claim["token"], now=datetime.now(UTC)):
            return
        if paragraphs is not None:
            row.paragraphs, row.generation = paragraphs, row.generation + 1
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


def _concepts(claim, dsn):
    owner, material, revision, _ = claim["key"]
    with database_session(dsn) as db:
        view = knowledge_reads.outline(db, owner, material, revision)
        path = knowledge_reads.learning_path(view)
        rows = knowledge_reads.read_concepts(db, owner, material, revision, path)
        by_id = {r.concept_id: r.payload["concept"] for r in rows}
        document = knowledge_structures._read_verified_document(db, owner, material, revision=revision)
        return note_content.with_page_context([by_id[c] for c in path], document)


def model_request(concepts, kind):
    """請求只帶一次原文；短編號在此請求內有效，發布前還原正式身分。"""
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
    return {"kind": kind, "concepts": list(groups.values()), "evidence": list(evidence.values())}, {
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
    result, current = [], []
    for concept in concepts:
        for claim in concept["claims"]:
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
    重點整理的引用由程式整理後交給內容核對。"""
    format_retried = False
    reference_repair = None
    repaired_references = set()
    while True:
        response = call(request)
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
            code = str(error) if str(error).startswith("NOTE_") else "NOTE_FORMAT_INVALID"
            diagnostics.append({"event": "response_invalid", "code": code,
                **({"paragraph": error.paragraph_index} if reference_error else {})})
            if (reference_error and error.paragraph_index in repaired_references
                or not reference_error and format_retried):
                raise
            format_retried = True
            instruction = ("只修正 previous_response 的 claim_ids／evidence_ids，標題、正文、公式與表格保持不變。每個 claim_ids 都須有其對應的 evidence_ids；刪掉不屬於本段 claim_ids 的 evidence_ids，兩種編號都不可重複。"
                if code == "NOTE_REFERENCE_INVALID" else "修正前次回應的格式或重點涵蓋問題。")
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


def _claim_sources(claim):
    return {e["evidence_id"] for e in [*claim["evidence"], *claim.get("context_evidence", [])]}


def _summary_references(response, identities, part, diagnostics):
    """重點整理的引用由程式整理：重點沒引用自己的來源就移除該重點，
    來源不屬於本段保留的重點就移除該來源；失去來源支持的文字交給後續核對。"""
    claims = {q["claim_id"]: q for c in part for q in c["claims"]}
    kept = []
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
    if not kept:
        raise ValueError("NOTE_REFERENCE_INVALID")
    return note_content.validate({"paragraphs": kept}, part, "summary")


def generate_part(part, mode, call, diagnostics=None):
    diagnostics = [] if diagnostics is None else diagnostics
    request, identities = model_request(part, mode)
    result = _write(request, identities, part, mode, call, diagnostics)
    if mode == "full":
        return _check_paragraphs(result, part, call, diagnostics)
    # 同一份候選稿內，引用或段落刪減後請求完全相同的段落沿用已核對結果。
    review = _remembered_review(call)
    items = _check_summary([(p, False) for p in result], part, call, review, diagnostics)
    missing = _unexplained_concepts(items, part)
    if missing:
        diagnostics.append({"event": "concepts_unexplained", "concepts": missing, "after_repair": False})
        items = _check_summary(_cover_concepts(items, missing, part, call, diagnostics), part, call, review, diagnostics)
        missing = _unexplained_concepts(items, part)
        if missing:
            diagnostics.append({"event": "concepts_unexplained", "concepts": missing, "after_repair": True})
            raise ValueError("NOTE_COVERAGE_INCOMPLETE")
    return [p for p, _ in items]


def _unexplained_concepts(items, part):
    """每個概念至少要有一個重點留在已核對的段落中（核對已確認段內列出的重點都有實質解說）。"""
    cited = {cid for p, _ in items for cid in p["claim_ids"]}
    claims = {}
    for c in part:
        claims.setdefault(c["concept_id"], set()).update(q["claim_id"] for q in c["claims"])
    return [concept for concept, ids in claims.items() if not ids & cited]


def _cover_concepts(items, missing, part, call, diagnostics):
    """沒有被解說的概念只為它們局部補寫一次；新段落比照局部重寫，核對仍不符就刪段。"""
    scope = [c for c in part if c["concept_id"] in missing]
    request, identities = model_request(scope, "summary")
    request["correction"] = {"problem": "NOTE_CONCEPT_UNEXPLAINED",
        "instruction": "前次重點整理沒有實際解說這些概念。為每個概念依原文說明它最核心的重點，只把正文真正解說的重點列入 claim_ids；不要只列名稱或編號，也不要補寫原文沒有的內容。"}
    try:
        added = _write(request, identities, scope, "summary", call, diagnostics)
    except (ValueError, TypeError, KeyError) as error:
        diagnostics.append({"event": "cover_invalid",
            "code": str(error) if str(error).startswith("NOTE_") else "NOTE_FORMAT_INVALID"})
        return items
    order = {}
    for index, c in enumerate(part):
        for q in c["claims"]:
            order.setdefault(q["claim_id"], index)

    def position(paragraph):
        return min(order[cid] for cid in paragraph["claim_ids"])
    result = list(items)
    for paragraph in added:
        # 依學習順序插在第一個較後面的段落之前，不改動既有段落的順序。
        index = next((i for i, (p, _) in enumerate(result) if position(p) > position(paragraph)), len(result))
        result.insert(index, (paragraph, True))
    return _summary_items(result, part)


def _remembered_review(call):
    """同一份候選稿中，核對請求完全相同就沿用已核對結果，不重送模型。"""
    responses = {}

    def remember(payload):
        key = canonical_sha256(payload)
        if key not in responses:
            responses[key] = call(payload)
        return responses[key]

    def review(paragraph, claims, coverage_ids, related):
        return note_content.review_paragraph(paragraph, remember, claims=claims, kind="summary",
            coverage_claim_ids=coverage_ids, related_paragraphs=related)
    return review


def _summary_items(items, part):
    """移除引用或段落後重新驗證，重建段落編號、錨點與來源節錄。"""
    paragraphs = note_content.validate({"paragraphs": [{k: p[k] for k in note_content.Paragraph.model_fields}
        for p, _ in items]}, part, "summary")
    return [(p, repaired) for p, (_, repaired) in zip(paragraphs, items)]


def _reason_digest(issues):
    # 只保存核對理由的摘要碼與位置，不保存可能含教材內容的理由全文。
    return [{"part": i["part"], "reason_sha256": canonical_sha256(i["reason"])[:12]} for i in issues]


def _check_summary(items, part, call, review, diagnostics):
    """重點整理逐段核對。items 為（段落, 是否已局部重寫）；未解說的重點由程式移除，
    局部重寫一次仍不符的段落整段刪除。刪除後是否仍涵蓋每個概念由呼叫端檢查。"""
    claims = {q["claim_id"]: q for c in part for q in c["claims"]}
    while True:
        for index, (paragraph, repaired) in enumerate(items):
            later = {cid for p, _ in items[index+1:] for cid in p["claim_ids"]}
            # 同一重點可跨段；在最後一段檢查合併涵蓋，前文皆已完成事實核對。
            coverage_ids = [cid for cid in paragraph["claim_ids"] if cid not in later]
            issues = review(paragraph, claims, coverage_ids, [p for p, _ in items[:index]])
            if issues:
                break
        else:
            return items
        refs = {f"claim.C{i+1}": cid for i, cid in enumerate(paragraph["claim_ids"])}
        unexplained = {refs[i["part"]] for i in issues if i["part"] in refs}
        diagnostics.append({"event": "review_rejected", "paragraph": index, "repaired": repaired,
            "issues": _reason_digest(issues)})
        if unexplained:
            # 未解說表示全稿都沒說明：移除全稿所有出現處及只屬於它的來源，引用改變的段落重新核對。
            diagnostics.append({"event": "claims_removed", "claims": sorted(unexplained)})
            remaining = []
            for p, flag in items:
                ids = [cid for cid in p["claim_ids"] if cid not in unexplained]
                if not ids:
                    diagnostics.append({"event": "paragraph_dropped", "reason": "no_claims", "claims": p["claim_ids"]})
                    continue
                allowed = set().union(*(_claim_sources(claims[cid]) for cid in ids))
                remaining.append(({**p, "claim_ids": ids,
                    "evidence_ids": [eid for eid in p["evidence_ids"] if eid in allowed]}, flag))
        elif repaired:
            diagnostics.append({"event": "paragraph_dropped", "reason": "content_unsupported",
                "claims": paragraph["claim_ids"]})
            remaining = [*items[:index], *items[index+1:]]
        else:
            try:
                replacement = _repair_paragraph(paragraph, index, issues, [p for p, _ in items[:index]],
                    part, "summary", call, diagnostics)
            except (ValueError, TypeError, KeyError) as error:
                # 局部重寫沒有產生可用段落，等同重寫後仍不符：刪掉這一段。
                diagnostics.append({"event": "paragraph_dropped", "reason": "repair_invalid",
                    "code": str(error) if str(error).startswith("NOTE_") else "NOTE_FORMAT_INVALID",
                    "claims": paragraph["claim_ids"]})
                replacement = []
            remaining = [*items[:index], *((p, True) for p in replacement), *items[index+1:]]
        if not remaining:
            return []
        items = _summary_items(remaining, part)


def _repair_paragraph(paragraph, index, issues, previous, part, mode, call, diagnostics):
    """只重生不符段落，避免把已核對的其他段落重新改寫並引入新錯誤。"""
    ids = set(paragraph["claim_ids"])
    scope = [{**c, "claims": [q for q in c["claims"] if q["claim_id"] in ids]} for c in part]
    scope = [c for c in scope if c["claims"]]
    repair, mapping = model_request(scope, mode)
    repair["correction"] = {
        "problem": "NOTE_CONTENT_UNSUPPORTED",
        "instruction": "只修正這個段落中核對指出的不符字句或缺漏；其他已有來源支持的敘述保持不變。先查本次 context_evidence_ids 的實際表頭、規範詞與條件，若能補足數值或片語的意義，須明確解說並把那些區塊列入 evidence_ids，不要只重複缺少對象的原片語。related_paragraphs 已說明的部分不必重複，只補本段缺漏，仍保留本次提供的所有重點編號。保持原有大致篇幅，只依原文修正，不把核對理由或其他筆記當作新來源。",
        "content_review": {"paragraph_index": index,
            "paragraph": {k: paragraph[k] for k in ("heading", "text", "formula", "table")},
            "issues": issues, "related_paragraphs": [
                {k: p[k] for k in ("heading", "text", "formula", "table")}
                for p in previous if ids.intersection(p["claim_ids"])]},
    }
    # 完整筆記的修正須保留本段所有重點；重點整理沿用程式整理引用，交給後續核對。
    return _write(repair, mapping, scope, "full" if mode == "full" else "summary", call, diagnostics)


def _check_paragraphs(result, part, call, diagnostics):
    """完整筆記逐段核對；不符只重生該段一次，仍不符就不發布。"""
    checked = []
    claims = {q["claim_id"]: q for c in part for q in c["claims"]}
    for index, paragraph in enumerate(result):
        later = {cid for p in result[index+1:] for cid in p["claim_ids"]}
        coverage_ids = [cid for cid in paragraph["claim_ids"] if cid not in later]
        # 同一重點可跨段；在最後一段檢查合併涵蓋，前文皆已完成事實核對。
        issues = note_content.review_paragraph(paragraph, call, claims=claims, kind="full",
            coverage_claim_ids=coverage_ids, related_paragraphs=checked)
        if not issues:
            checked.append(paragraph)
            continue
        diagnostics.append({"event": "review_rejected", "paragraph": index, "repaired": False,
            "issues": _reason_digest(issues)})
        repaired = _repair_paragraph(paragraph, index, issues, checked, part, "full", call, diagnostics)
        for position, replacement in enumerate(repaired):
            remaining = {cid for p in repaired[position+1:] for cid in p["claim_ids"]}
            final_ids = [cid for cid in replacement["claim_ids"] if cid in coverage_ids and cid not in remaining]
            issues = note_content.review_paragraph(replacement, call, claims=claims, kind="full",
                coverage_claim_ids=final_ids, related_paragraphs=[*checked, *repaired[:position]])
            if issues:
                diagnostics.append({"event": "review_rejected", "paragraph": len(checked) + position,
                    "repaired": True, "issues": _reason_digest(issues)})
                raise ValueError("NOTE_CONTENT_UNSUPPORTED")
        checked.extend(repaired)
    return checked


def summary_sources(concepts, input_budget, diagnostics):
    """單一重點連同同頁補充原文就超過一次請求時，只對這個重點省略補充原文，重點自己的 Evidence 完整保留。
    依大小決定，計畫與接續寫作得到相同結果，不必另存。"""
    omitted, result = [], []
    for c in concepts:
        claims = []
        for q in c["claims"]:
            if q.get("context_evidence") and estimate_prompt_tokens(
                    note_content.PROMPT, model_request([{**c, "claims": [q]}], "summary")[0]) > input_budget:
                q = {**q, "context_evidence": []}
                omitted.append(q["claim_id"])
            claims.append(q)
        result.append({**c, "claims": claims})
    if omitted:
        diagnostics.append({"event": "context_omitted", "claims": sorted(set(omitted))})
    return result


def summary_plan(concepts, call, *, input_budget):
    """重點整理的分批計畫：一次寫得下就全部交給寫作；否則先以概念為單位挑核心重點。
    計畫保存選中的重點，接續時不重新挑選；寫作批次沿用完整筆記的切批方式。"""
    if not concepts or not any(c["claims"] for c in concepts):
        raise ValueError("NOTE_SOURCE_EMPTY")
    if estimate_prompt_tokens(note_content.PROMPT, model_request(concepts, "summary")[0]) > input_budget:
        concepts = select_summary(concepts, call, input_budget=input_budget)
    return plan_sections(concepts, [{"label": "重點整理", "concept_ids": [c["concept_id"] for c in concepts]}],
        "summary", input_budget)


def select_summary(concepts, call, *, input_budget):
    """每個概念挑出核心重點；挑選只看知識結構與來源檔名，不計原文與同頁補充原文。
    共用重點只在第一個所屬概念出現一次；每個重點只送進一次挑選請求。"""
    owner = {}
    for c in concepts:
        for q in c["claims"]:
            owner.setdefault(q["claim_id"], c["concept_id"])
    chosen, entries = set(), []
    for c in concepts:
        own = [q for q in c["claims"] if owner[q["claim_id"]] == c["concept_id"]]
        if len(own) == 1:
            chosen.add(own[0]["claim_id"])  # 只有一個候選，不必請模型挑。
        elif own:
            entries.append((c, own))
    for batch in _selection_batches(entries, input_budget):
        chosen |= _select_batch(batch, call)
    selected = []
    for c in concepts:
        claims = [q for q in c["claims"] if q["claim_id"] in chosen]
        if not claims:
            # 只有與前面概念共用的重點且都沒被挑中：取第一個，確保這個概念也會寫到。
            claims = c["claims"][:1]
            chosen.add(claims[0]["claim_id"])
        selected.append({**c, "claims": claims})
    return selected


def _selection_request(batch):
    concepts, refs = [], {}
    for concept, claims in batch:
        items = []
        for q in claims:
            ref = refs.setdefault(q["claim_id"], f"C{len(refs) + 1}")
            items.append({"ref": ref, "text": q["text"], "sources": list(dict.fromkeys(
                e["source_name"] for e in q["evidence"] if e.get("source_name")))})
        concepts.append({"ref": f"K{len(concepts) + 1}", "label": concept["label"], "claims": items})
    return {"phase": "select_summary", "concepts": concepts}, {ref: cid for cid, ref in refs.items()}


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


def _select_batch(batch, call):
    request, refs = _selection_request(batch)
    expected = {k["ref"]: {q["ref"] for q in k["claims"]} for k in request["concepts"]}
    for attempt in range(2):
        response = call(request)
        try:
            picks = note_content.SummarySelection.model_validate(response).concepts
            if (sorted(p.concept for p in picks) != sorted(expected)
                or any(len(p.claim_ids) != len(set(p.claim_ids)) or not set(p.claim_ids) <= expected[p.concept] for p in picks)):
                raise ValueError("NOTE_SELECTION_INVALID")
            return {refs[ref] for p in picks for ref in p.claim_ids}
        except ValueError:
            if attempt:
                raise ValueError("NOTE_SELECTION_INVALID") from None
            request = {**request, "correction": {
                "instruction": "修正前次選取結果：每個概念 ref 恰好回覆一次；claim_ids 只能選該概念自己的候選 ref，至少一個且不重複。",
                "previous_response": response,
            }}


def finalize(paragraphs, concepts, kind):
    return note_content.validate({"paragraphs": [{k: p[k] for k in note_content.Paragraph.model_fields}
        for p in paragraphs]}, concepts, kind)


def plan_sections(concepts, regions, kind="full", budget=None):
    """凍結區域／章節順序與各批重點；區內再依輸入容量切批，不設總呼叫上限。"""
    by_id = {c["concept_id"]: c for c in concepts}
    flat = [cid for region in regions for cid in region["concept_ids"]]
    if len(flat) != len(set(flat)) or set(flat) != by_id.keys():
        raise ValueError("NOTE_SECTION_PLAN_INVALID")
    return [{"label": region["label"], "parts": [
        [{"concept_id": c["concept_id"], "claim_ids": [q["claim_id"] for q in c["claims"]]} for c in part]
        for part in batches([by_id[c] for c in region["concept_ids"]], kind, budget or BATCH_TOKENS)]} for region in regions]


def complete_sections(concepts, plan, call, results, on_part, diagnostics=None, kind="full"):
    by_id = {c["concept_id"]: c for c in concepts}
    parts = [part for section in plan for part in section["parts"]]
    for index in range(len(results), len(parts)):
        selected = [{**by_id[c["concept_id"]], "claims": [q for q in by_id[c["concept_id"]]["claims"]
            if q["claim_id"] in c["claim_ids"]]} for c in parts[index]]
        generated = generate_part(selected, kind, call, diagnostics)
        on_part(index, generated)
        results.append(generated)
    return finalize([p for part in results for p in part], concepts, kind)


def section_progress(row):
    if row.kind != "full" or row.section_plan is None:
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


def _progress(claim, dsn, *, plan=None, part=None, stage="generating"):
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
            row.section_results = [*(row.section_results or []), paragraphs]
        row.progress_stage = stage
        row.updated_at = datetime.now(UTC)


def _complete(claim, concepts, call, dsn, diagnostics, *, input_budget):
    kind = claim["key"][3]
    if kind == "summary":
        concepts = summary_sources(concepts, input_budget, diagnostics)
    plan = claim["plan"]
    if plan is None:
        if kind == "full":
            with database_session(dsn) as db:
                regions = knowledge_reads.regions(db, *claim["key"][:3])["regions"]
            plan = plan_sections(concepts, regions)
        else:
            # 重點整理不設頁數；大教材依模型上下文分批，並與完整筆記同樣保存已核對的批次。
            plan = summary_plan(concepts, call, input_budget=input_budget)
        _progress(claim, dsn, plan=plan)
    result = complete_sections(concepts, plan, call, claim["results"],
        lambda index, paragraphs: _progress(claim, dsn, part=(index, paragraphs)), diagnostics, kind)
    _progress(claim, dsn, stage="validating")
    return result


def step(*, dsn=None, call=None, stop=None):
    """由教材執行緒處理一份；測試可注入受控模型。"""
    claim = _claim(dsn)
    if claim is None:
        return False
    if claim.get("skipped"):
        return True
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

        def request(payload):
            _check(claim, dsn, stop, count_call=True)
            if call is not None:
                return call(payload)
            phase = payload.get("phase")
            if phase == "review_paragraph":
                prompt, schema = policy["review_prompt"], note_content.ContentReview
            elif phase == "select_summary":
                prompt, schema = policy["selection_prompt"], note_content.SummarySelection
            else:
                prompt, schema = policy["prompt"], note_content.Response
            response_schema = schema.model_json_schema()
            if phase == "review_paragraph":
                response_schema["$defs"]["ContentCheck"]["properties"]["part"]["enum"] = payload["parts"]
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
            task_lock = {**lock["material_review"], "prompt": prompt}
            with semantic_client() as client:
                return request_semantics(client, runtime_lock={**lock, "material_review": task_lock}, task="material_review",
                    request=payload, response_schema=response_schema,
                    cancellation_check=lambda: _check(claim, dsn, stop))

        concepts = _concepts(claim, dsn)
        result = _complete(claim, concepts, request, dsn, diagnostics,
            input_budget=lock["semantic_service"]["max_model_len"] - policy["max_tokens"])
        _check(claim, dsn, stop)
        summary = {"events": diagnostics} if diagnostics else None
        if summary:
            # 發布前刪掉的重點／段落只記編號、位置與理由摘要碼；日誌與 DB 存同一份。
            logging.getLogger(__name__).warning("NOTE_GENERATION_ADJUSTED kind=%s diagnostics=%s",
                claim["key"][3], json.dumps(summary, ensure_ascii=False))
        _save(claim, dsn, paragraphs=result, diagnostics=summary)
    except WorkerStopping:
        _save(claim, dsn, stopping=True)
    except JobCancelled:
        pass
    except Exception as error:
        code = error.reason_code if isinstance(error, SemanticServiceError) else str(error)
        if code not in {"NOTE_FORMAT_INVALID", "NOTE_REFERENCE_INVALID", "NOTE_TABLE_INVALID", "NOTE_COVERAGE_INCOMPLETE",
            "NOTE_INPUT_TOO_LARGE", "NOTE_SOURCE_EMPTY", "NOTE_CONTRACT_INVALID", "NOTE_SECTION_PLAN_INVALID", "NOTE_SELECTION_INVALID", "NOTE_CONTENT_UNSUPPORTED", "NOTE_REVIEW_INVALID"} and not isinstance(error, SemanticServiceError):
            code = "NOTE_GENERATION_FAILED"
        summary = {"code": code, "error_type": type(error).__name__, "events": diagnostics}
        logging.getLogger(__name__).warning("NOTE_GENERATION_FAILED kind=%s diagnostics=%s",
            claim["key"][3], json.dumps(summary, ensure_ascii=False))
        _save(claim, dsn, error=code, diagnostics=summary)
    return True
