"""每教材版本的兩種筆記、持久生成與不隨重生消失的個人註記。"""
from copy import deepcopy
from datetime import UTC, datetime
from math import ceil
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
from .text_budget import estimate_prompt_tokens

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
            if row.kind == "full":
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


def batches(concepts):
    """按學習順序以完整重點切批，單一重點不裁切來源。"""
    result, current = [], []
    for concept in concepts:
        for claim in concept["claims"]:
            unit = {"concept_id": concept["concept_id"], "label": concept["label"], "claims": [claim]}
            if estimate_prompt_tokens(note_content.PROMPT, model_request([unit], "full")[0]) > BATCH_TOKENS:
                raise ValueError("NOTE_INPUT_TOO_LARGE")
            if current and estimate_prompt_tokens(note_content.PROMPT, model_request([*current, unit], "full")[0]) > BATCH_TOKENS:
                result.append(current)
                current = []
            current.append(unit)
    if current:
        result.append(current)
    return result


def _layout_request(request, paragraphs, layout, *, expand=False):
    action = "充實" if expand else "精簡"
    ratio = round(layout["target_height_ratio"] * 100)
    instruction = (
        f"依實測排版{action}到目前稿高約 {ratio}%。"
        f"本次標題、正文、公式與所有表格儲存格合計目標為 {layout['suggested_characters']} 字，"
        + ("請接近這個目標。" if expand else "輸出前自行計數，不得超過這個依本稿超頁比例算出的目標。")
        + "不要套固定段數；以填入一頁為目標，不改字級。"
        + ("對照原文補充尚未說明的核心內容，包含已引用重點中省略的用途、機制或必要條件；只有編號列出不算已解說。沒有其他核心內容可補時保留原稿，不以重複敘述填空。" if expand else
           "優先精簡贅詞與重複說明、合併相近段落／表格，盡量保留不同主題及其必要條件。")
    )
    correction = {
        "problem": "NOTE_SUMMARY_ROOM" if expand else "NOTE_SUMMARY_TOO_LONG",
        "instruction": instruction, "layout": layout,
        "previous_body": [{k: p[k] for k in ("heading", "text", "formula", "table")} for p in paragraphs],
    }
    if not expand:
        correction["max_paragraphs"] = max(1, ceil(len(paragraphs) * layout["target_height_ratio"]))
        correction["instruction"] += (
            f"依本稿超頁比例，合併相近主題為至多 {correction['max_paragraphs']} 段，減少標題及段間留白；保留不同主題的核心敘述。")
    return {**request, "correction": correction}


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
                if code == "NOTE_REFERENCE_INVALID" else "修正前次回應的格式或重點涵蓋問題，並遵守已有的排版限制。")
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
    expanded, reflows = False, 0
    while True:
        result = _write(request, identities, part, mode, call, diagnostics)
        if mode == "full":
            return _check_paragraphs(result, part, call, diagnostics)
        layout = note_content.summary_layout(result)
        if not layout["fits"]:
            if reflows < 4:
                reflows += 1
                diagnostics.append({"event": "reflow", "height_ratio": layout["height_ratio"]})
                request = _layout_request(request, result, layout)
                continue
            items = _trim_to_page([(p, False) for p in result], part, diagnostics)
        else:
            if not expanded:
                smallest = min(note_content.summary_layout([p])["used_height_pt"] for p in result)
                room = layout["available_height_pt"] - layout["used_height_pt"]
                # ID 已列出不代表其核心內容已說明；有空間就請模型檢查一次可補充的來源內容。
                if room >= smallest:
                    expanded = True
                    request = _layout_request(request, result, layout, expand=True)
                    continue
            items = [(p, False) for p in result]
        # 每份新稿各自核對；同一稿內引用或段落刪減後，請求完全相同的段落沿用已核對結果。
        review = _remembered_review(call)
        while True:
            items = _check_summary(items, part, call, review, diagnostics)
            checked = [p for p, _ in items]
            layout = note_content.summary_layout(checked)
            if layout["fits"]:
                return checked
            if reflows < 4:
                break
            # 重排次數用盡：從最後一段整段刪除，刪段可能改變跨段重點的涵蓋位置，須再核對。
            items = _trim_to_page(items, part, diagnostics)
        reflows += 1
        diagnostics.append({"event": "reflow", "height_ratio": layout["height_ratio"]})
        request = _layout_request(request, checked, layout)  # 重排改動正文後，全部新段落仍須重新核對。


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


def _trim_to_page(items, part, diagnostics):
    """不裁切文字、不縮字級：從最後一段整段刪到放進一頁。"""
    items = list(items)
    layout = note_content.summary_layout([p for p, _ in items])
    while not layout["fits"]:
        removed, _ = items.pop()
        diagnostics.append({"event": "paragraph_dropped", "reason": "page_overflow",
            "claims": removed["claim_ids"]})
        if not items:
            raise note_content.SummaryTooLong(layout)
        layout = note_content.summary_layout([p for p, _ in items])
    return _summary_items(items, part)


def _reason_digest(issues):
    # 只保存核對理由的摘要碼與位置，不保存可能含教材內容的理由全文。
    return [{"part": i["part"], "reason_sha256": canonical_sha256(i["reason"])[:12]} for i in issues]


def _check_summary(items, part, call, review, diagnostics):
    """重點整理逐段核對。items 為（段落, 是否已局部重寫）；未解說的重點由程式移除，
    局部重寫一次仍不符的段落整段刪除，全稿刪光才失敗。"""
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
            cause = "NOTE_COVERAGE_INCOMPLETE"
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
            cause = "NOTE_CONTENT_UNSUPPORTED"
            diagnostics.append({"event": "paragraph_dropped", "reason": "content_unsupported",
                "claims": paragraph["claim_ids"]})
            remaining = [*items[:index], *items[index+1:]]
        else:
            cause = "NOTE_CONTENT_UNSUPPORTED"
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
            raise ValueError(cause)
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


def compute_summary(concepts, call, *, input_budget, diagnostics=None):
    if not concepts or not any(c["claims"] for c in concepts):
        raise ValueError("NOTE_SOURCE_EMPTY")
    selected = concepts
    while estimate_prompt_tokens(note_content.PROMPT, model_request(selected, "summary")[0]) > input_budget:
        # 挑選只需知識結構；確定選中重點後，寫作仍帶入完整原文，不截斷 Evidence。
        catalog = {}
        for c in selected:
            for q in c["claims"]:
                item = catalog.setdefault(q["claim_id"], {"ref": f"C{len(catalog)+1}", "concepts": [], "sources": [], "text": q["text"]})
                if c["label"] not in item["concepts"]:
                    item["concepts"].append(c["label"])
                for evidence in q["evidence"]:
                    name = evidence.get("source_name")
                    if name and name not in item["sources"]:
                        item["sources"].append(name)
        groups, group = [], []
        for item in catalog.values():
            if estimate_prompt_tokens(note_content.SELECTION_PROMPT, {"candidates": [item]}) > input_budget:
                raise ValueError("NOTE_INPUT_TOO_LARGE")
            if group and estimate_prompt_tokens(note_content.SELECTION_PROMPT, {"candidates": [*group, item]}) > input_budget:
                groups.append(group)
                group = []
            group.append(item)
        if group:
            groups.append(group)
        chosen = set()
        for group in groups:
            limit = max(1, len(group) // 2)
            request = {"phase": "select_summary", "max_claims": limit, "candidates": group}
            allowed = {c["ref"] for c in group}
            for attempt in range(2):
                response = call(request)
                try:
                    refs = note_content.SummarySelection.model_validate(response).claim_ids
                    if len(refs) > limit or len(refs) != len(set(refs)) or not set(refs) <= allowed:
                        raise ValueError("NOTE_SELECTION_INVALID")
                    chosen.update(refs)
                    break
                except ValueError:
                    if attempt:
                        raise ValueError("NOTE_SELECTION_INVALID") from None
                    request = {**request, "correction": {
                        "instruction": f"修正前次選取結果，只選輸入中的 ref，勿重複。請自行計數，最多 {limit} 個；超過時刪除較次要項目，不要再增加。",
                        "previous_response": response,
                    }}
        ids = {cid for cid, c in catalog.items() if c["ref"] in chosen}
        if len(ids) >= len(catalog):
            raise ValueError("NOTE_INPUT_TOO_LARGE")
        selected = [{**c, "claims": [q for q in c["claims"] if q["claim_id"] in ids]} for c in selected]
        selected = [c for c in selected if c["claims"]]
    return finalize(generate_part(selected, "summary", call, diagnostics), concepts, "summary")


def finalize(paragraphs, concepts, kind):
    return note_content.validate({"paragraphs": [{k: p[k] for k in note_content.Paragraph.model_fields}
        for p in paragraphs]}, concepts, kind)


def plan_sections(concepts, regions):
    """凍結地圖區域／章節順序；區內再依輸入容量切批，不設總呼叫上限。"""
    by_id = {c["concept_id"]: c for c in concepts}
    flat = [cid for region in regions for cid in region["concept_ids"]]
    if len(flat) != len(set(flat)) or set(flat) != by_id.keys():
        raise ValueError("NOTE_SECTION_PLAN_INVALID")
    return [{"label": region["label"], "parts": [
        [{"concept_id": c["concept_id"], "claim_ids": [q["claim_id"] for q in c["claims"]]} for c in part]
        for part in batches([by_id[c] for c in region["concept_ids"]])]} for region in regions]


def complete_sections(concepts, plan, call, results, on_part, diagnostics=None):
    by_id = {c["concept_id"]: c for c in concepts}
    parts = [part for section in plan for part in section["parts"]]
    for index in range(len(results), len(parts)):
        selected = [{**by_id[c["concept_id"]], "claims": [q for q in by_id[c["concept_id"]]["claims"]
            if q["claim_id"] in c["claim_ids"]]} for c in parts[index]]
        generated = generate_part(selected, "full", call, diagnostics)
        on_part(index, generated)
        results.append(generated)
    return finalize([p for part in results for p in part], concepts, "full")


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


def _complete_full(claim, concepts, call, dsn, diagnostics):
    plan = claim["plan"]
    if plan is None:
        with database_session(dsn) as db:
            regions = knowledge_reads.regions(db, *claim["key"][:3])["regions"]
        plan = plan_sections(concepts, regions)
        _progress(claim, dsn, plan=plan)
    result = complete_sections(concepts, plan, call, claim["results"],
        lambda index, paragraphs: _progress(claim, dsn, part=(index, paragraphs)), diagnostics)
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
                response_schema["properties"]["claim_ids"]["maxItems"] = payload["max_claims"]
            elif phase != "review_paragraph" and "max_paragraphs" in payload.get("correction", {}):
                response_schema["properties"]["paragraphs"]["maxItems"] = payload["correction"]["max_paragraphs"]
            task_lock = {**lock["material_review"], "prompt": prompt}
            with semantic_client() as client:
                return request_semantics(client, runtime_lock={**lock, "material_review": task_lock}, task="material_review",
                    request=payload, response_schema=response_schema,
                    cancellation_check=lambda: _check(claim, dsn, stop))

        concepts = _concepts(claim, dsn)
        result = (_complete_full(claim, concepts, request, dsn, diagnostics) if claim["key"][3] == "full"
                  else compute_summary(concepts, request,
                      input_budget=lock["semantic_service"]["max_model_len"] - policy["max_tokens"],
                      diagnostics=diagnostics))
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
            "NOTE_SUMMARY_TOO_LONG", "NOTE_INPUT_TOO_LARGE", "NOTE_SOURCE_EMPTY", "NOTE_CONTRACT_INVALID", "NOTE_SECTION_PLAN_INVALID", "NOTE_SELECTION_INVALID", "NOTE_CONTENT_UNSUPPORTED", "NOTE_REVIEW_INVALID"} and not isinstance(error, SemanticServiceError):
            code = "NOTE_GENERATION_FAILED"
        summary = {"code": code, "error_type": type(error).__name__, "events": diagnostics}
        logging.getLogger(__name__).warning("NOTE_GENERATION_FAILED kind=%s diagnostics=%s",
            claim["key"][3], json.dumps(summary, ensure_ascii=False))
        _save(claim, dsn, error=code, diagnostics=summary)
    return True
