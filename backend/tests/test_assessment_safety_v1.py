import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from learning_adaptation.assessments import _candidate, _documents
from learning_adaptation.map_context import ClaimContext, ConceptContext, EvidenceContext
from runtime.storage.tables import StudySession


def _claim() -> ClaimContext:
    return ClaimContext(
        "claim:sha256:" + "1" * 64,
        "The null character is written as '\\0' and the array has 8 bytes.",
        (
            EvidenceContext(
                "evidence:sha256:" + "2" * 64,
                4,
                "The null character is written as '\\0' and the array has 8 bytes.",
                {"page": 4, "block_id": "block:sha256:" + "3" * 64, "region": [1, 2, 3, 4]},
            ),
        ),
    )


def _proposal() -> dict:
    return {
        "learning_angle": "exact null character spelling",
        "novelty": "distinct",
        "safety": "safe",
        "prompt": "根據教材，null character 的寫法是什麼？",
        "correct_answer": "'\\0'",
        "supporting_evidence_ids": ["evidence:sha256:" + "2" * 64],
        "distractors": [
            {"text": "'\\n'", "changed_from": "\\0", "changed_to": "\\n"},
            {"text": "'NULL'", "changed_from": "\\0", "changed_to": "NULL"},
            {"text": "nullptr", "changed_from": "\\0", "changed_to": "nullptr"},
        ],
    }


def test_source_span_candidate_preserves_technical_token():
    candidate = _candidate(_proposal(), _claim(), set())
    assert candidate is not None
    assert candidate["correct_answer"] == "'\\0'"
    assert candidate["options"] == ["'\\0'", "'\\n'", "'NULL'", "nullptr"]


def test_unsupported_correct_multiple_supported_and_model_reject_are_blocked():
    unsupported = _proposal()
    unsupported["correct_answer"] = "'\\x00'"
    assert _candidate(unsupported, _claim(), set()) is None

    supported_distractor = _proposal()
    supported_distractor["distractors"][0] = {
        "text": "8 bytes",
        "changed_from": "\\0",
        "changed_to": "8 bytes",
    }
    assert _candidate(supported_distractor, _claim(), set()) is None

    rejected = _proposal()
    rejected["safety"] = "reject"
    assert _candidate(rejected, _claim(), set()) is None

    semantic_equivalent = _proposal()
    semantic_equivalent["distractors"][0] = {
        "text": "the zero byte",
        "changed_from": "\\0",
        "changed_to": "the zero byte",
    }
    semantic_equivalent["safety"] = "reject"
    assert _candidate(semantic_equivalent, _claim(), set()) is None


def test_exact_duplicate_is_blocked_but_novelty_uncertainty_does_not_block_publication():
    first = _candidate(_proposal(), _claim(), set())
    assert first is not None
    assert _candidate(_proposal(), _claim(), {first["semantic_identity"]}) is None

    uncertain = _proposal()
    uncertain["novelty"] = "uncertain"
    uncertain["prompt"] = "依教材選出 null character。"
    projected = _candidate(uncertain, _claim(), set())
    assert projected is not None
    study = StudySession(
        study_session_id=uuid4(),
        learner_id=uuid4(),
        material_id=uuid4(),
        knowledge_structure_revision="knowledge-structure:sha256:" + "4" * 64,
        current_concept_id="concept:sha256:" + "5" * 64,
        no_safe_claim_ids=[],
        deferred_concept_ids=[],
        status="active",
        idempotency_key_sha256=b"x" * 32,
        request_fingerprint=b"y" * 32,
        started_at=datetime.now(UTC),
        last_event_number=0,
    )
    concept = ConceptContext(study.current_concept_id, "Null character", (_claim(),), ())
    lock = json.loads((Path(__file__).parents[2] / "local_ai/runtime-lock.json").read_text())
    public, private, provenance, qualified = _documents(
        study, concept, _claim(), projected, runtime_lock=lock, prior_angles={"another angle"}
    )
    assert public["schema"] == "single-choice-assessment/v2"
    assert "correct_option_id" not in public
    assert private["correct_answer"] == "'\\0'"
    assert provenance["model_id"] == "Qwen/Qwen3.8-27B-FP8"
    assert qualified is False
