from __future__ import annotations

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
    start_relation_process,
)
from pdf_evidence.ocr_page_evidence import canonical_sha256

from .artifacts import build_knowledge_map
from .formal_concepts import (
    build_resolution_requests,
    validate_resolution,
)
from .relations import (
    build_relation_artifact,
    has_structural_relation_evidence,
    relation_premise,
    select_relation_pairs,
)


_RESOLUTION_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "studydy_formal_concept_resolution",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["schema", "group_id", "resolutions"],
            "properties": {
                "schema": {"const": "formal-concept-resolution/v1"},
                "group_id": {"type": "string"},
                "resolutions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["operation", "source_ids", "nodes"],
                        "properties": {
                            "operation": {"enum": ["KEEP", "MERGE", "RENAME", "SPLIT", "DROP"]},
                            "source_ids": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                            "nodes": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": ["label", "claim_ids"],
                                    "properties": {
                                        "label": {"type": "string", "minLength": 1},
                                        "claim_ids": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
    },
}
def _resolution_format(request: dict[str, Any]) -> dict[str, Any]:
    """讓 structured output 只能選目前群組實際存在的 alias。"""

    candidate_aliases = [candidate["id"] for candidate in request["candidates"]]
    claim_aliases = [
        claim["id"]
        for candidate in request["candidates"]
        for claim in candidate["claims"]
    ]
    response_format = deepcopy(_RESOLUTION_FORMAT)
    schema = response_format["json_schema"]["schema"]
    schema["properties"]["group_id"] = {"const": request["group_id"]}
    resolutions = schema["properties"]["resolutions"]
    resolutions["minItems"] = 1
    resolutions["maxItems"] = len(candidate_aliases)
    resolution = resolutions["items"]["properties"]
    resolution["source_ids"]["maxItems"] = len(candidate_aliases)
    resolution["source_ids"]["items"] = {"enum": candidate_aliases}
    resolution["nodes"]["maxItems"] = 2
    resolution["nodes"]["items"]["properties"]["claim_ids"]["maxItems"] = len(
        claim_aliases
    )
    resolution["nodes"]["items"]["properties"]["claim_ids"]["items"] = {
        "enum": claim_aliases
    }
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
    resolution_artifacts = []
    server = None
    try:
        server = start_concept_server(settings)
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            for request, concept_aliases, claim_aliases in build_resolution_requests(source_concepts):
                model_text = _request_stage(
                    client,
                    settings,
                    runtime_lock["formal_resolution"],
                    request,
                    _resolution_format(request),
                )
                resolution_artifacts.append(
                    validate_resolution(
                        _json_document(model_text),
                        request=request,
                        concept_aliases=concept_aliases,
                        claim_aliases=claim_aliases,
                        source_concepts=source_concepts,
                    )
                )
    except (ConceptAPIError, KeyError, TypeError, ValueError):
        raise ValueError("KNOWLEDGE_GENERATION_FAILED") from None
    finally:
        if server is not None:
            server.close()

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
