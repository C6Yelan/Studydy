from copy import deepcopy
import hashlib
import json

import pytest
import pymupdf

from learning_resources.catalog import (
    build_controlled_resource_catalog,
    validate_controlled_resource_catalog,
)


def _write_pdf(path):
    document = pymupdf.open()
    document.new_page()
    document.save(path)
    document.close()


def _candidate(
    artifact_path,
    *,
    subject="data_structures",
    title="Open Data Structures",
    topics=None,
    keywords=None,
    source_locator="https://materials.university.edu/open-data-structures",
    license_status="cc_by",
    use_boundary="attribution_required",
):
    return {
        "assessment": "accepted",
        "subject": subject,
        "title": title,
        "topics": topics or ["array", "stack"],
        "keywords": keywords or ["data structure", "complexity"],
        "source_locator": source_locator,
        "artifact_ref": artifact_path.name,
        "artifact_sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
        "license_status": license_status,
        "use_boundary": use_boundary,
        "checked_at": "2026-08-08T12:00:00+08:00",
        "learning_use": "primary",
    }


def _rebind_catalog(catalog):
    content = {
        key: value for key, value in catalog.items() if key != "catalog_revision"
    }
    encoded = json.dumps(
        content,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    catalog["catalog_revision"] = (
        "resource-catalog:sha256:" + hashlib.sha256(encoded).hexdigest()
    )
    return catalog

def test_builds_evidence_bound_catalog_without_concept_id(tmp_path):
    """只保留完整受控資源，catalog 不混入 Concept matching 欄位。"""
    artifact_path = tmp_path / "resource.pdf"
    _write_pdf(artifact_path)

    catalog = build_controlled_resource_catalog([_candidate(artifact_path)], tmp_path)

    assert validate_controlled_resource_catalog(catalog, tmp_path) is None
    assert catalog["schema"] == "controlled-resource-catalog/v1"
    assert catalog["catalog_revision"].startswith("resource-catalog:sha256:")
    assert catalog["exclusions"] == []
    assert (
        catalog["processing"],
        catalog["quality"],
        catalog["decision"],
        catalog["reason_code"],
    ) == ("succeeded", "accepted", "retain", "RESOURCE_CATALOG_ACCEPTED")
    resource = catalog["resources"][0]
    assert "concept_id" not in catalog
    assert "concept_id" not in resource
    assert resource["resource_key"].startswith("resource:sha256:")
    assert "summary" not in resource
    assert "source_name" not in resource
    assert (
        resource["processing"],
        resource["quality"],
        resource["decision"],
        resource["reason_code"],
    ) == ("succeeded", "accepted", "retain", "SOURCE_ACCEPTED")


@pytest.mark.parametrize(
    "subject",
    ["data_structures", "e_commerce", "linear_algebra", "world_history"],
)
def test_safe_dynamic_subjects_use_the_same_catalog_path(tmp_path, subject):
    artifact_path = tmp_path / "resource.pdf"
    _write_pdf(artifact_path)

    catalog = build_controlled_resource_catalog(
        [_candidate(artifact_path, subject=subject)], tmp_path
    )

    assert validate_controlled_resource_catalog(catalog, tmp_path) is None
    assert catalog["resources"][0]["subject"] == subject


@pytest.mark.parametrize(
    "subject",
    ["", "Data_Structures", "data-structures", "data__structures", "../physics"],
)
def test_unsafe_subject_slugs_fail_closed(tmp_path, subject):
    artifact_path = tmp_path / "resource.pdf"
    _write_pdf(artifact_path)

    catalog = build_controlled_resource_catalog(
        [_candidate(artifact_path, subject=subject)], tmp_path
    )

    assert catalog["resources"] == []
    assert catalog["exclusions"][0]["reason_code"] == "RESOURCE_SUBJECT_INVALID"


@pytest.mark.parametrize(
    "source_locator",
    [
        "https://materials.example.edu/book.pdf",
        "https://cdn.example.org/library/book.pdf?revision=2",
        "https://materials.example.edu:0/book.pdf",
        "https://materials.example.edu:65535/book.pdf",
    ],
)
def test_dynamic_https_source_locators_are_accepted(tmp_path, source_locator):
    artifact_path = tmp_path / "resource.pdf"
    _write_pdf(artifact_path)

    catalog = build_controlled_resource_catalog(
        [_candidate(artifact_path, source_locator=source_locator)], tmp_path
    )

    assert validate_controlled_resource_catalog(catalog, tmp_path) is None
    assert catalog["resources"][0]["source_locator"] == source_locator


def test_private_artifact_locator_is_hash_bound(tmp_path):
    artifact_path = tmp_path / "resource.pdf"
    _write_pdf(artifact_path)
    artifact_sha256 = hashlib.sha256(artifact_path.read_bytes()).hexdigest()

    accepted = build_controlled_resource_catalog(
        [
            _candidate(
                artifact_path,
                source_locator=f"artifact:sha256:{artifact_sha256}",
                license_status="private_task_authorized",
                use_boundary="private_task_only",
            )
        ],
        tmp_path,
    )
    mismatched = build_controlled_resource_catalog(
        [
            _candidate(
                artifact_path,
                source_locator="artifact:sha256:" + "0" * 64,
                license_status="private_task_authorized",
                use_boundary="private_task_only",
            )
        ],
        tmp_path,
    )

    assert validate_controlled_resource_catalog(accepted, tmp_path) is None
    assert mismatched["resources"] == []
    assert mismatched["exclusions"][0]["reason_code"] == "RESOURCE_LOCATOR_INVALID"


@pytest.mark.parametrize(
    "source_locator",
    [
        "https://example.edu/book name.pdf",
        "https://user@example.edu/book.pdf",
        "https:///book.pdf",
        "https://example.org:not-a-port/a.pdf",
        "https://example.org:65536/a.pdf",
    ],
)
def test_unsafe_source_locators_fail_closed(tmp_path, source_locator):
    artifact_path = tmp_path / "resource.pdf"
    _write_pdf(artifact_path)

    catalog = build_controlled_resource_catalog(
        [_candidate(artifact_path, source_locator=source_locator)], tmp_path
    )

    assert catalog["resources"] == []
    assert catalog["exclusions"][0]["reason_code"] == "RESOURCE_LOCATOR_INVALID"


@pytest.mark.parametrize(
    ("license_status", "use_boundary"),
    [
        ("cc_by", "attribution_required"),
        ("cc_by_nc", "noncommercial_attribution_required"),
        ("cc_by_sa", "attribution_share_alike_required"),
        ("cc_by_nc_sa", "noncommercial_attribution_share_alike_required"),
        ("private_task_authorized", "private_task_only"),
        ("academic_noncommercial_notice", "noncommercial_academic_use"),
    ],
)
def test_approved_license_use_pairs_are_exact(
    tmp_path, license_status, use_boundary
):
    artifact_path = tmp_path / "resource.pdf"
    _write_pdf(artifact_path)

    catalog = build_controlled_resource_catalog(
        [
            _candidate(
                artifact_path,
                license_status=license_status,
                use_boundary=use_boundary,
            )
        ],
        tmp_path,
    )

    assert validate_controlled_resource_catalog(catalog, tmp_path) is None


@pytest.mark.parametrize(
    ("license_status", "use_boundary"),
    [
        ("cc_by_sa", "noncommercial_attribution_share_alike_required"),
        ("cc_by_nc_sa", "attribution_share_alike_required"),
    ],
)
def test_mismatched_share_alike_license_pairs_fail_closed(
    tmp_path, license_status, use_boundary
):
    artifact_path = tmp_path / "resource.pdf"
    _write_pdf(artifact_path)

    catalog = build_controlled_resource_catalog(
        [
            _candidate(
                artifact_path,
                license_status=license_status,
                use_boundary=use_boundary,
            )
        ],
        tmp_path,
    )

    assert catalog["resources"] == []
    assert catalog["exclusions"][0]["reason_code"] == "RESOURCE_LICENSE_INVALID"


@pytest.mark.parametrize(
    ("invalid_case", "reason_code"),
    [
        ("review", "RESOURCE_SOURCE_NEEDS_REVIEW"),
        ("problem", "RESOURCE_SOURCE_PROBLEM"),
        ("subject", "RESOURCE_SUBJECT_INVALID"),
        ("locator", "RESOURCE_LOCATOR_INVALID"),
        ("license", "RESOURCE_LICENSE_INVALID"),
        ("artifact_missing", "RESOURCE_ARTIFACT_MISSING"),
        ("artifact_hash", "RESOURCE_ARTIFACT_HASH_MISMATCH"),
    ],
)
def test_untrusted_candidates_are_excluded_with_reason(
    tmp_path, invalid_case, reason_code
):
    """review、problem 或證據不完整的來源不得變成 retained resource。"""
    artifact_path = tmp_path / "resource.pdf"
    _write_pdf(artifact_path)
    valid = _candidate(artifact_path)
    invalid = deepcopy(valid)
    if invalid_case in {"review", "problem"}:
        invalid["assessment"] = invalid_case
    elif invalid_case == "subject":
        invalid["subject"] = "Accounting"
    elif invalid_case == "locator":
        invalid["source_locator"] = "not-a-url"
    elif invalid_case == "license":
        invalid["license_status"] = "unknown"
    elif invalid_case == "artifact_missing":
        invalid["artifact_ref"] = "missing.pdf"
    elif invalid_case == "artifact_hash":
        invalid["artifact_sha256"] = "0" * 64

    catalog = build_controlled_resource_catalog([valid, invalid], tmp_path)

    assert len(catalog["resources"]) == 1
    assert catalog["exclusions"] == [
        {
            "input_index": 1,
            "processing": "partial" if invalid_case == "review" else "failed",
            "quality": "needs_review" if invalid_case == "review" else "unsupported",
            "decision": "review" if invalid_case == "review" else "reject",
            "reason_code": reason_code,
        }
    ]
    assert catalog["reason_code"] == "RESOURCE_CATALOG_PARTIAL"
    assert validate_controlled_resource_catalog(catalog, tmp_path) is None


def test_duplicate_resource_is_not_retained_twice(tmp_path):
    artifact_path = tmp_path / "resource.pdf"
    _write_pdf(artifact_path)
    candidate = _candidate(artifact_path)

    catalog = build_controlled_resource_catalog(
        [candidate, deepcopy(candidate)], tmp_path
    )

    assert len(catalog["resources"]) == 1
    assert catalog["exclusions"][0]["reason_code"] == "RESOURCE_DUPLICATE"
    assert validate_controlled_resource_catalog(catalog, tmp_path) is None


def test_distinct_artifacts_from_one_source_page_are_retained(tmp_path):
    stack_path = tmp_path / "stack.pdf"
    queue_path = tmp_path / "queue.pdf"
    _write_pdf(stack_path)
    with pymupdf.open() as document:
        document.new_page()
        document.new_page()
        document.save(queue_path)
    locator = "https://materials.example.edu/stacks-and-queues"

    catalog = build_controlled_resource_catalog(
        [
            _candidate(stack_path, title="Stack", source_locator=locator),
            _candidate(queue_path, title="Queue", source_locator=locator),
        ],
        tmp_path,
    )

    assert validate_controlled_resource_catalog(catalog, tmp_path) is None
    assert {resource["title"] for resource in catalog["resources"]} == {
        "Queue",
        "Stack",
    }


def test_normalized_duplicate_metadata_is_excluded(tmp_path):
    artifact_path = tmp_path / "resource.pdf"
    _write_pdf(artifact_path)
    candidate = _candidate(artifact_path, topics=["array", " ARRAY "])

    catalog = build_controlled_resource_catalog([candidate], tmp_path)

    assert catalog["resources"] == []
    assert catalog["exclusions"][0]["reason_code"] == "RESOURCE_METADATA_INVALID"
    assert validate_controlled_resource_catalog(catalog, tmp_path) is None


def test_literal_placeholder_metadata_is_excluded(tmp_path):
    artifact_path = tmp_path / "resource.pdf"
    _write_pdf(artifact_path)
    candidate = _candidate(artifact_path, keywords=["sample"])

    catalog = build_controlled_resource_catalog([candidate], tmp_path)

    assert catalog["resources"] == []
    assert catalog["exclusions"][0]["reason_code"] == "RESOURCE_METADATA_INVALID"
    assert validate_controlled_resource_catalog(catalog, tmp_path) is None


@pytest.mark.parametrize("extra_field", ["summary", "source_name"])
def test_unapproved_catalog_field_is_excluded(tmp_path, extra_field):
    """加入未核准欄位時，candidate 必須因 exact shape 被拒絕。"""
    artifact_path = tmp_path / "resource.pdf"
    _write_pdf(artifact_path)
    candidate = _candidate(artifact_path)
    candidate[extra_field] = "Unapproved catalog text"

    catalog = build_controlled_resource_catalog([candidate], tmp_path)

    assert catalog["resources"] == []
    assert catalog["exclusions"][0]["reason_code"] == "RESOURCE_CANDIDATE_INVALID"
    assert validate_controlled_resource_catalog(catalog, tmp_path) is None


def test_validator_rejects_extra_matching_field_and_changed_artifact(tmp_path):
    artifact_path = tmp_path / "resource.pdf"
    _write_pdf(artifact_path)
    catalog = build_controlled_resource_catalog([_candidate(artifact_path)], tmp_path)

    changed = deepcopy(catalog)
    changed["resources"][0]["concept_id"] = "concept:sha256:" + "0" * 64
    assert (
        validate_controlled_resource_catalog(_rebind_catalog(changed), tmp_path)
        == "RESOURCE_METADATA_INVALID"
    )

    for extra_field in ("summary", "source_name"):
        changed = deepcopy(catalog)
        changed["resources"][0][extra_field] = "Unapproved catalog text"
        assert (
            validate_controlled_resource_catalog(
                _rebind_catalog(changed), tmp_path
            )
            == "RESOURCE_METADATA_INVALID"
        )

    artifact_path.write_bytes(b"changed after catalog build")
    assert (
        validate_controlled_resource_catalog(catalog, tmp_path)
        == "RESOURCE_ARTIFACT_HASH_MISMATCH"
    )


@pytest.mark.parametrize("artifact_ref", ["../resource.pdf", "/tmp/resource.pdf"])
def test_artifact_path_escape_fails_closed(tmp_path, artifact_ref):
    artifact_path = tmp_path / "resource.pdf"
    _write_pdf(artifact_path)
    candidate = _candidate(artifact_path)
    candidate["artifact_ref"] = artifact_ref

    catalog = build_controlled_resource_catalog([candidate], tmp_path)

    assert catalog["resources"] == []
    assert catalog["exclusions"][0]["reason_code"] == "RESOURCE_ARTIFACT_MISSING"


def test_unreadable_pdf_fails_closed_after_hash_validation(tmp_path):
    artifact_path = tmp_path / "resource.pdf"
    artifact_path.write_bytes(b"not a PDF")

    catalog = build_controlled_resource_catalog([_candidate(artifact_path)], tmp_path)

    assert catalog["resources"] == []
    assert catalog["exclusions"][0]["reason_code"] == "RESOURCE_PDF_INVALID"


def test_zero_page_pdf_fails_closed(tmp_path, monkeypatch):
    artifact_path = tmp_path / "resource.pdf"
    _write_pdf(artifact_path)

    class EmptyDocument:
        is_pdf = True
        page_count = 0

        def close(self):
            return None

    monkeypatch.setattr(
        "learning_resources.catalog.pymupdf.open",
        lambda path: EmptyDocument(),
    )

    catalog = build_controlled_resource_catalog([_candidate(artifact_path)], tmp_path)

    assert catalog["resources"] == []
    assert catalog["exclusions"][0]["reason_code"] == "RESOURCE_PDF_INVALID"


def test_pdf_metadata_read_error_fails_closed(tmp_path, monkeypatch):
    artifact_path = tmp_path / "resource.pdf"
    _write_pdf(artifact_path)

    class BrokenDocument:
        @property
        def is_pdf(self):
            raise RuntimeError("PDF metadata is unavailable")

        def close(self):
            return None

    monkeypatch.setattr(
        "learning_resources.catalog.pymupdf.open",
        lambda path: BrokenDocument(),
    )

    catalog = build_controlled_resource_catalog([_candidate(artifact_path)], tmp_path)

    assert catalog["resources"] == []
    assert catalog["exclusions"][0]["reason_code"] == "RESOURCE_PDF_INVALID"


def test_invalid_root_input_fails_closed(tmp_path):
    assert build_controlled_resource_catalog([], tmp_path) == {
        "schema": "controlled-resource-catalog/v1",
        "processing": "failed",
        "quality": "unsupported",
        "decision": "reject",
        "reason_code": "RESOURCE_CATALOG_INPUT_INVALID",
    }
