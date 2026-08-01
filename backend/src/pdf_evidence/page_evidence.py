from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import tempfile
from typing import Any

import pymupdf


EVIDENCE_SCHEMA = "s1-page-evidence/v1"
RENDER_SCHEMA = "s1-page-render/v1"
# 固定渲染解析度，供頁面渲染與尺寸驗證使用。
RENDER_DPI = 200


def _failure(reason: str, page_number: object) -> dict[str, Any]:
    """建立不含私人路徑或內容的失敗結果。"""
    result: dict[str, Any] = {
        "schema": EVIDENCE_SCHEMA,
        "status": "failed",
        "reason": reason,
    }
    if type(page_number) is int:
        result["page_number"] = page_number
    return result


def _sha256_bytes(data: bytes) -> str:
    """計算記憶體中 bytes 資料的 SHA-256。"""
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    """分次讀取磁碟檔案並計算 SHA-256。"""
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def _box(value: Any) -> list[float]:
    """將 PyMuPDF 矩形轉為可序列化座標。"""
    return [float(value.x0), float(value.y0), float(value.x1), float(value.y1)]


def _descriptor_value(value: Any) -> Any:
    """將PyMuPDF原生描述值轉為可序列化資料。"""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite descriptor number")
        return value
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, (pymupdf.Rect, pymupdf.IRect)):
        return _box(value)
    if isinstance(value, pymupdf.Point):
        return [float(value.x), float(value.y)]
    if isinstance(value, pymupdf.Quad):
        return [_descriptor_value(point) for point in value]
    if isinstance(value, dict):
        return {str(key): _descriptor_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_descriptor_value(item) for item in value]
    raise TypeError(f"unsupported descriptor value: {type(value).__name__}")


def _write_file(path: Path, data: bytes) -> None:
    """寫入新檔並同步內容至磁碟。"""
    with path.open("xb") as file:
        file.write(data)
        file.flush()
        os.fsync(file.fileno())


def _fsync_directory(path: Path) -> None:
    """將檔名與目錄變更同步到磁碟，確保發布結果完整保存。"""
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_manifest(manifest_path: Path | None) -> None:
    """嘗試刪除失效的 manifest，避免不完整產物被視為成功。"""
    if manifest_path is None:
        return
    try:
        manifest_path.unlink(missing_ok=True)
    except OSError:
        pass


def _validate_request(expected_source_sha256: str, page_number: int) -> str | None:
    """驗證頁碼與來源雜湊格式。"""
    if (
        isinstance(page_number, bool)
        or not isinstance(page_number, int)
        or page_number < 1
    ):
        return "PAGE_NUMBER_INVALID"
    if (
        not isinstance(expected_source_sha256, str)
        or len(expected_source_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_source_sha256)
    ):
        return "SOURCE_HASH_INVALID"
    return None


def _verify_source(
    pdf_path: str | os.PathLike[str], expected_source_sha256: str
) -> tuple[Path | None, str | None, str | None]:
    """驗證PDF來源型態、檔頭與內容雜湊。"""
    try:
        source_path = Path(pdf_path)
    except TypeError:
        return None, None, "SOURCE_PATH_INVALID"
    if not source_path.exists():
        return None, None, "SOURCE_MISSING"
    if not source_path.is_file():
        return None, None, "SOURCE_NOT_FILE"
    try:
        with source_path.open("rb") as source:
            if source.read(5) != b"%PDF-":
                return None, None, "SOURCE_NOT_PDF"
        source_sha256 = _sha256_file(source_path)
    except OSError:
        return None, None, "SOURCE_READ_FAILED"
    if source_sha256 != expected_source_sha256:
        return None, None, "SOURCE_HASH_MISMATCH"
    return source_path, source_sha256, None


def _extract_page_payload(
    source_path: Path, source_sha256: str, page_number: int
) -> tuple[dict[str, Any] | None, str | None]:
    """擷取頁面原生描述與渲染，並在內部驗證座標邊界。"""
    # 以來源雜湊與 1-based 頁碼建立不依賴檔名、路徑的穩定識別。
    material_ref = f"material:sha256:{source_sha256}"
    page_ref_hash = _sha256_bytes(f"{source_sha256}:{page_number}".encode("ascii"))
    page_ref = f"page:sha256:{page_ref_hash}"

    # 開啟 PDF 並確認指定頁面可安全載入及具有有效幾何資訊。
    try:
        document = pymupdf.open(source_path)
    except Exception:
        return None, "PDF_CORRUPT"

    try:
        if document.needs_pass:
            return None, "PDF_ENCRYPTED"
        if page_number > document.page_count:
            return None, "PAGE_OUT_OF_RANGE"
        try:
            page = document.load_page(page_number - 1)
        except Exception:
            return None, "PDF_CORRUPT"
        if page.number + 1 != page_number:
            return None, "PAGE_IDENTITY_INVALID"

        cropbox = page.cropbox
        mediabox = page.mediabox
        visible = page.rect
        geometry_numbers = [*_box(cropbox), *_box(mediabox), *_box(visible)]
        if (
            page.rotation not in (0, 90, 180, 270)
            or cropbox.width <= 0
            or cropbox.height <= 0
            or visible.width <= 0
            or visible.height <= 0
            or not all(math.isfinite(number) for number in geometry_numbers)
        ):
            return None, "PAGE_GEOMETRY_INVALID"

        # 擷取文字 spans、圖片及向量圖形，作為可回查的原生頁面證據。
        try:
            text = page.get_text("dict", sort=False)
            spans = []
            for block in text.get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        spans.append(_descriptor_value(span))
            images = _descriptor_value(page.get_image_info(hashes=True, xrefs=True))
            drawings = _descriptor_value(page.get_drawings())
        except Exception:
            return None, "NATIVE_EXTRACTION_FAILED"

        # 將實際頁面渲染成固定 200 DPI RGB PNG，供後續多模態理解使用。
        try:
            pixmap = page.get_pixmap(
                dpi=RENDER_DPI,
                colorspace=pymupdf.csRGB,
                alpha=False,
            )
            render_bytes = pixmap.tobytes("png")
        except Exception:
            return None, "RENDER_FAILED"

        expected_width = visible.width * RENDER_DPI / 72
        expected_height = visible.height * RENDER_DPI / 72
        if (
            pixmap.alpha
            or pixmap.colorspace is None
            or pixmap.colorspace.n != 3
            or not render_bytes.startswith(b"\x89PNG\r\n\x1a\n")
            or pixmap.width <= 0
            or pixmap.height <= 0
            or not math.isfinite(float(pixmap.width))
            or not math.isfinite(float(pixmap.height))
            or abs(pixmap.width - expected_width) > 1
            or abs(pixmap.height - expected_height) > 1
        ):
            return None, "RENDER_VALIDATION_FAILED"

        # PDF 可用 metadata 宣告頁面旋轉；原生文字 bbox 仍採未旋轉座標，
        # render 則呈現旋轉後的畫面。保存雙向矩陣才能讓兩者正確對應。
        # 此處不判斷或修正閱讀方向，只確認文字 bbox 落在原生頁面範圍內。
        try:
            rotation = page.rotation_matrix
            derotation = page.derotation_matrix
            matrix_values = [*rotation, *derotation]
            if not all(math.isfinite(float(value)) for value in matrix_values):
                raise ValueError("non-finite coordinate matrix")

            native_region = visible * derotation
            for span in spans:
                bbox = span.get("bbox") if isinstance(span, dict) else None
                if not isinstance(bbox, list) or len(bbox) != 4:
                    raise ValueError("invalid span bbox")
                bbox_values = [float(value) for value in bbox]
                if (
                    not all(math.isfinite(value) for value in bbox_values)
                    or bbox_values[2] <= bbox_values[0]
                    or bbox_values[3] <= bbox_values[1]
                ):
                    raise ValueError("invalid span bbox")
                intersection = pymupdf.Rect(bbox_values) & native_region
                if intersection.width <= 0 or intersection.height <= 0:
                    raise ValueError("span bbox is outside the page")

            coordinate_transform = {
                "native_coordinate_space": "unrotated_page_points",
                "render_coordinate_space": "rotated_page_points",
                "point_to_rotated": [float(value) for value in rotation],
                "rotated_to_point": [float(value) for value in derotation],
            }
        except (KeyError, TypeError, ValueError):
            return None, "COORDINATE_VALIDATION_FAILED"

        # 將原生證據序列化，連同 render 與座標資料交給發布流程寫入。
        geometry = {
            "mediabox_points": _box(mediabox),
            "cropbox_points": _box(cropbox),
            "visible_points": _box(visible),
            "rotation_degrees": page.rotation,
        }
        native_descriptor = {
            "schema": "s1-page-native/v1",
            "material_ref": material_ref,
            "page_ref": page_ref,
            "page_number": page_number,
            "geometry": geometry,
            "spans": spans,
            "images": images,
            "drawings": drawings,
        }
        native_bytes = json.dumps(
            native_descriptor,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return {
            "material_ref": material_ref,
            "page_ref": page_ref,
            "geometry": geometry,
            "native_bytes": native_bytes,
            "render_bytes": render_bytes,
            "render_width": pixmap.width,
            "render_height": pixmap.height,
            "coordinate_transform": coordinate_transform,
        }, None
    finally:
        document.close()


def _build_manifest(
    source_sha256: str, page_number: int, payload: dict[str, Any]
) -> dict[str, Any]:
    """綁定來源、原生描述與渲染雜湊並建立確定性manifest。"""
    native_sha256 = _sha256_bytes(payload["native_bytes"])
    render_sha256 = _sha256_bytes(payload["render_bytes"])
    evidence_hash = _sha256_bytes(
        f"{source_sha256}:{page_number}:{native_sha256}:{render_sha256}".encode("ascii")
    )
    manifest = {
        "schema": EVIDENCE_SCHEMA,
        "status": "succeeded",
        "reason": "EVIDENCE_READY",
        "material_ref": payload["material_ref"],
        "page_ref": payload["page_ref"],
        "evidence_ref": f"evidence:sha256:{evidence_hash}",
        "page_number": page_number,
        "hashes": {
            "source_sha256": source_sha256,
            "native_sha256": native_sha256,
            "render_sha256": render_sha256,
        },
        "geometry": payload["geometry"],
        "render": {
            "schema": RENDER_SCHEMA,
            "dpi": RENDER_DPI,
            "colorspace": "RGB",
            "format": "PNG",
            "alpha": False,
            "coverage": "full_visible_page",
            "width_pixels": payload["render_width"],
            "height_pixels": payload["render_height"],
        },
        "coordinate_transform": payload["coordinate_transform"],
        "provenance": {
            "python_version": platform.python_version(),
            "pymupdf_version": pymupdf.__version__,
            "mupdf_version": ".".join(str(value) for value in pymupdf.mupdf_version_tuple),
        },
    }
    return manifest


def _publish_and_verify(
    output_root: str | os.PathLike[str],
    payload: dict[str, Any],
    manifest: dict[str, Any],
) -> str | None:
    """先暫存並驗證產物，最後發布 manifest。"""
    try:
        root = Path(output_root)
    except TypeError:
        return "OUTPUT_ROOT_INVALID"

    stage_path: Path | None = None
    manifest_path: Path | None = None
    try:
        # 正式資料依 evidence hash 分目錄保存，本次寫入先放在 staging。
        staging_root = root / "staging"
        evidence_hash = manifest["evidence_ref"].removeprefix("evidence:sha256:")
        native_sha256 = manifest["hashes"]["native_sha256"]
        render_sha256 = manifest["hashes"]["render_sha256"]
        manifest_bytes = json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        output_directory = root / "output" / evidence_hash
        staging_root.mkdir(parents=True, exist_ok=True)
        output_directory.mkdir(parents=True, exist_ok=True)
        stage_path = Path(tempfile.mkdtemp(prefix="publish-", dir=staging_root))
        staged_native = stage_path / "native.json"
        staged_render = stage_path / "render.png"
        staged_manifest = stage_path / "manifest.json"

        # 先寫入兩個暫存產物，確認磁碟內容與 manifest 記錄的 hash 相同。
        _write_file(staged_native, payload["native_bytes"])
        _write_file(staged_render, payload["render_bytes"])
        if _sha256_file(staged_native) != native_sha256:
            return "NATIVE_HASH_MISMATCH"
        if _sha256_file(staged_render) != render_sha256:
            return "RENDER_HASH_MISMATCH"

        manifest_path = output_directory / "manifest.json"
        # 先移除舊的成功標記，再逐一替換正式產物，避免更新途中仍被視為成功。
        _remove_manifest(manifest_path)
        os.replace(staged_native, output_directory / "native.json")
        os.replace(staged_render, output_directory / "render.png")
        _fsync_directory(output_directory)

        # 兩個產物都就位後才發布 manifest；沒有 manifest 就不算完整成功結果。
        _write_file(staged_manifest, manifest_bytes)
        os.replace(staged_manifest, manifest_path)
        _fsync_directory(output_directory)
    except OSError:
        _remove_manifest(manifest_path)
        return "ATOMIC_PUBLISH_FAILED" if manifest_path is not None else "DISK_WRITE_FAILED"
    finally:
        if stage_path is not None:
            shutil.rmtree(stage_path, ignore_errors=True)
    return None


def build_page_evidence(
    pdf_path: str | os.PathLike[str],
    expected_source_sha256: str,
    page_number: int,
    output_root: str | os.PathLike[str],
) -> dict[str, Any]:
    """驗證單一 PDF 頁面，並在產物完整後發布 Page Evidence manifest。"""
    reason = _validate_request(expected_source_sha256, page_number)
    if reason is not None:
        return _failure(reason, page_number)

    source_path, source_sha256, reason = _verify_source(
        pdf_path, expected_source_sha256
    )
    if reason is not None:
        return _failure(reason, page_number)

    payload, reason = _extract_page_payload(source_path, source_sha256, page_number)
    if reason is not None:
        return _failure(reason, page_number)

    manifest = _build_manifest(source_sha256, page_number, payload)
    reason = _publish_and_verify(
        output_root,
        payload,
        manifest,
    )
    if reason is not None:
        return _failure(reason, page_number)
    return manifest
