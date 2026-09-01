from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from runtime.learner_session import TrustedLearner
from runtime.storage.database import DatabaseConfigurationError
from runtime.storage.tables import Assessment, database_session

from .assessment_generation import (
    AssessmentGenerationError,
    generate_and_store_assessment,
)
from .assessment_items import (
    AssessmentError,
    StoredAssessment,
    _stored_assessment,
)
from .assessment_runtime_reuse import AssessmentRuntimeReuse
from .adaptive_plans import record_no_safe_assessment
from .map_context import MapContextError
from .study_sessions import (
    StudySessionError,
    _learner_id,
    _read_stored_row,
    _validate_binding,
)


_CLAIM_ID = re.compile(r"^claim:sha256:[0-9a-f]{64}$")


class AssessmentRequestError(RuntimeError):
    """Public Assessment request identity 無法安全處理。"""


def _error(reason: str) -> AssessmentRequestError:
    return AssessmentRequestError(reason)


def assessment_request_identity(
    study_session_id: UUID,
    target_claim_id: str,
    idempotency_key: str,
) -> tuple[bytes, bytes]:
    try:
        encoded_key = idempotency_key.encode("utf-8")
    except (AttributeError, UnicodeError):
        raise _error("ASSESSMENT_REQUEST_INVALID") from None
    if (
        not isinstance(study_session_id, UUID)
        or not isinstance(target_claim_id, str)
        or _CLAIM_ID.fullmatch(target_claim_id) is None
        or not 1 <= len(encoded_key) <= 256
    ):
        raise _error("ASSESSMENT_REQUEST_INVALID")
    request = json.dumps(
        {
            "study_session_id": str(study_session_id),
            "target_claim_id": target_claim_id,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return sha256(encoded_key).digest(), sha256(request).digest()


def _existing_request(
    session,
    study_session_id: UUID,
    target_claim_id: str,
    key_digest: bytes,
    fingerprint: bytes,
) -> StoredAssessment | None:
    row = session.scalar(
        select(Assessment).where(
            Assessment.study_session_id == study_session_id,
            Assessment.request_idempotency_key_sha256 == key_digest,
        )
    )
    if row is None:
        return None
    if (
        bytes(row.request_fingerprint) != fingerprint
        or row.target_claim_id != target_claim_id
    ):
        raise _error("ASSESSMENT_IDEMPOTENCY_CONFLICT")
    return _stored_assessment(row)


def read_assessment_request(
    learner: TrustedLearner,
    study_session_id: UUID,
    target_claim_id: str,
    idempotency_key: str,
    *,
    dsn: str | None = None,
) -> StoredAssessment | None:
    key_digest, fingerprint = assessment_request_identity(
        study_session_id, target_claim_id, idempotency_key
    )
    try:
        learner_id = _learner_id(learner)
        with database_session(dsn) as session:
            study_session = _read_stored_row(
                session, learner_id, study_session_id
            )
            _validate_binding(session, study_session)
            return _existing_request(
                session,
                study_session_id,
                target_claim_id,
                key_digest,
                fingerprint,
            )
    except AssessmentRequestError:
        raise
    except (AssessmentError, StudySessionError, MapContextError):
        raise _error("ASSESSMENT_REQUEST_UNAVAILABLE") from None
    except (DatabaseConfigurationError, SQLAlchemyError, TypeError, ValueError):
        raise _error("ASSESSMENT_REQUEST_STORAGE_FAILED") from None


def generate_assessment_for_request(
    learner: TrustedLearner,
    study_session_id: UUID,
    target_claim_id: str,
    local_config: dict[str, Any],
    idempotency_key: str,
    *,
    runtime_reuse: AssessmentRuntimeReuse | None = None,
    dsn: str | None = None,
) -> StoredAssessment:
    """在 frozen generator 外綁定 public request idempotency。"""

    key_digest, fingerprint = assessment_request_identity(
        study_session_id, target_claim_id, idempotency_key
    )
    expected_formal_concept_id: str | None = None
    expected_event_number: int | None = None
    try:
        learner_id = _learner_id(learner)
        semantic_lock_key = int.from_bytes(
            sha256(
                b"assessment-semantic-novelty:"
                + study_session_id.bytes
            ).digest()[:8],
            "big",
            signed=True,
        )
        with database_session(dsn) as session:
            session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": semantic_lock_key},
            )
            study_session = _read_stored_row(
                session, learner_id, study_session_id
            )
            _validate_binding(session, study_session)
            replay = _existing_request(
                session,
                study_session_id,
                target_claim_id,
                key_digest,
                fingerprint,
            )
            if replay is not None:
                return replay
            if study_session.status == "no_safe":
                raise AssessmentGenerationError(
                    "ASSESSMENT_NO_NEW_SAFE_ITEM"
                )
            if study_session.status != "active":
                raise _error("ASSESSMENT_REQUEST_UNAVAILABLE")
            expected_formal_concept_id = (
                study_session.current_formal_concept_id
            )
            expected_event_number = study_session.last_event_number
            if target_claim_id in study_session.no_safe_claim_ids:
                raise AssessmentGenerationError(
                    "ASSESSMENT_NO_NEW_SAFE_ITEM"
                )
            operation = lambda: generate_and_store_assessment(
                learner, study_session_id, target_claim_id, local_config, dsn=dsn
            )
            generated = (
                operation()
                if runtime_reuse is None
                else runtime_reuse.generate(operation)
            )
            row = session.scalar(
                select(Assessment)
                .where(
                    Assessment.study_session_id == study_session_id,
                    Assessment.assessment_revision
                    == generated.assessment_revision,
                )
                .with_for_update()
            )
            if row is None:
                raise _error("ASSESSMENT_REQUEST_STORAGE_FAILED")
            if (
                row.request_idempotency_key_sha256 is not None
                or row.request_fingerprint is not None
            ):
                raise _error("ASSESSMENT_IDEMPOTENCY_CONFLICT")
            row.request_idempotency_key_sha256 = key_digest
            row.request_fingerprint = fingerprint
            session.flush()
            return _stored_assessment(row)
    except AssessmentGenerationError as error:
        if (
            str(error)
            in {"ASSESSMENT_NO_NEW_SAFE_ITEM", "ASSESSMENT_NO_SAFE_CANDIDATE"}
            and expected_formal_concept_id is not None
            and expected_event_number is not None
        ):
            record_no_safe_assessment(
                learner,
                study_session_id,
                target_claim_id,
                expected_formal_concept_id,
                expected_event_number,
                dsn=dsn,
            )
        raise
    except AssessmentRequestError:
        raise
    except (AssessmentError, StudySessionError, MapContextError):
        raise _error("ASSESSMENT_REQUEST_UNAVAILABLE") from None
    except (DatabaseConfigurationError, SQLAlchemyError, TypeError, ValueError):
        raise _error("ASSESSMENT_REQUEST_STORAGE_FAILED") from None
