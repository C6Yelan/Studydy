"""以 exact KS/run 綁定來源；原生定位可不確定，normalized PDF 不可指錯。"""

from copy import deepcopy
from uuid import UUID

from sqlalchemy import select

from pdf_evidence.ocr_page_evidence import canonical_sha256
from knowledge_map.structure import finalize_knowledge_structure

from .storage.tables import (
    Artifact,
    MaterialProcessingRun,
    MaterialSourceSet,
    MaterialSourceSetItem,
    SourceNormalization,
    database_session,
)
from .storage.source_artifacts import open_verified_artifact
from .source_normalization import SourceError
from .material_runtime import lock_matches_binding


def _bound_input(session, owner, run):
    """同一 DB snapshot 核對來源關係；不開啟尚未使用的檔案。"""
    if run is None or run.learner_id != owner:
        raise SourceError("RESOURCE_NOT_FOUND")
    if run.input_source_set_id is None:
        raise SourceError("SOURCE_BINDING_INVALID")
    source_set = session.get(MaterialSourceSet, run.input_source_set_id)
    if source_set is None or source_set.learner_id != owner or source_set.material_id != run.material_id:
        raise SourceError("SOURCE_BINDING_INVALID")

    manifest = deepcopy(source_set.manifest)
    bundle = deepcopy(run.bundle_manifest)
    members = session.scalars(select(MaterialSourceSetItem).where(
        MaterialSourceSetItem.source_set_id == source_set.source_set_id,
    ).order_by(MaterialSourceSetItem.ordinal)).all()
    if (
        canonical_sha256(manifest) != source_set.digest
        or canonical_sha256(bundle) != run.bundle_manifest_sha256
        or bundle.get("source_set_digest") != source_set.digest
        or len(members) != len(manifest["items"])
        or not members
        or bundle.get("schema") != "bundle-manifest/v1"
    ):
        raise SourceError("SOURCE_BINDING_INVALID")

    for ordinal, (member, item) in enumerate(zip(members, manifest["items"]), 1):
        if (
            member.ordinal != ordinal
            or str(member.source_id) != item["source_id"]
            or str(member.normalization_id) != item["normalization_id"]
        ):
            raise SourceError("SOURCE_BINDING_INVALID")
        job = session.get(SourceNormalization, member.normalization_id)
        if (
            job is None
            or job.status != "ready"
            or job.learner_id != owner
            or job.material_id != run.material_id
            or job.source_id != member.source_id
            or canonical_sha256(job.policy) != item["policy_sha256"]
            or job.page_count != item["page_count"]
            or str(job.normalized_artifact_id) != item["normalized_artifact_id"]
            or str(job.mapping_artifact_id) != item["mapping_artifact_id"]
        ):
            raise SourceError("SOURCE_BINDING_INVALID")
        for prefix in ("original", "normalized", "mapping"):
            artifact = session.get(Artifact, UUID(item[f"{prefix}_artifact_id"]))
            if (
                artifact is None
                or artifact.learner_id != owner
                or artifact.material_id != run.material_id
                or bytes(artifact.sha256).hex() != item[f"{prefix}_sha256"]
            ):
                raise SourceError("SOURCE_BINDING_INVALID")

    first_source = manifest["items"][0]
    if str(run.source_artifact_id) != first_source["normalized_artifact_id"]:
        raise SourceError("SOURCE_BINDING_INVALID")
    if (
        bundle.get("processing_policy") != "source-boundary-incremental/v1"
        or bundle.get("source_names") != [item["original_name"] for item in manifest["items"]]
    ):
        raise SourceError("SOURCE_BINDING_INVALID")
    expected_pages = []
    for item in manifest["items"]:
        for number in range(1, item["page_count"] + 1):
            expected_pages.append({
                "page": len(expected_pages) + 1,
                "source_id": item["source_id"],
                "normalized_page": number,
            })
    if bundle["pages"] != expected_pages:
        raise SourceError("SOURCE_BINDING_INVALID")
    return {
        "schema": "structure-input-binding/v1",
        "source_set_id": str(source_set.source_set_id),
        "source_set_digest": source_set.digest,
        "bundle_manifest_sha256": run.bundle_manifest_sha256,
        "manifest": manifest,
        "bundle": bundle,
        "base_revision": run.base_revision,
    }


def _input(owner, run_id, *, dsn=None):
    with database_session(dsn) as session:
        return _bound_input(session, owner, session.get(MaterialProcessingRun, run_id))


def verify_source_files(owner, binding, *, dsn=None):
    """發布邊界檢查完整來源檔案；下載／預覽另由開檔入口檢查實際 bytes。"""
    for item in binding["manifest"]["items"]:
        for prefix in ("original", "normalized", "mapping"):
            with open_verified_artifact(owner, UUID(item[f"{prefix}_artifact_id"]), dsn=dsn) as blob:
                if blob.sha256 != item[f"{prefix}_sha256"]:
                    raise SourceError("SOURCE_BINDING_INVALID")


def bind_structure_input(owner, run_id, document, *, dsn=None):
    binding = _input(owner, run_id, dsn=dsn)
    return finalize_knowledge_structure(document, binding)


def verify_structure_input(session, run, document):
    expected = _bound_input(session, run.learner_id, run)
    runtime = run.runtime_binding
    if (
        document.get("input_binding") != expected
        or not lock_matches_binding(run.runtime_lock_document, runtime)
        or any(
            runtime[key] != document["provenance"][key]
            for key in ("model_id", "model_revision", "runtime_lock_sha256")
        )
    ):
        raise SourceError("SOURCE_BINDING_INVALID")
    return expected
