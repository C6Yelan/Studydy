from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import tempfile
from typing import Any

from . import generation, transport
from .transport import _canonical_sha256, _valid_sha256
from .source import (
    _cleanup_source_snapshot,
    _copy_source_snapshot,
    _source_page_count,
)
from ..concept_candidates import build_concept_context
from ..concept_content import (
    CONCEPT_CONTENT_PROMPT_VERSION,
    CONCEPT_CONTENT_SCHEMA,
    MAX_CONTENT_GROUPS,
    build_concept_keywords,
    build_summary_context,
)
from ..concept_deduplication import group_concept_candidates
from .generation import (
    generate_concept_candidate,
    generate_concept_content,
    generate_development_page_structure,
    generate_visual_review,
)
from ..page_alignment import assess_page_structure_alignment
from .. import page_evidence
from ..study_material_output import (
    CONCEPT_CONTEXT_UNAVAILABLE,
    FORMAL_PROVIDER_DEFERRED,
    PAGE_CONTENT_EXCLUDED,
    build_study_material_output,
)


RUN_SCHEMA = "material-analysis-run/v1"
_OPERATIONS = (
    "page_structure",
    "visual_alignment_adjudication",
    "concept_candidate",
    "concept_content",
)


def _empty_metrics() -> dict[str, int]:
    """建立四個 model operation 與總數的固定計數器。"""
    return {**{operation: 0 for operation in _OPERATIONS}, "total": 0}


def _record_generation(
    operation: str,
    generation: dict[str, Any],
    provider_call_counts: dict[str, int],
    cache_hits: dict[str, int],
) -> None:
    """只累加 generation client 回傳的可信計數欄位。"""
    provider_call_counts[operation] += generation["provider_call_count"]
    provider_call_counts["total"] += generation["provider_call_count"]
    if generation["cache_hit"]:
        cache_hits[operation] += 1
        cache_hits["total"] += 1


def development_pipeline_binding(local_config: Any) -> dict[str, Any] | None:
    """回傳不含 endpoint 或路徑的 development pipeline binding。"""
    if not transport._valid_config(local_config):
        return None
    runtime_binding_sha256 = _canonical_sha256(
        {
            "page_structure": generation._runtime_binding(
                "page_structure", local_config, "page-render/v1"
            ),
            "visual_alignment_adjudication": generation._runtime_binding(
                "visual_alignment_adjudication", local_config, "page-render/v1"
            ),
            "concept_candidate": generation._runtime_binding(
                "concept_candidate", local_config, "page-render/v1"
            ),
            "concept_content": generation._runtime_binding(
                "concept_content", local_config, "not-applicable"
            ),
        }
    )
    return {
        "schema": "material-analysis-pipeline-binding/v1",
        "runtime_binding_sha256": runtime_binding_sha256,
    }


def _input_binding(
    expected_source_sha256: Any,
    page_count: int | None,
    pipeline_binding: dict[str, Any] | None,
) -> dict[str, Any]:
    """建立不含路徑與 endpoint 的 material/run input binding。"""
    material_ref = (
        f"material:sha256:{expected_source_sha256}"
        if _valid_sha256(expected_source_sha256)
        else None
    )
    return {
        "material_ref": material_ref,
        "source_sha256": (
            expected_source_sha256 if _valid_sha256(expected_source_sha256) else None
        ),
        "page_count": page_count,
        "runtime_binding_sha256": (pipeline_binding or {}).get(
            "runtime_binding_sha256"
        ),
    }


def _run_result(
    *,
    run_id: Any,
    input_binding: dict[str, Any],
    processing: str,
    quality: str,
    decision: str,
    reason_code: str,
    provider_call_counts: dict[str, int],
    cache_hits: dict[str, int],
    page_statuses: list[dict[str, Any]],
    study_material_output: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """建立不洩漏實體路徑、raw model body 或教材中間內容的 run 結果。"""
    return {
        "schema": RUN_SCHEMA,
        "development_only": True,
        "run_id": run_id if isinstance(run_id, str) else None,
        "input_binding": deepcopy(input_binding),
        "processing": processing,
        "quality": quality,
        "decision": decision,
        "reason_code": reason_code,
        "provider_call_counts": deepcopy(provider_call_counts),
        "cache_hits": deepcopy(cache_hits),
        "page_statuses": sorted(
            deepcopy(page_statuses), key=lambda item: item["page_number"]
        ),
        "study_material_output": deepcopy(study_material_output),
    }


def _failure(
    reason_code: str,
    *,
    run_id: Any,
    input_binding: dict[str, Any],
    provider_call_counts: dict[str, int],
    cache_hits: dict[str, int],
    page_statuses: list[dict[str, Any]],
) -> dict[str, Any]:
    """所有 file/model/consumer gap 都使用同一個 terminal failure shape。"""
    return _run_result(
        run_id=run_id,
        input_binding=input_binding,
        processing="failed",
        quality="unsupported",
        decision="reject",
        reason_code=reason_code,
        provider_call_counts=provider_call_counts,
        cache_hits=cache_hits,
        page_statuses=page_statuses,
    )


def _page_status(
    page_number: int,
    page_ref: str | None,
    last_stage: str,
    processing: str,
    quality: str,
    decision: str,
    reason_code: str,
) -> dict[str, Any]:
    """建立不含頁面文字的 per-page 狀態。"""
    return {
        "page_number": page_number,
        "page_ref": page_ref,
        "last_stage": last_stage,
        "processing": processing,
        "quality": quality,
        "decision": decision,
        "reason_code": reason_code,
    }


def _replace_page_status(
    page_statuses: list[dict[str, Any]], status: dict[str, Any]
) -> None:
    """依頁碼替換既有狀態，不建立重複頁。"""
    for index, current in enumerate(page_statuses):
        if current["page_number"] == status["page_number"]:
            page_statuses[index] = status
            return
    page_statuses.append(status)


def _fail_page_stage(
    record: dict[str, Any],
    last_stage: str,
    reason_code: str,
    *,
    run_id: str,
    input_binding: dict[str, Any],
    provider_call_counts: dict[str, int],
    cache_hits: dict[str, int],
    page_statuses: list[dict[str, Any]],
) -> dict[str, Any]:
    """記錄目前頁面的 terminal stage failure，並建立一致的 run failure。"""
    evidence = record["page_evidence"]
    _replace_page_status(
        page_statuses,
        _page_status(
            evidence["page_number"],
            evidence["page_ref"],
            last_stage,
            "failed",
            "unsupported",
            "reject",
            reason_code,
        ),
    )
    return _failure(
        reason_code,
        run_id=run_id,
        input_binding=input_binding,
        provider_call_counts=provider_call_counts,
        cache_hits=cache_hits,
        page_statuses=page_statuses,
    )


def _excluded_page(
    page_evidence: dict[str, Any], last_stage: str, reason_code: str
) -> dict[str, Any]:
    """記錄被排除頁的 Evidence identity，不保存未驗證 model output。"""
    return {
        "page_ref": page_evidence["page_ref"],
        "page_number": page_evidence["page_number"],
        "page_evidence_ref": page_evidence["evidence_ref"],
        "last_stage": last_stage,
        "processing": "failed",
        "quality": "unsupported",
        "decision": "reject",
        "reason_code": reason_code,
    }


def _collect_page_records(
    source_path: Path,
    expected_source_sha256: str,
    page_count: int,
    page_evidence_root: Path,
    *,
    run_id: str,
    input_binding: dict[str, Any],
    provider_call_counts: dict[str, int],
    cache_hits: dict[str, int],
    page_statuses: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """先完成所有頁面的 Evidence 與檔案回讀，再開始任何 model generation。"""
    page_records = []
    for page_number in range(1, page_count + 1):
        evidence = page_evidence._build_page_evidence(
            source_path,
            expected_source_sha256,
            page_number,
            page_evidence_root,
        )
        if evidence.get("status") != "succeeded":
            reason = evidence.get("reason", "PAGE_EVIDENCE_FAILED")
            page_statuses.append(
                _page_status(
                    page_number,
                    None,
                    "page_evidence",
                    "failed",
                    "unsupported",
                    "reject",
                    reason,
                )
            )
            return page_records, _failure(
                reason,
                run_id=run_id,
                input_binding=input_binding,
                provider_call_counts=provider_call_counts,
                cache_hits=cache_hits,
                page_statuses=page_statuses,
            )
        native_page, render_bytes, reason = page_evidence._load_page_artifacts(
            page_evidence_root, evidence
        )
        if reason is not None:
            page_statuses.append(
                _page_status(
                    page_number,
                    evidence.get("page_ref"),
                    "page_evidence",
                    "failed",
                    "unsupported",
                    "reject",
                    reason,
                )
            )
            return page_records, _failure(
                reason,
                run_id=run_id,
                input_binding=input_binding,
                provider_call_counts=provider_call_counts,
                cache_hits=cache_hits,
                page_statuses=page_statuses,
            )
        page_records.append(
            {
                "page_evidence": evidence,
                "native_page": native_page,
                "render_bytes": render_bytes,
            }
        )
        page_statuses.append(
            _page_status(
                page_number,
                evidence["page_ref"],
                "page_evidence",
                "succeeded",
                "accepted",
                "retain",
                "PAGE_EVIDENCE_READY",
            )
        )
    return page_records, None


def _understand_pages(
    page_records: list[dict[str, Any]],
    local_config: dict[str, Any],
    *,
    run_id: str,
    input_binding: dict[str, Any],
    provider_call_counts: dict[str, int],
    cache_hits: dict[str, int],
    page_statuses: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """逐頁產生 Page Structure，並完成 native 與視覺 alignment Gate。"""
    for index, record in enumerate(page_records):
        nearby_pages = []
        for nearby_index in (index - 1, index + 1):
            if 0 <= nearby_index < len(page_records):
                nearby = page_records[nearby_index]
                nearby_pages.append(
                    {
                        "page_evidence": nearby["page_evidence"],
                        "render_bytes": nearby["render_bytes"],
                    }
                )
        generation = generate_development_page_structure(
            record["page_evidence"],
            record["render_bytes"],
            local_config,
            nearby_pages,
        )
        _record_generation(
            "page_structure", generation, provider_call_counts, cache_hits
        )
        if generation.get("processing") != "succeeded":
            reason = generation.get("reason_code", "PAGE_STRUCTURE_FAILED")
            if reason == "PAGE_STRUCTURE_INVALID":
                record["excluded_page"] = _excluded_page(
                    record["page_evidence"], "page_structure", reason
                )
                _replace_page_status(
                    page_statuses,
                    _page_status(
                        record["page_evidence"]["page_number"],
                        record["page_evidence"]["page_ref"],
                        "page_structure",
                        "failed",
                        "unsupported",
                        "reject",
                        reason,
                    ),
                )
                continue
            return _fail_page_stage(
                record,
                "page_structure",
                reason,
                run_id=run_id,
                input_binding=input_binding,
                provider_call_counts=provider_call_counts,
                cache_hits=cache_hits,
                page_statuses=page_statuses,
            )
        page_structure = generation["artifact"]
        alignment = assess_page_structure_alignment(
            page_structure, record["page_evidence"], record["native_page"]
        )
        if alignment.get("processing") != "succeeded":
            reason = alignment.get("reason_code", "PAGE_ALIGNMENT_FAILED")
            return _fail_page_stage(
                record,
                "visual_alignment",
                reason,
                run_id=run_id,
                input_binding=input_binding,
                provider_call_counts=provider_call_counts,
                cache_hits=cache_hits,
                page_statuses=page_statuses,
            )
        if alignment.get("quality") == "needs_review":
            visual = generate_visual_review(
                record["page_evidence"],
                page_structure,
                alignment,
                record["render_bytes"],
                local_config,
            )
            _record_generation(
                "visual_alignment_adjudication",
                visual,
                provider_call_counts,
                cache_hits,
            )
            if visual.get("processing") != "succeeded":
                reason = visual.get("reason_code", "VISUAL_ALIGNMENT_FAILED")
                return _fail_page_stage(
                    record,
                    "visual_alignment",
                    reason,
                    run_id=run_id,
                    input_binding=input_binding,
                    provider_call_counts=provider_call_counts,
                    cache_hits=cache_hits,
                    page_statuses=page_statuses,
                )
            alignment = visual["artifact"]
            if alignment.get("decision") != "retain":
                reason = alignment.get(
                    "reason_code", "VISUAL_ALIGNMENT_REVIEW_REJECTED"
                )
                record["excluded_page"] = _excluded_page(
                    record["page_evidence"], "visual_alignment", reason
                )
                _replace_page_status(
                    page_statuses,
                    _page_status(
                        record["page_evidence"]["page_number"],
                        record["page_evidence"]["page_ref"],
                        "visual_alignment",
                        "failed",
                        "unsupported",
                        "reject",
                        reason,
                    ),
                )
                continue
        if (
            alignment.get("quality") != "accepted"
            or alignment.get("decision") != "retain"
        ):
            return _fail_page_stage(
                record,
                "visual_alignment",
                "PAGE_ALIGNMENT_NOT_ACCEPTED",
                run_id=run_id,
                input_binding=input_binding,
                provider_call_counts=provider_call_counts,
                cache_hits=cache_hits,
                page_statuses=page_statuses,
            )
        record["page_structure"] = page_structure
        record["alignment"] = alignment
        _replace_page_status(
            page_statuses,
            _page_status(
                record["page_evidence"]["page_number"],
                record["page_evidence"]["page_ref"],
                "visual_alignment",
                "succeeded",
                "accepted",
                "retain",
                "PAGE_ALIGNMENT_READY",
            ),
        )
    return None


def _generate_concept_candidates(
    accepted_page_records: list[dict[str, Any]],
    local_config: dict[str, Any],
    *,
    run_id: str,
    input_binding: dict[str, Any],
    provider_call_counts: dict[str, int],
    cache_hits: dict[str, int],
    page_statuses: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any] | None]:
    """從已接受頁面的 heading 產生 Evidence-bound Concept candidates。"""
    candidates = []
    unavailable_page_refs = []
    for record in accepted_page_records:
        page_structure = record["page_structure"]
        elements_by_id = {
            element["id"]: element for element in page_structure["elements"]
        }
        heading_ids = [
            element_id
            for element_id in page_structure["reading_order"]
            if elements_by_id[element_id]["type"] == "heading"
        ]
        contexts = [
            build_concept_context(
                page_structure,
                record["page_evidence"],
                record["alignment"],
                heading_id,
            )
            for heading_id in heading_ids
        ]
        if not contexts or any(context is None for context in contexts):
            unavailable_page_refs.append(record["page_evidence"]["page_ref"])
            _replace_page_status(
                page_statuses,
                _page_status(
                    record["page_evidence"]["page_number"],
                    record["page_evidence"]["page_ref"],
                    "concept",
                    "partial",
                    "needs_review",
                    "review",
                    CONCEPT_CONTEXT_UNAVAILABLE,
                ),
            )
            continue
        page_candidates = []
        for context in contexts:
            generation = generate_concept_candidate(
                context, local_config, generation_run_id=run_id
            )
            _record_generation(
                "concept_candidate", generation, provider_call_counts, cache_hits
            )
            if generation.get("processing") != "succeeded":
                reason = generation.get("reason_code", "CONCEPT_GENERATION_FAILED")
                return candidates, unavailable_page_refs, _fail_page_stage(
                    record,
                    "concept",
                    reason,
                    run_id=run_id,
                    input_binding=input_binding,
                    provider_call_counts=provider_call_counts,
                    cache_hits=cache_hits,
                    page_statuses=page_statuses,
                )
            page_candidates.append(generation["artifact"])
        candidates.extend(page_candidates)
        _replace_page_status(
            page_statuses,
            _page_status(
                record["page_evidence"]["page_number"],
                record["page_evidence"]["page_ref"],
                "concept",
                "succeeded",
                "accepted",
                "retain",
                "CONCEPTS_READY",
            ),
        )
    if not candidates:
        return candidates, unavailable_page_refs, _failure(
            CONCEPT_CONTEXT_UNAVAILABLE,
            run_id=run_id,
            input_binding=input_binding,
            provider_call_counts=provider_call_counts,
            cache_hits=cache_hits,
            page_statuses=page_statuses,
        )
    return candidates, unavailable_page_refs, None


def _generate_concept_content_items(
    candidates: list[dict[str, Any]],
    local_config: dict[str, Any],
    *,
    run_id: str,
    input_binding: dict[str, Any],
    provider_call_counts: dict[str, int],
    cache_hits: dict[str, int],
    page_statuses: list[dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any] | None,
]:
    """分組後產生摘要、relation clues 與 deterministic keywords。"""
    groups = group_concept_candidates(candidates)
    if not groups:
        return [], [], [], _failure(
            "CONCEPT_GROUPING_FAILED",
            run_id=run_id,
            input_binding=input_binding,
            provider_call_counts=provider_call_counts,
            cache_hits=cache_hits,
            page_statuses=page_statuses,
        )
    content_items = []
    keyword_items = []
    for start in range(0, len(groups), MAX_CONTENT_GROUPS):
        batch = groups[start : start + MAX_CONTENT_GROUPS]
        summary_context = build_summary_context(batch)
        if summary_context is None:
            return groups, content_items, keyword_items, _failure(
                "CONCEPT_CONTENT_CONTEXT_INVALID",
                run_id=run_id,
                input_binding=input_binding,
                provider_call_counts=provider_call_counts,
                cache_hits=cache_hits,
                page_statuses=page_statuses,
            )
        generation = generate_concept_content(summary_context, local_config)
        _record_generation(
            "concept_content", generation, provider_call_counts, cache_hits
        )
        if generation.get("processing") != "succeeded":
            reason = generation.get("reason_code", "CONCEPT_CONTENT_FAILED")
            return groups, content_items, keyword_items, _failure(
                reason,
                run_id=run_id,
                input_binding=input_binding,
                provider_call_counts=provider_call_counts,
                cache_hits=cache_hits,
                page_statuses=page_statuses,
            )
        keywords = build_concept_keywords(batch)
        if keywords.get("processing") != "succeeded":
            return groups, content_items, keyword_items, _failure(
                keywords.get("reason_code", "CONCEPT_KEYWORDS_FAILED"),
                run_id=run_id,
                input_binding=input_binding,
                provider_call_counts=provider_call_counts,
                cache_hits=cache_hits,
                page_statuses=page_statuses,
            )
        content_items.append(generation["artifact"])
        keyword_items.append(keywords)
    return groups, content_items, keyword_items, None


def _build_study_material_result(
    page_records: list[dict[str, Any]],
    accepted_page_records: list[dict[str, Any]],
    unavailable_page_refs: list[str],
    groups: list[dict[str, Any]],
    content_items: list[dict[str, Any]],
    keyword_items: list[dict[str, Any]],
    *,
    run_id: str,
    produced_at: str,
    input_binding: dict[str, Any],
    provider_call_counts: dict[str, int],
    cache_hits: dict[str, int],
    page_statuses: list[dict[str, Any]],
) -> dict[str, Any]:
    """保存已通過 Gate 的內容，並保留所有已確認的頁面限制。"""
    accepted_page_refs = [
        record["page_evidence"]["page_ref"]
        for record in accepted_page_records
    ]
    page_limitations = [
        {
            "reason_code": FORMAL_PROVIDER_DEFERRED,
            "affected_page_refs": accepted_page_refs,
        }
    ]
    excluded_page_items = [
        record["excluded_page"]
        for record in page_records
        if "excluded_page" in record
    ]
    if excluded_page_items:
        page_limitations.append(
            {
                "reason_code": PAGE_CONTENT_EXCLUDED,
                "affected_pages": sorted(
                    excluded_page_items,
                    key=lambda item: item["page_number"],
                ),
            }
        )
    if unavailable_page_refs:
        page_limitations.append(
            {
                "reason_code": CONCEPT_CONTEXT_UNAVAILABLE,
                "affected_page_refs": unavailable_page_refs,
            }
        )
    output = build_study_material_output(
        [record["page_evidence"] for record in accepted_page_records],
        [record["page_structure"] for record in accepted_page_records],
        groups,
        content_items,
        keyword_items,
        handoff_id=run_id,
        produced_at=produced_at,
        page_limitations=page_limitations,
        provenance={
            "page_evidence": "page-evidence/v1",
            "page_structure": (
                "page-structure/v1;structured-generation-loopback/v1"
            ),
            "concepts": "concept-context/v1;concept-candidate/v1;concept-group/v1",
            "content": (
                f"{CONCEPT_CONTENT_SCHEMA};{CONCEPT_CONTENT_PROMPT_VERSION};"
                "concept-keywords/v1"
            ),
        },
    )
    if output.get("processing") == "failed":
        return _failure(
            "STUDY_MATERIAL_OUTPUT_ROOT_INVALID",
            run_id=run_id,
            input_binding=input_binding,
            provider_call_counts=provider_call_counts,
            cache_hits=cache_hits,
            page_statuses=page_statuses,
        )
    return _run_result(
        run_id=run_id,
        input_binding=input_binding,
        processing=output["processing"],
        quality=output["quality"],
        decision=output["decision"],
        reason_code=output["reason_code"],
        provider_call_counts=provider_call_counts,
        cache_hits=cache_hits,
        page_statuses=page_statuses,
        study_material_output=output,
    )


def _run_development_pdf_snapshot(
    pdf_path: str | os.PathLike[str],
    expected_source_sha256: str,
    output_root: str | os.PathLike[str],
    local_config: dict[str, Any],
    *,
    run_id: str,
    produced_at: str,
    page_limit: int,
    pipeline_binding: dict[str, Any] | None,
) -> dict[str, Any]:
    """只從已驗 hash 的 private snapshot 執行完整 development PDF pipeline。"""
    provider_call_counts = _empty_metrics()
    cache_hits = _empty_metrics()
    page_statuses: list[dict[str, Any]] = []
    source_path, page_count, reason = _source_page_count(
        pdf_path, expected_source_sha256, page_limit
    )
    input_binding = _input_binding(
        expected_source_sha256, page_count, pipeline_binding
    )
    if reason is not None:
        return _failure(
            reason,
            run_id=run_id,
            input_binding=input_binding,
            provider_call_counts=provider_call_counts,
            cache_hits=cache_hits,
            page_statuses=page_statuses,
        )
    endpoint = transport._loopback_endpoint(
        local_config["endpoint_url"]
    )
    if endpoint is None:
        return _failure(
            "LOCAL_ENDPOINT_NOT_LOOPBACK",
            run_id=run_id,
            input_binding=input_binding,
            provider_call_counts=provider_call_counts,
            cache_hits=cache_hits,
            page_statuses=page_statuses,
        )
    local_config = {**local_config, "endpoint_url": endpoint}
    try:
        os.fsencode(output_root)
        raw_root = Path(output_root)
        root = Path(os.path.abspath(raw_root))
    except (OSError, TypeError, UnicodeError, ValueError):
        raw_root = None
        root = None
    if (
        root is None
        or "\x00" in str(raw_root)
        or ".." in raw_root.parts
        or page_evidence._path_has_symlink(root)
        or (root.exists() and not root.is_dir())
    ):
        return _failure(
            "OUTPUT_ROOT_INVALID",
            run_id=run_id,
            input_binding=input_binding,
            provider_call_counts=provider_call_counts,
            cache_hits=cache_hits,
            page_statuses=page_statuses,
        )
    page_evidence_root = root / "page_evidence"
    if not page_evidence._valid_page_evidence_storage(page_evidence_root):
        return _failure(
            "OUTPUT_ROOT_INVALID",
            run_id=run_id,
            input_binding=input_binding,
            provider_call_counts=provider_call_counts,
            cache_hits=cache_hits,
            page_statuses=page_statuses,
        )

    page_records, failure = _collect_page_records(
        source_path,
        expected_source_sha256,
        page_count,
        page_evidence_root,
        run_id=run_id,
        input_binding=input_binding,
        provider_call_counts=provider_call_counts,
        cache_hits=cache_hits,
        page_statuses=page_statuses,
    )
    if failure is not None:
        return failure

    failure = _understand_pages(
        page_records,
        local_config,
        run_id=run_id,
        input_binding=input_binding,
        provider_call_counts=provider_call_counts,
        cache_hits=cache_hits,
        page_statuses=page_statuses,
    )
    if failure is not None:
        return failure

    accepted_page_records = [
        record for record in page_records if "excluded_page" not in record
    ]
    if not accepted_page_records:
        return _failure(
            page_records[0]["excluded_page"]["reason_code"],
            run_id=run_id,
            input_binding=input_binding,
            provider_call_counts=provider_call_counts,
            cache_hits=cache_hits,
            page_statuses=page_statuses,
        )

    candidates, unavailable_page_refs, failure = _generate_concept_candidates(
        accepted_page_records,
        local_config,
        run_id=run_id,
        input_binding=input_binding,
        provider_call_counts=provider_call_counts,
        cache_hits=cache_hits,
        page_statuses=page_statuses,
    )
    if failure is not None:
        return failure

    groups, content_items, keyword_items, failure = _generate_concept_content_items(
        candidates,
        local_config,
        run_id=run_id,
        input_binding=input_binding,
        provider_call_counts=provider_call_counts,
        cache_hits=cache_hits,
        page_statuses=page_statuses,
    )
    if failure is not None:
        return failure

    return _build_study_material_result(
        page_records,
        accepted_page_records,
        unavailable_page_refs,
        groups,
        content_items,
        keyword_items,
        run_id=run_id,
        produced_at=produced_at,
        input_binding=input_binding,
        provider_call_counts=provider_call_counts,
        cache_hits=cache_hits,
        page_statuses=page_statuses,
    )


def run_development_pdf(
    pdf_path: str | os.PathLike[str],
    expected_source_sha256: str,
    output_root: str | os.PathLike[str],
    local_config: dict[str, Any],
    *,
    run_id: str,
    produced_at: str,
    page_limit: int,
) -> dict[str, Any]:
    """由任意合法 development PDF 建立 validator-accepted Study Material Output。"""
    provider_call_counts = _empty_metrics()
    cache_hits = _empty_metrics()
    pipeline_binding = development_pipeline_binding(local_config)
    input_binding = _input_binding(expected_source_sha256, None, pipeline_binding)
    if (
        not isinstance(run_id, str)
        or not run_id.strip()
        or not isinstance(produced_at, str)
        or not produced_at.strip()
    ):
        return _failure(
            "RUN_INPUT_INVALID",
            run_id=run_id,
            input_binding=input_binding,
            provider_call_counts=provider_call_counts,
            cache_hits=cache_hits,
            page_statuses=[],
        )
    if (
        not _valid_sha256(expected_source_sha256)
        or isinstance(page_limit, bool)
        or not isinstance(page_limit, int)
        or page_limit < 1
    ):
        return _failure(
            "MATERIAL_INPUT_INVALID",
            run_id=run_id,
            input_binding=input_binding,
            provider_call_counts=provider_call_counts,
            cache_hits=cache_hits,
            page_statuses=[],
        )
    if pipeline_binding is None:
        return _failure(
            "LOCAL_CONFIG_INVALID",
            run_id=run_id,
            input_binding=input_binding,
            provider_call_counts=provider_call_counts,
            cache_hits=cache_hits,
            page_statuses=[],
        )
    temporary_directory = None
    snapshot_path = None
    try:
        temporary_directory = tempfile.TemporaryDirectory(
            prefix="studydy-pdf-source-"
        )
        snapshot_path = Path(temporary_directory.name) / "source.pdf"
        reason = _copy_source_snapshot(pdf_path, snapshot_path)
        if reason is not None:
            result = _failure(
                reason,
                run_id=run_id,
                input_binding=input_binding,
                provider_call_counts=provider_call_counts,
                cache_hits=cache_hits,
                page_statuses=[],
            )
        else:
            result = _run_development_pdf_snapshot(
                snapshot_path,
                expected_source_sha256,
                output_root,
                local_config,
                run_id=run_id,
                produced_at=produced_at,
                page_limit=page_limit,
                pipeline_binding=pipeline_binding,
            )
    except (OSError, ValueError):
        result = _failure(
            "MATERIAL_READ_FAILED",
            run_id=run_id,
            input_binding=input_binding,
            provider_call_counts=provider_call_counts,
            cache_hits=cache_hits,
            page_statuses=[],
        )
    except Exception:
        _cleanup_source_snapshot(snapshot_path, temporary_directory)
        raise
    if not _cleanup_source_snapshot(snapshot_path, temporary_directory):
        return _failure(
            "MATERIAL_SNAPSHOT_CLEANUP_FAILED",
            run_id=run_id,
            input_binding=result["input_binding"],
            provider_call_counts=result["provider_call_counts"],
            cache_hits=result["cache_hits"],
            page_statuses=result["page_statuses"],
        )
    return result
