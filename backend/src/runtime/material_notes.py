"""每教材版本的兩種筆記、持久生成與不隨重生消失的個人註記。"""
from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4
import logging

from sqlalchemy import select, or_, text
from sqlalchemy.dialects.postgresql import insert

from . import note_content
from .errors import SourceError
from .generation_jobs import JobCancelled, WORKER_LEASE_TTL, lease_is_current
from .local_ai_config import configured_lock
from .semantic_service import request_semantics, semantic_client, SemanticServiceError
from .storage.database import database_session
from .storage import knowledge_reads
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


def _save(claim, dsn, *, paragraphs=None, error=None, stopping=False):
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
        row.lease_token = row.lease_expires_at = None
        row.updated_at = datetime.now(UTC)


def _concepts(claim, dsn):
    owner, material, revision, _ = claim["key"]
    with database_session(dsn) as db:
        view = knowledge_reads.outline(db, owner, material, revision)
        path = knowledge_reads.learning_path(view)
        rows = knowledge_reads.read_concepts(db, owner, material, revision, path)
        by_id = {r.concept_id: r.payload["concept"] for r in rows}
        return [by_id[c] for c in path]


def model_request(concepts, kind):
    """請求只帶一次原文；短編號在此請求內有效，發布前還原正式身分。"""
    groups, claims, evidence = {}, {}, {}
    for concept in concepts:
        group = groups.setdefault(concept["concept_id"], {"label": concept["label"], "claims": []})
        for claim in concept["claims"]:
            cid = claims.setdefault(claim["claim_id"], f"C{len(claims) + 1}")
            refs = []
            for item in claim["evidence"]:
                source = evidence.setdefault(item["evidence_id"], {
                    "evidence_id": f"E{len(evidence) + 1}",
                    **note_content.evidence_location(item), "quotes": [],
                })
                if item["quote"] not in source["quotes"]:
                    source["quotes"].append(item["quote"])
                refs.append(source["evidence_id"])
            group["claims"].append({"claim_id": cid, "text": claim["text"], "evidence_ids": refs})
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
        correction["max_paragraphs"] = max(1, int(len(paragraphs) * layout["target_height_ratio"]))
        correction["instruction"] += (
            f"依本稿超頁比例，合併相近主題為至多 {correction['max_paragraphs']} 段，減少標題及段間留白；保留不同主題的核心敘述。")
    return {**request, "correction": correction}


def generate_part(part, mode, call):
    request, identities = model_request(part, mode)
    format_retried, expanded, reflows = False, False, 0
    reference_repair = None
    repaired_references = set()
    while True:
        response = call(request)
        try:
            if reference_repair is not None:
                original, index = reference_repair
                repaired = note_content.Response.model_validate(response).model_dump()["paragraphs"][index]
                # 引用補正只採用出錯段落的編號，正文及其他合法段落不跟著重寫。
                for field in ("claim_ids", "evidence_ids"):
                    original["paragraphs"][index][field] = repaired[field]
                response = original
            result = note_content.validate(canonical_response(response, identities), part, mode)
        except (ValueError, TypeError, KeyError) as error:
            reference_error = isinstance(error, note_content.ReferenceInvalid)
            if (reference_error and error.paragraph_index in repaired_references
                or not reference_error and format_retried):
                raise
            format_retried = True
            code = str(error) if str(error).startswith("NOTE_") else "NOTE_FORMAT_INVALID"
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
            continue
        # 每份重排新稿各可修正一次格式；較早稿件用過的補正不應阻止新稿修正引用。
        format_retried = False
        reference_repair = None
        repaired_references.clear()
        if mode == "summary":
            layout = note_content.summary_layout(result)
            if not layout["fits"]:
                if reflows >= 4:
                    raise note_content.SummaryTooLong(layout)
                reflows += 1
                request = _layout_request(request, result, layout)
                continue
            if not expanded and not reflows:
                smallest = min(note_content.summary_layout([p])["used_height_pt"] for p in result)
                room = layout["available_height_pt"] - layout["used_height_pt"]
                # ID 已列出不代表其核心內容已說明；有空間就請模型檢查一次可補充的來源內容。
                if room >= smallest:
                    expanded = True
                    request = _layout_request(request, result, layout, expand=True)
                    continue
        checked, changed = _check_paragraphs(result, part, mode, call)
        if mode == "summary" and changed:
            layout = note_content.summary_layout(checked)
            if not layout["fits"]:
                if reflows >= 4:
                    raise note_content.SummaryTooLong(layout)
                reflows += 1
                request = _layout_request(request, checked, layout)
                continue  # 重排改動正文後，全部新段落仍須重新核對。
        return checked


def _check_paragraphs(result, part, mode, call):
    checked = []
    repaired_any = False
    for index, paragraph in enumerate(result):
        issues = note_content.review_paragraph(paragraph, call)
        if not issues:
            checked.append(paragraph)
            continue
        # 只重生不符段落，避免把已核對的其他段落重新改寫並引入新錯誤。
        repaired_any = True
        ids = set(paragraph["claim_ids"])
        scope = [{**c, "claims": [q for q in c["claims"] if q["claim_id"] in ids]} for c in part]
        scope = [c for c in scope if c["claims"]]
        repair, mapping = model_request(scope, mode)
        repair["correction"] = {
            "problem": "NOTE_CONTENT_UNSUPPORTED",
            "instruction": "只修正這個段落中核對指出的不符字句；其他已有來源支持的敘述保持不變，避免重寫時新增斷言。保持原有大致篇幅，保留本次提供的所有重點。只依原文修正，不把核對理由當作新來源。",
            "content_review": {"paragraph_index": index,
                "paragraph": {k: paragraph[k] for k in ("heading", "text", "formula", "table")},
                "issues": issues},
        }
        # 替換前後保留同一組重點；摘要亦不能在修正單段時偷偷刪掉原本涵蓋的點。
        repaired = note_content.validate(canonical_response(call(repair), mapping), scope, "full")
        for replacement in repaired:
            if note_content.review_paragraph(replacement, call):
                raise ValueError("NOTE_CONTENT_UNSUPPORTED")
        checked.extend(repaired)
    return checked, repaired_any


def compute_summary(concepts, call, *, input_budget):
    if not concepts or not any(c["claims"] for c in concepts):
        raise ValueError("NOTE_SOURCE_EMPTY")
    selected = concepts
    while estimate_prompt_tokens(note_content.PROMPT, model_request(selected, "summary")[0]) > input_budget:
        # 挑選只需知識結構；確定選中重點後，寫作仍帶入完整原文，不截斷 Evidence。
        catalog = {}
        for c in selected:
            for q in c["claims"]:
                item = catalog.setdefault(q["claim_id"], {"ref": f"C{len(catalog)+1}", "concepts": [], "text": q["text"]})
                if c["label"] not in item["concepts"]:
                    item["concepts"].append(c["label"])
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
    return finalize(generate_part(selected, "summary", call), concepts, "summary")


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


def complete_sections(concepts, plan, call, results, on_part):
    by_id = {c["concept_id"]: c for c in concepts}
    parts = [part for section in plan for part in section["parts"]]
    for index in range(len(results), len(parts)):
        selected = [{**by_id[c["concept_id"]], "claims": [q for q in by_id[c["concept_id"]]["claims"]
            if q["claim_id"] in c["claim_ids"]]} for c in parts[index]]
        generated = generate_part(selected, "full", call)
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


def _complete_full(claim, concepts, call, dsn):
    plan = claim["plan"]
    if plan is None:
        with database_session(dsn) as db:
            regions = knowledge_reads.regions(db, *claim["key"][:3])["regions"]
        plan = plan_sections(concepts, regions)
        _progress(claim, dsn, plan=plan)
    result = complete_sections(concepts, plan, call, claim["results"],
        lambda index, paragraphs: _progress(claim, dsn, part=(index, paragraphs)))
    _progress(claim, dsn, stage="validating")
    return result


def step(*, dsn=None, call=None, stop=None):
    """由教材執行緒處理一份；測試可注入受控模型。"""
    claim = _claim(dsn)
    if claim is None:
        return False
    if claim.get("skipped"):
        return True
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
            if phase == "select_summary":
                response_schema["properties"]["claim_ids"]["maxItems"] = payload["max_claims"]
            elif phase != "review_paragraph" and "max_paragraphs" in payload.get("correction", {}):
                response_schema["properties"]["paragraphs"]["maxItems"] = payload["correction"]["max_paragraphs"]
            task_lock = {**lock["material_review"], "prompt": prompt}
            with semantic_client() as client:
                return request_semantics(client, runtime_lock={**lock, "material_review": task_lock}, task="material_review",
                    request=payload, response_schema=response_schema,
                    cancellation_check=lambda: _check(claim, dsn, stop))

        concepts = _concepts(claim, dsn)
        result = (_complete_full(claim, concepts, request, dsn) if claim["key"][3] == "full"
                  else compute_summary(concepts, request,
                      input_budget=lock["semantic_service"]["max_model_len"] - policy["max_tokens"]))
        _check(claim, dsn, stop)
        _save(claim, dsn, paragraphs=result)
    except WorkerStopping:
        _save(claim, dsn, stopping=True)
    except JobCancelled:
        pass
    except Exception as error:
        code = error.reason_code if isinstance(error, SemanticServiceError) else str(error)
        if code not in {"NOTE_REFERENCE_INVALID", "NOTE_TABLE_INVALID", "NOTE_COVERAGE_INCOMPLETE",
            "NOTE_SUMMARY_TOO_LONG", "NOTE_INPUT_TOO_LARGE", "NOTE_SOURCE_EMPTY", "NOTE_CONTRACT_INVALID", "NOTE_SECTION_PLAN_INVALID", "NOTE_SELECTION_INVALID", "NOTE_CONTENT_UNSUPPORTED", "NOTE_REVIEW_INVALID"} and not isinstance(error, SemanticServiceError):
            code = "NOTE_GENERATION_FAILED"
        logging.getLogger(__name__).warning("NOTE_GENERATION_FAILED error_type=%s", type(error).__name__)
        _save(claim, dsn, error=code)
    return True
