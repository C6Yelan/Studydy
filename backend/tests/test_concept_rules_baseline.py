from __future__ import annotations

import copy
import hashlib

import pytest

from concept_rules_baseline import build_concept_rule_decisions
from handoff.contract import seal_handoff_draft
from handoff.contract_hashing import record_canonical_sha256
from handoff.package_builder import build_handoff_package
from material_runtime_files import canonical_json_bytes
from presemantic_records_provider import (
    build_presemantic_records_package_input,
)


_MATERIAL_ID = "material-concept-rules-synthetic"


def _unit(unit_id: str, reading_order: int, text: str) -> dict:
    return {
        "layout_unit_id": unit_id,
        "parent_block_id": "block-001",
        "reading_order": reading_order,
        "bbox": [
            0.0,
            float(reading_order * 12),
            100.0,
            float(reading_order * 12 + 10),
        ],
        "kind": "text",
        "text": text,
        "style_summary": {
            "bold": False,
            "font_names": ["Synthetic"],
            "font_size_max": 10.0,
            "font_size_min": 10.0,
            "line_count": 1,
            "monospace": False,
        },
    }


def _normalized(*units: dict) -> dict:
    return {
        "schema_version": "normalized-material-blocks/v2",
        "status": "success",
        "source_provenance": {},
        "materials": [
            {
                "material_id": _MATERIAL_ID,
                "case_id": "case-concept-rules-synthetic",
                "artifact_ref": "synthetic:concept-rules",
                "blocks": [
                    {
                        "material_id": _MATERIAL_ID,
                        "case_id": "case-concept-rules-synthetic",
                        "artifact_ref": "synthetic:concept-rules",
                        "block_id": "block-001",
                        "pdf_page": 1,
                        "source_ref": "synthetic://concept-rules#page=1",
                        "locator": {
                            "pdf_page": 1,
                            "source_ref": (
                                "synthetic://concept-rules#page=1"
                            ),
                        },
                        "page_bbox": [0.0, 0.0, 200.0, 300.0],
                        "provenance": {
                            "native_analysis": {"policy": "synthetic-v2"}
                        },
                        "native_analysis_status": "success",
                        "selection_status": "selected",
                        "reasons": [],
                        "warnings": [],
                        "layout_units": list(units),
                        "layout_unit_omissions": [],
                    }
                ],
            }
        ],
    }


def _package(
    *texts: str,
    evidence_kinds: tuple[str, ...] | None = None,
    hard_negative: bool = False,
) -> tuple[dict, dict]:
    normalized = _normalized(
        *[
            _unit(f"unit-{index:03d}", index, text)
            for index, text in enumerate(texts)
        ]
    )
    envelope = build_presemantic_records_package_input(
        normalized, _MATERIAL_ID
    )
    if evidence_kinds is not None:
        evidence_records = envelope["records_artifact"][
            "evidence_records"
        ]
        assert len(evidence_records) == len(evidence_kinds)
        for evidence, evidence_kind in zip(
            evidence_records, evidence_kinds, strict=True
        ):
            evidence["evidence_kind"] = evidence_kind
            evidence["canonical_sha256"] = record_canonical_sha256(
                evidence
            )
    if hard_negative:
        for candidate in envelope["records_artifact"]["candidates"]:
            candidate["support_summary"]["hard_negative_gate"] = True
            candidate["canonical_sha256"] = record_canonical_sha256(
                candidate
            )
    records = envelope["records_artifact"]
    envelope["deterministic_records_binding"]["raw_sha256"] = (
        hashlib.sha256(canonical_json_bytes(records)).hexdigest()
    )
    return (
        build_handoff_package(envelope),
        envelope["normalized_source"],
    )


def _build(package: dict, normalized_source: dict) -> dict:
    return build_concept_rule_decisions(
        package,
        normalized_source=normalized_source,
    )


def _all_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            keys.add(key)
            keys.update(_all_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(_all_keys(child))
    return keys


def test_exact_pass_definition_retains_with_internal_boundary() -> None:
    package, normalized_source = _package(
        "Alpha is the first bounded synthetic definition.",
        evidence_kinds=("definition",),
    )

    output = _build(package, normalized_source)

    assert output["schema_version"] == "concept-rule-decisions/v1"
    assert output["package_canonical_sha256"] == package[
        "canonical_sha256"
    ]
    assert output["material_id"] == _MATERIAL_ID
    assert output["retained_count"] == 1
    assert output["pending_questions"] == []
    [decision] = output["decisions"]
    assert (decision["route"], decision["outcome"]) == (
        "accepted_by_rules",
        "retain",
    )
    assert decision["reason_codes"] == ["strong_definition_evidence"]
    assert decision["name"] == "Alpha"
    assert decision["normalized_name"] == "alpha"
    assert decision["teaching_scope"] in {
        evidence["statement"] for evidence in package["evidence_records"]
    }
    assert decision["normalized_source_binding"] == package[
        "normalized_source_binding"
    ]
    assert decision["candidate_source_binding"] == package[
        "candidate_source_binding"
    ]


@pytest.mark.parametrize(
    ("evidence_kind", "route", "outcome", "reason_code"),
    [
        (
            "candidate_literal",
            "rejected_by_rules",
            "reject",
            "weak_evidence_only",
        ),
        (
            "projection_literal",
            "rejected_by_rules",
            "reject",
            "weak_evidence_only",
        ),
        (
            "heading",
            "needs_local_model_review",
            "review",
            "strong_evidence_scope_unresolved",
        ),
        (
            "explicit_alias",
            "needs_local_model_review",
            "review",
            "strong_evidence_scope_unresolved",
        ),
    ],
)
def test_approved_evidence_classes_have_only_bounded_routes(
    evidence_kind: str,
    route: str,
    outcome: str,
    reason_code: str,
) -> None:
    package, normalized_source = _package(
        "Alpha bounded synthetic statement.",
        evidence_kinds=(evidence_kind,),
    )

    output = _build(package, normalized_source)

    [decision] = output["decisions"]
    assert (decision["route"], decision["outcome"]) == (route, outcome)
    assert decision["reason_codes"] == [reason_code]
    assert output["retained_count"] == 0
    assert len(output["pending_questions"]) == (outcome == "review")
    if outcome == "review":
        [question] = output["pending_questions"]
        assert question["question_id"].startswith(
            "concept-rule-question:"
        )
        assert question["decision_id"] == decision["decision_id"]
        assert question["reason_codes"] == decision["reason_codes"]
    assert ("concept_id" in decision) == (outcome == "retain")
    assert ("teaching_scope" in decision) == (outcome == "retain")


def test_same_material_exact_normalized_name_aggregates_deterministically() -> None:
    package, normalized_source = _package(
        "Alpha is the bounded synthetic definition.",
        "Ａｌｐｈａ is the bounded synthetic definition.",
        evidence_kinds=("definition", "definition"),
    )

    output = _build(package, normalized_source)

    [decision] = output["decisions"]
    assert decision["outcome"] == "retain"
    assert decision["candidate_ids"] == sorted(
        candidate["candidate_id"] for candidate in package["candidates"]
    )
    assert decision["evidence_ids"] == sorted(
        evidence["evidence_id"] for evidence in package["evidence_records"]
    )
    assert len(decision["origin_locator_refs"]) == 2
    assert len(decision["context_locator_refs"]) == 2


def test_conflicting_strong_definitions_and_hard_negative_require_review() -> None:
    conflicting, conflicting_source = _package(
        "Alpha means the first bounded synthetic behavior.",
        "Alpha means a contradictory second synthetic behavior.",
        evidence_kinds=("definition", "definition"),
    )
    hard_negative, hard_negative_source = _package(
        "Beta means one bounded synthetic behavior.",
        evidence_kinds=("definition",),
        hard_negative=True,
    )

    conflict_decision = _build(
        conflicting, conflicting_source
    )["decisions"][0]
    hard_negative_decision = _build(
        hard_negative, hard_negative_source
    )["decisions"][0]

    for decision in (conflict_decision, hard_negative_decision):
        assert (decision["route"], decision["outcome"]) == (
            "needs_local_model_review",
            "review",
        )
        assert decision["reason_codes"] == [
            "conflicting_strong_evidence"
        ]
        assert "concept_id" not in decision


@pytest.mark.parametrize(
    "text",
    [
        "11 synthetic statement",
        "Figure synthetic statement",
        "概念 synthetic statement",
    ],
)
def test_noise_never_auto_retains(text: str) -> None:
    package, normalized_source = _package(
        text,
        evidence_kinds=("definition",),
    )

    output = _build(package, normalized_source)

    [decision] = output["decisions"]
    assert decision["outcome"] == "reject"
    assert decision["reason_codes"] == ["name_not_identifiable"]
    assert output["retained_count"] == 0


def test_fail_hash_source_binding_and_reference_inputs_fail_closed() -> None:
    package, normalized_source = _package(
        "Alpha is the bounded synthetic definition.",
        evidence_kinds=("definition",),
    )
    hash_drift = copy.deepcopy(package)
    hash_drift["candidates"][0]["surface"] = "Changed"
    wrong_source = copy.deepcopy(normalized_source)
    wrong_source["raw_sha256"] = "0" * 64
    invalid_reference = copy.deepcopy(package)
    invalid_reference["candidates"][0]["origin_ids"] = ["origin-missing"]
    invalid_reference = seal_handoff_draft(
        invalid_reference,
        normalized_source=normalized_source,
    )

    for invalid_package, source in (
        (hash_drift, normalized_source),
        (package, wrong_source),
        (invalid_reference, normalized_source),
    ):
        output = _build(invalid_package, source)
        assert output["decisions"] == []
        assert output["pending_questions"] == []
        assert output["retained_count"] == 0


def test_input_is_immutable_and_replay_is_canonical_three_of_three() -> None:
    package, normalized_source = _package(
        "Alpha is the bounded synthetic definition.",
        "Beta heading with bounded source context.",
        "Gamma alias with bounded source context.",
        evidence_kinds=("definition", "heading", "explicit_alias"),
    )
    original_package = copy.deepcopy(package)
    original_source = copy.deepcopy(normalized_source)

    outputs = [
        _build(package, normalized_source)
        for _ in range(3)
    ]

    assert package == original_package
    assert normalized_source == original_source
    encoded = [canonical_json_bytes(output) for output in outputs]
    assert encoded[0] == encoded[1] == encoded[2]
    question_ids_by_replay = [
        [
            question["question_id"]
            for question in output["pending_questions"]
        ]
        for output in outputs
    ]
    assert question_ids_by_replay[0] == question_ids_by_replay[1]
    assert question_ids_by_replay[1] == question_ids_by_replay[2]
    for output in outputs:
        assert output["decisions"] == sorted(
            output["decisions"],
            key=lambda decision: decision["decision_id"],
        )
        question_ids = [
            question["question_id"]
            for question in output["pending_questions"]
        ]
        assert len(question_ids) == 2
        assert len(question_ids) == len(set(question_ids))
        assert question_ids == sorted(question_ids)


def test_retained_and_review_traceability_is_complete_and_model_off() -> None:
    package, normalized_source = _package(
        "Alpha is the bounded synthetic definition.",
        "Beta is a bounded heading.",
        evidence_kinds=("definition", "heading"),
    )

    output = _build(package, normalized_source)

    assert {
        decision["outcome"] for decision in output["decisions"]
    } == {"retain", "review"}
    candidate_ids = {
        candidate["candidate_id"] for candidate in package["candidates"]
    }
    evidence_ids = {
        evidence["evidence_id"] for evidence in package["evidence_records"]
    }
    origin_ids = {
        origin["origin_id"] for origin in package["origins"]
    }
    context_ids = {
        context["context_id"] for context in package["contexts"]
    }
    for decision in output["decisions"]:
        assert set(decision["candidate_ids"]) <= candidate_ids
        assert set(decision["evidence_ids"]) <= evidence_ids
        assert {
            ref["origin_id"] for ref in decision["origin_locator_refs"]
        } <= origin_ids
        assert {
            ref["context_id"]
            for ref in decision["context_locator_refs"]
        } <= context_ids
    assert not {
        "model",
        "model_call",
        "model_calls",
        "model_output",
        "provider",
    } & _all_keys(output)
