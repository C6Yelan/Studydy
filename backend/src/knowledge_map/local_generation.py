from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

import httpx

from pdf_evidence.concept_api import (
    ConceptAPIError,
    request_structured_text,
    start_concept_server,
)

from .artifacts import build_knowledge_map
from .formal_concepts import (
    build_resolution_requests,
    validate_resolution,
)
from .relations import (
    build_relation_request,
    select_relation_pairs,
    validate_relations,
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
_RELATION_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "studydy_formal_relations",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["schema", "pairs"],
            "properties": {
                "schema": {"const": "formal-relations/v1"},
                "pairs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["id", "outcome", "relations"],
                        "properties": {
                            "id": {"type": "string"},
                            "outcome": {"enum": ["relations", "no_relation", "uncertain"]},
                            "relations": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": ["type", "source", "target", "source_evidence_ids", "target_evidence_ids"],
                                    "properties": {
                                        "type": {"enum": ["prerequisite", "contains", "similar", "confusing", "application", "example"]},
                                        "source": {"type": "string"},
                                        "target": {"type": "string"},
                                        "source_evidence_ids": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                                        "target_evidence_ids": {"type": "array", "minItems": 1, "items": {"type": "string"}},
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


def generate_knowledge_map(
    study_material_output: dict[str, Any],
    settings: dict[str, Any],
    material_runtime_binding_sha256: str,
) -> dict[str, Any]:
    """同一次本機 Qwen lifecycle 完成 Resolution 與 Relation candidates。"""

    runtime_lock = settings["runtime_lock"]
    source_concepts = study_material_output["concepts"]
    if not source_concepts:
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
            material_runtime_binding_sha256=material_runtime_binding_sha256,
        )
    resolution_artifacts = []
    relation_artifacts = []
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
            formal_concepts = [
                concept
                for artifact in resolution_artifacts
                for concept in artifact["formal_concepts"]
            ]
            page_numbers = {
                page["page_ref"]: page["page_number"]
                for page in study_material_output["pages"]
            }
            batches, pair_status = select_relation_pairs(formal_concepts, page_numbers)
            evidence_pages = {
                evidence["evidence_id"]: evidence["page_ref"]
                for evidence in study_material_output["evidence_index"]
            }
            for pairs in batches:
                request, concept_aliases, evidence_aliases = build_relation_request(
                    pairs, formal_concepts
                )
                model_text = _request_stage(
                    client,
                    settings,
                    runtime_lock["formal_relation"],
                    request,
                    _RELATION_FORMAT,
                )
                relation_artifacts.append(
                    validate_relations(
                        _json_document(model_text),
                        request=request,
                        concept_aliases=concept_aliases,
                        evidence_aliases=evidence_aliases,
                        formal_concepts=formal_concepts,
                        evidence_pages=evidence_pages,
                    )
                )
    except (ConceptAPIError, KeyError, TypeError, ValueError):
        raise ValueError("KNOWLEDGE_GENERATION_FAILED") from None
    finally:
        if server is not None:
            server.close()
    return build_knowledge_map(
        study_material_output,
        resolution_artifacts,
        relation_artifacts,
        relation_pair_status=pair_status,
        material_runtime_binding_sha256=material_runtime_binding_sha256,
    )
