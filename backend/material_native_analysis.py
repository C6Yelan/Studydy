from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

import pymupdf

from material_native_layout import (
    BBOX_MATCH_TOLERANCE_PT,
    _analyze_page,
    _failed_row,
)
from material_runtime_files import publish_runtime_json


SCHEMA_VERSION = "material-native-analysis/v2"
NATIVE_ANALYSIS_STABLE_PATH = (
    ".studydy-runtime/materials/native-analysis/stable/"
    "material-native-analysis.v2.json"
)

def analyze_material_native(
    material_blocks: Mapping[str, Any],
    source_descriptors: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """檢查輸入資料，找到每份教材對應的 PDF，並逐頁產生原生版面分析結果。"""
    if material_blocks.get("schema_version") != "material-blocks/v1":
        raise ValueError("material_blocks_schema_mismatch")
    materials = material_blocks.get("materials")
    if not isinstance(materials, list):
        raise ValueError("materials_invalid")
    if not isinstance(source_descriptors, Sequence) or isinstance(
        source_descriptors,
        (str, bytes),
    ):
        raise ValueError("source_descriptors_invalid")

    source_paths, source_failures = _source_paths(
        materials,
        source_descriptors,
    )

    rows: list[dict[str, Any]] = []
    for material in materials:
        if not isinstance(material, Mapping):
            raise ValueError("material_invalid")
        identity = _material_identity(material)
        rows.extend(
            _analyze_material(
                material,
                source_paths.get(identity),
                source_failures.get(identity),
            )
        )

    rows.sort(key=lambda row: (row["material_id"], row["pdf_page"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "page_count": len(rows),
        "pages": rows,
    }


def persist_material_native_analysis(
    artifact: Mapping[str, Any],
    *,
    repo_root: str | Path,
) -> None:
    """把分析結果寫入專案固定保存的 runtime JSON 檔案。"""
    publish_runtime_json(
        artifact,
        repo_root=repo_root,
        stable_path=NATIVE_ANALYSIS_STABLE_PATH,
    )


def _analyze_material(
    material: Mapping[str, Any],
    pdf_path: str | Path | None,
    source_failure_reason: str | None,
) -> list[dict[str, Any]]:
    """開啟一份教材的 PDF 並逐頁分析；無法讀取時為每頁保留失敗原因。"""
    material_id = material.get("material_id")
    case_id = material.get("case_id")
    artifact_ref = material.get("artifact_ref")
    blocks = material.get("blocks")
    if not isinstance(material_id, str) or not material_id:
        raise ValueError("material_id_missing")
    if not isinstance(case_id, str) or not case_id:
        raise ValueError("case_id_missing")
    if not isinstance(blocks, list):
        raise ValueError("blocks_invalid")

    if source_failure_reason is not None or pdf_path is None:
        return [
            _failed_row(
                material_id,
                case_id,
                artifact_ref,
                block,
                source_failure_reason or "source_mapping_missing",
            )
            for block in blocks
        ]

    try:
        document = pymupdf.open(Path(pdf_path))
    except Exception:
        return [
            _failed_row(
                material_id,
                case_id,
                artifact_ref,
                block,
                "document_unreadable",
            )
            for block in blocks
        ]

    try:
        page_count_mismatch = document.page_count != len(blocks)
        rows = []
        for block in blocks:
            if not isinstance(block, Mapping):
                raise ValueError("block_invalid")
            locator = block.get("locator")
            if not isinstance(locator, Mapping):
                raise ValueError("locator_missing")
            pdf_page = locator.get("pdf_page")
            if not isinstance(pdf_page, int) or pdf_page < 1:
                raise ValueError("page_locator_missing")
            try:
                page = document.load_page(pdf_page - 1)
            except Exception:
                rows.append(
                    _failed_row(
                        material_id,
                        case_id,
                        artifact_ref,
                        block,
                        "page_unreadable",
                    )
                )
                continue
            row = _analyze_page(material_id, case_id, artifact_ref, block, page)
            if page_count_mismatch:
                row["status"] = "partial"
                row["reasons"] = sorted(
                    {*row["reasons"], "document_page_count_mismatch"}
                )
            rows.append(row)
        return rows
    finally:
        document.close()


def _material_identity(
    material: Mapping[str, Any],
) -> tuple[str, str, str]:
    """讀取並檢查教材 ID、case ID 與來源檔案 ID，供後續配對 PDF。"""
    material_id = material.get("material_id")
    case_id = material.get("case_id")
    artifact_ref = material.get("artifact_ref")
    if not isinstance(material_id, str) or not material_id:
        raise ValueError("material_id_missing")
    if not isinstance(case_id, str) or not case_id:
        raise ValueError("case_id_missing")
    if not isinstance(artifact_ref, str) or not artifact_ref:
        raise ValueError("artifact_ref_missing")
    return material_id, case_id, artifact_ref


def _source_paths(
    materials: list[Any],
    descriptors: Sequence[Mapping[str, Any]],
) -> tuple[
    dict[tuple[str, str, str], str | Path],
    dict[tuple[str, str, str], str],
]:
    """替每份教材配對唯一的 PDF；缺少、重複或身分衝突時記錄失敗原因。"""
    material_counts: dict[tuple[str, str, str], int] = {}
    for material in materials:
        if not isinstance(material, Mapping):
            raise ValueError("material_invalid")
        identity = _material_identity(material)
        material_counts[identity] = material_counts.get(identity, 0) + 1

    descriptor_rows: dict[
        tuple[str, str, str],
        list[str | Path],
    ] = {}
    invalid_descriptor = False
    for descriptor in descriptors:
        if not isinstance(descriptor, Mapping) or set(descriptor) != {
            "artifact_ref",
            "case_id",
            "material_id",
            "pdf_path",
        }:
            invalid_descriptor = True
            continue
        identity_values = (
            descriptor.get("material_id"),
            descriptor.get("case_id"),
            descriptor.get("artifact_ref"),
        )
        pdf_path = descriptor.get("pdf_path")
        if (
            not all(
                isinstance(value, str) and value
                for value in identity_values
            )
            or not isinstance(pdf_path, (str, Path))
            or not str(pdf_path)
        ):
            invalid_descriptor = True
            continue
        identity = (
            identity_values[0],
            identity_values[1],
            identity_values[2],
        )
        descriptor_rows.setdefault(identity, []).append(pdf_path)

    material_identities = set(material_counts)
    descriptor_identities = set(descriptor_rows)
    paths: dict[tuple[str, str, str], str | Path] = {}
    if invalid_descriptor:
        return paths, {
            identity: "source_mapping_invalid"
            for identity in material_identities
        }
    if descriptor_identities - material_identities:
        return paths, {
            identity: "source_mapping_identity_mismatch"
            for identity in material_identities
        }

    identities_by_path: dict[str, set[tuple[str, str, str]]] = {}
    for identity, descriptor_paths in descriptor_rows.items():
        for descriptor_path in descriptor_paths:
            identities_by_path.setdefault(
                str(Path(descriptor_path)),
                set(),
            ).add(identity)
    shared_paths = {
        identity
        for identities in identities_by_path.values()
        if len(identities) > 1
        for identity in identities
    }

    failures: dict[tuple[str, str, str], str] = {}
    for identity in material_identities:
        descriptor_paths = descriptor_rows.get(identity, [])
        if (
            material_counts[identity] != 1
            or len(descriptor_paths) > 1
            or identity in shared_paths
        ):
            failures[identity] = "source_mapping_ambiguous"
        elif not descriptor_paths:
            failures[identity] = "source_mapping_missing"
        else:
            paths[identity] = descriptor_paths[0]
    return paths, failures
