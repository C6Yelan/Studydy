from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
import json
from typing import Any

import httpx

from learning_resources.map_resources import promote_resources_to_formal_concepts

from pdf_evidence.concept_api import (
    ConceptAPIError,
    request_structured_text,
    start_concept_server,
)
from pdf_evidence.local_ai_process import (
    LocalAIError,
    LocalAIProcess,
    start_equivalence_process,
    start_relation_process,
)
from pdf_evidence.ocr_page_evidence import canonical_sha256
from pdf_evidence.artifact_reason_codes import formal_reason_code

from .artifacts import build_knowledge_map
from .formal_concepts import (
    DEDUPLICATION_OUTPUT_SCHEMA,
    FormalConceptError,
    build_deduplication_request,
    build_verifier_texts,
    canonicalize_concepts,
    uncertain_pair_decisions,
    validate_pair_decisions,
)
from .relations import (
    build_relation_artifact,
    has_structural_relation_evidence,
    relation_premise,
    select_relation_pairs,
)


_DEDUPLICATION_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "studydy_concept_deduplication",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["schema", "pairs"],
            "properties": {
                "schema": {"const": "concept-deduplication/v1"},
                "pairs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["id", "decision"],
                        "properties": {
                            "id": {"type": "string"},
                            "decision": {
                                "enum": ["SAME", "DISTINCT", "UNCERTAIN"]
                            },
                        },
                    },
                },
            },
        },
    },
}
def _deduplication_format(request: dict[str, Any]) -> dict[str, Any]:
    """讓 structured output 只能回覆目前送出的 pair ID。"""

    pair_ids = [pair["id"] for pair in request["pairs"]]
    response_format = deepcopy(_DEDUPLICATION_FORMAT)
    schema = response_format["json_schema"]["schema"]
    pairs = schema["properties"]["pairs"]
    pairs["minItems"] = len(pair_ids)
    pairs["maxItems"] = len(pair_ids)
    pairs["items"]["properties"]["id"] = {"enum": pair_ids}
    return response_format


def _json_document(model_text: str) -> dict[str, Any]:
    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        document: dict[str, Any] = {}
        for key, value in pairs:
            if key in document:
                raise ValueError("MODEL_OUTPUT_INVALID")
            document[key] = value
        return document

    try:
        document = json.loads(model_text, object_pairs_hook=no_duplicates)
    except (RecursionError, ValueError):
        raise ValueError("MODEL_OUTPUT_INVALID") from None
    if not isinstance(document, dict):
        raise ValueError("MODEL_OUTPUT_INVALID")
    return document


def _request_stage(
    client: httpx.Client,
    settings: dict[str, Any],
    stage: dict[str, Any],
    request: dict[str, Any],
    response_format: dict[str, Any],
) -> str:
    """只對 runtime lock 明列的暫時性本機 API 失敗重試一次。"""

    retry = stage["retry"]
    for attempt in range(1, retry["max_attempts"] + 1):
        try:
            return request_structured_text(
                client,
                base_url=settings["concept_api_base_url"],
                model=settings["concept_model"],
                prompt_template=stage["prompt"],
                request_document=request,
                response_format=response_format,
                max_model_len=settings["concept_max_model_len"],
                max_tokens=stage["generation"]["max_tokens"],
                timeout_seconds=stage["timeout_seconds"],
                enable_thinking=False,
            )
        except ConceptAPIError as error:
            if (
                attempt == retry["max_attempts"]
                or error.reason_code not in retry["retryable_reasons"]
            ):
                raise
    raise ConceptAPIError("CONCEPT_API_UNAVAILABLE")


def _verification_diagnostics(
    proposals: list[dict[str, str]],
) -> dict[str, int]:
    return {
        "qwen_same_pairs": sum(pair["decision"] == "SAME" for pair in proposals),
        "qwen_distinct_pairs": sum(
            pair["decision"] == "DISTINCT" for pair in proposals
        ),
        "qwen_uncertain_pairs": sum(
            pair["decision"] == "UNCERTAIN" for pair in proposals
        ),
        "verifier_requested_pairs": 0,
        "verifier_scored_pairs": 0,
        "verifier_allowed_pairs": 0,
        "verifier_vetoed_pairs": 0,
        "verifier_unsupported_pairs": 0,
        "verifier_failed_pairs": 0,
    }


def _direction_is_valid(direction: Any) -> bool:
    return (
        isinstance(direction, dict)
        and set(direction)
        == {"entailment_probability", "argmax_label", "token_length"}
        and type(direction["entailment_probability"]) is float
        and 0 <= direction["entailment_probability"] <= 1
        and direction["argmax_label"]
        in {"entailment", "neutral", "contradiction"}
        and type(direction["token_length"]) is int
        and 1 <= direction["token_length"] <= 384
    )


def _verify_same_pairs(
    request: dict[str, Any],
    proposals: list[dict[str, str]],
    verifier_texts: dict[str, str],
    settings: dict[str, Any],
) -> tuple[list[dict[str, str]], dict[str, int], str | None]:
    """只有 Qwen SAME 進入固定雙向 verifier；任何失敗都保留來源。"""

    diagnostics = _verification_diagnostics(proposals)
    same_ids = {pair["id"] for pair in proposals if pair["decision"] == "SAME"}
    diagnostics["verifier_requested_pairs"] = len(same_ids)
    if not same_ids:
        return proposals, diagnostics, None
    pairs_by_id = {pair["id"]: pair for pair in request["pairs"]}
    process = None
    final_decisions = []
    timeout_seconds = settings["runtime_lock"]["concept_equivalence"][
        "timeout_seconds"
    ]
    try:
        process = start_equivalence_process(settings, timeout_seconds)
        for proposal in proposals:
            if proposal["decision"] != "SAME":
                final_decisions.append(deepcopy(proposal))
                continue
            pair = pairs_by_id[proposal["id"]]
            request_id = canonical_sha256({
                "request_sha256": canonical_sha256(request),
                "pair_id": pair["id"],
            })
            try:
                response = process.request(
                    {
                        "schema": "local-concept-equivalence-request/v1",
                        "request_id": request_id,
                        "left_text": verifier_texts[pair["left"]],
                        "right_text": verifier_texts[pair["right"]],
                    },
                    timeout_seconds,
                )
            except LocalAIError as error:
                if error.reason_code == "CHILD_TIMEOUT":
                    raise LocalAIError(
                        "CONCEPT_EQUIVALENCE_VERIFIER_TIMEOUT"
                    ) from None
                if error.reason_code == "CHILD_RESPONSE_INVALID":
                    raise LocalAIError(
                        "CONCEPT_EQUIVALENCE_VERIFIER_RESPONSE_INVALID"
                    ) from None
                raise LocalAIError(
                    "CONCEPT_EQUIVALENCE_VERIFIER_UNAVAILABLE"
                ) from None
            if (
                isinstance(response, dict)
                and set(response)
                == {"schema", "request_id", "status", "reason_code", "token_lengths"}
                and response.get("schema")
                == "local-concept-equivalence-response/v1"
                and response.get("request_id") == request_id
                and response.get("status") == "unsupported"
                and response.get("reason_code") == "VERIFIER_INPUT_TOO_LARGE"
                and isinstance(response.get("token_lengths"), list)
                and len(response["token_lengths"]) == 2
                and all(
                    type(length) is int and length > 0
                    for length in response["token_lengths"]
                )
                and any(length > 384 for length in response["token_lengths"])
            ):
                diagnostics["verifier_unsupported_pairs"] += 1
                final_decisions.append({"id": pair["id"], "decision": "UNCERTAIN"})
                continue
            if (
                not isinstance(response, dict)
                or set(response)
                != {"schema", "request_id", "status", "a_to_b", "b_to_a"}
                or response.get("schema")
                != "local-concept-equivalence-response/v1"
                or response.get("request_id") != request_id
                or response.get("status") != "scored"
                or not _direction_is_valid(response.get("a_to_b"))
                or not _direction_is_valid(response.get("b_to_a"))
            ):
                raise LocalAIError(
                    "CONCEPT_EQUIVALENCE_VERIFIER_RESPONSE_INVALID"
                )
            diagnostics["verifier_scored_pairs"] += 1
            allowed = all(
                direction["argmax_label"] == "entailment"
                and direction["entailment_probability"] >= 0.8
                for direction in (response["a_to_b"], response["b_to_a"])
            )
            diagnostics[
                "verifier_allowed_pairs" if allowed else "verifier_vetoed_pairs"
            ] += 1
            final_decisions.append({
                "id": pair["id"],
                "decision": "SAME" if allowed else "UNCERTAIN",
            })
        process.close()
        process = None
        return final_decisions, diagnostics, None
    except LocalAIError as error:
        if process is not None:
            process.abort()
        diagnostics.update({
            "verifier_scored_pairs": 0,
            "verifier_allowed_pairs": 0,
            "verifier_vetoed_pairs": 0,
            "verifier_unsupported_pairs": 0,
            "verifier_failed_pairs": len(same_ids),
        })
        return [
            {
                "id": proposal["id"],
                "decision": (
                    "UNCERTAIN" if proposal["decision"] == "SAME"
                    else proposal["decision"]
                ),
            }
            for proposal in proposals
        ], diagnostics, error.reason_code


def _verify_relation(
    process: LocalAIProcess,
    relation_type: str,
    source: dict[str, Any],
    target: dict[str, Any],
    timeout_seconds: float,
) -> bool:
    request_id = canonical_sha256(
        {
            "type": relation_type,
            "source": source["formal_concept_id"],
            "target": target["formal_concept_id"],
        }
    )
    try:
        response = process.request(
            {
                "schema": "local-relation-verifier-request/v1",
                "request_id": request_id,
                "relation_type": relation_type,
                "premise": relation_premise(source, target),
            },
            timeout_seconds,
        )
    except LocalAIError as error:
        if error.reason_code == "CHILD_TIMEOUT":
            raise LocalAIError("RELATION_VERIFIER_TIMEOUT") from None
        if error.reason_code == "CHILD_RESPONSE_INVALID":
            raise LocalAIError("RELATION_VERIFIER_RESPONSE_INVALID") from None
        raise LocalAIError("RELATION_VERIFIER_UNAVAILABLE") from None
    if (
        set(response) != {"schema", "request_id", "outcome"}
        or response.get("schema") != "local-relation-verifier-response/v1"
        or response.get("request_id") != request_id
        or response.get("outcome") not in {"entailed", "not_entailed"}
    ):
        raise LocalAIError("RELATION_VERIFIER_RESPONSE_INVALID")
    return response["outcome"] == "entailed"


def _build_relation_artifacts(
    batches: list[list[tuple[str, str]]],
    formal_concepts: list[dict[str, Any]],
    evidence_pages: dict[str, str],
    settings: dict[str, Any],
) -> list[dict[str, Any]]:
    """Verifier 任一 startup/runtime failure 都重建為無 structural edge。"""

    needs_verifier = any(
        has_structural_relation_evidence(pairs, formal_concepts)
        for pairs in batches
    )
    relation_process = None
    timeout_seconds = settings["runtime_lock"]["relation_verifier"][
        "timeout_seconds"
    ]
    try:
        if needs_verifier:
            relation_process = start_relation_process(settings, timeout_seconds)
        verifier = (
            None
            if relation_process is None
            else lambda relation_type, source, target: _verify_relation(
                relation_process,
                relation_type,
                source,
                target,
                timeout_seconds,
            )
        )
        artifacts = [
            build_relation_artifact(
                pairs, formal_concepts, evidence_pages, verifier
            )
            for pairs in batches
        ]
        if relation_process is not None:
            relation_process.close()
            relation_process = None
        return artifacts
    except LocalAIError as error:
        if relation_process is not None:
            relation_process.abort()
        known_reasons = {
            "RELATION_VERIFIER_DEPENDENCY_MISSING",
            "RELATION_VERIFIER_CUDA_UNAVAILABLE",
            "RELATION_VERIFIER_MODEL_LOAD_FAILED",
            "RELATION_VERIFIER_TIMEOUT",
            "RELATION_VERIFIER_RESPONSE_INVALID",
        }
        failure_reason = (
            error.reason_code
            if error.reason_code in known_reasons
            else "RELATION_VERIFIER_UNAVAILABLE"
        )
        return [
            build_relation_artifact(
                pairs,
                formal_concepts,
                evidence_pages,
                None,
                verifier_failure_reason=failure_reason,
            )
            for pairs in batches
        ]


def generate_knowledge_map(
    study_material_output: dict[str, Any],
    settings: dict[str, Any],
    material_runtime_binding_sha256: str,
    *,
    resource_context: dict[str, Any],
    resource_library: dict[str, Any],
) -> dict[str, Any]:
    """同一次本機 Qwen lifecycle 完成 Resolution 與 Relation candidates。"""

    runtime_lock = settings["runtime_lock"]
    source_concepts = study_material_output["concepts"]
    if not source_concepts:
        resource_promotion = promote_resources_to_formal_concepts(
            [], resource_context, study_material_output, resource_library
        )
        return build_knowledge_map(
            study_material_output,
            [],
            [],
            relation_pair_status={
                "processing": "partial",
                "quality": "needs_review",
                "decision": "review",
                "reason_codes": ["NO_FORMAL_CONCEPT"],
            },
            resource_promotion=resource_promotion,
            material_runtime_binding_sha256=material_runtime_binding_sha256,
        )
    deduplication_request, concept_aliases = build_deduplication_request(
        study_material_output
    )
    server = None
    failure_reason = None
    try:
        if deduplication_request["pairs"]:
            server = start_concept_server(settings)
        with httpx.Client(
            trust_env=False, follow_redirects=False
        ) if deduplication_request["pairs"] else nullcontext() as client:
            if deduplication_request["pairs"]:
                assert isinstance(client, httpx.Client)
                model_text = _request_stage(
                    client,
                    settings,
                    runtime_lock["formal_resolution"],
                    deduplication_request,
                    _deduplication_format(deduplication_request),
                )
                pair_decisions = validate_pair_decisions(
                    _json_document(model_text), deduplication_request
                )
            else:
                pair_decisions = []
    except ConceptAPIError as error:
        pair_decisions = uncertain_pair_decisions(deduplication_request)
        failure_reason = formal_reason_code(error.reason_code)
    except (FormalConceptError, KeyError, TypeError, ValueError):
        pair_decisions = uncertain_pair_decisions(deduplication_request)
        failure_reason = "MODEL_OUTPUT_INVALID"
    finally:
        if server is not None:
            server.close()

    if failure_reason is None:
        try:
            pair_decisions, verification_diagnostics, verifier_failure = (
                _verify_same_pairs(
                    deduplication_request,
                    pair_decisions,
                    build_verifier_texts(
                        study_material_output, concept_aliases
                    ),
                    settings,
                )
            )
            failure_reason = verifier_failure
        except (FormalConceptError, KeyError, TypeError, ValueError):
            pair_decisions = uncertain_pair_decisions(deduplication_request)
            verification_diagnostics = _verification_diagnostics(pair_decisions)
            failure_reason = "MODEL_OUTPUT_INVALID"
    else:
        verification_diagnostics = _verification_diagnostics(pair_decisions)

    resolution_artifacts = [
        canonicalize_concepts(
            study_material_output,
            deduplication_request,
            concept_aliases,
            pair_decisions,
            verification_diagnostics=verification_diagnostics,
            failure_reason=failure_reason,
        )
    ]

    resolved_formal_concepts = [
        concept
        for artifact in resolution_artifacts
        for concept in artifact["formal_concepts"]
    ]
    resource_promotion = promote_resources_to_formal_concepts(
        resolved_formal_concepts,
        resource_context,
        study_material_output,
        resource_library,
    )
    formal_concepts = resource_promotion["formal_concepts"]
    page_numbers = {
        page["page_ref"]: page["page_number"]
        for page in study_material_output["pages"]
    }
    batches, pair_status = select_relation_pairs(formal_concepts, page_numbers)
    evidence_pages = {
        evidence["evidence_id"]: evidence["page_ref"]
        for evidence in study_material_output["evidence_index"]
    }
    relation_artifacts = _build_relation_artifacts(
        batches, formal_concepts, evidence_pages, settings
    )
    return build_knowledge_map(
        study_material_output,
        resolution_artifacts,
        relation_artifacts,
        relation_pair_status=pair_status,
        resource_promotion=resource_promotion,
        material_runtime_binding_sha256=material_runtime_binding_sha256,
    )
