import hashlib
import json
import math
import os
from pathlib import Path

import pymupdf

from pdf_evidence.page_evidence import _build_page_evidence


def _sha256(path):
    """計算 synthetic fixture 或 artifact 的 SHA-256。"""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _make_pdf(root, name="fixture.pdf"):
    """建立含文字、圖形、圖片與旋轉頁的 synthetic PDF。"""
    path = root / name
    document = pymupdf.open()
    first = document.new_page(width=144, height=216)
    first.insert_text((18, 30), "Synthetic page one")
    first.draw_rect(pymupdf.Rect(18, 45, 90, 90))
    second = document.new_page(width=216, height=144)
    second.set_rotation(90)
    second.insert_text((18, 30), "Synthetic page two")
    second.draw_line((18, 45), (90, 90))
    image = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 2, 2), False)
    image.clear_with(0x33AA66)
    second.insert_image(pymupdf.Rect(105, 45, 125, 65), pixmap=image)
    document.save(path)
    document.close()
    return path


def _final_manifests(output_root):
    """列出測試輸出中的可讀 final manifests。"""
    return list((Path(output_root) / "output").glob("*/manifest.json"))


def test_builds_identity_hashes_native_render_transform_and_manifest_last(tmp_path):
    """驗證成功頁面的identity、hash、transform與manifest-last發布。"""
    pdf = _make_pdf(tmp_path)
    output_root = tmp_path / "runtime"

    result = _build_page_evidence(pdf, _sha256(pdf), 2, output_root)

    assert result["status"] == "succeeded"
    assert result["schema"] == "page-evidence/v1"
    assert result["page_number"] == 2
    assert set(result) == {
        "schema",
        "status",
        "reason",
        "material_ref",
        "page_ref",
        "evidence_ref",
        "page_number",
        "hashes",
        "geometry",
        "render",
        "coordinate_transform",
        "provenance",
    }
    assert result["material_ref"].startswith("material:sha256:")
    assert result["page_ref"].startswith("page:sha256:")
    assert set(result["hashes"]) == {
        "source_sha256",
        "native_sha256",
        "render_sha256",
    }
    assert result["hashes"]["source_sha256"] == _sha256(pdf)
    render = result["render"]
    assert set(render) == {
        "schema",
        "dpi",
        "colorspace",
        "format",
        "alpha",
        "coverage",
        "width_pixels",
        "height_pixels",
    }
    assert render["schema"] == "page-render/v1"
    assert render["dpi"] == 200
    assert render["colorspace"] == "RGB"
    assert render["format"] == "PNG"
    assert render["alpha"] is False
    assert render["coverage"] == "full_visible_page"
    assert render["width_pixels"] > 0
    assert render["height_pixels"] > 0
    assert set(result["provenance"]) == {
        "python_version",
        "pymupdf_version",
        "mupdf_version",
    }
    transform = result["coordinate_transform"]
    assert set(transform) == {
        "native_coordinate_space",
        "render_coordinate_space",
        "point_to_rotated",
        "rotated_to_point",
    }
    assert transform["native_coordinate_space"] == "unrotated_page_points"
    assert transform["render_coordinate_space"] == "rotated_page_points"
    assert len(transform["point_to_rotated"]) == 6
    assert len(transform["rotated_to_point"]) == 6
    assert all(math.isfinite(value) for value in transform["point_to_rotated"])
    assert all(math.isfinite(value) for value in transform["rotated_to_point"])

    manifests = _final_manifests(output_root)
    assert len(manifests) == 1
    assert json.loads(manifests[0].read_text(encoding="utf-8")) == result
    native_path = manifests[0].parent / "native.json"
    render_path = manifests[0].parent / "render.png"
    assert _sha256(native_path) == result["hashes"]["native_sha256"]
    assert _sha256(render_path) == result["hashes"]["render_sha256"]
    native = json.loads(native_path.read_text(encoding="utf-8"))
    assert native["page_number"] == 2
    assert native["spans"]
    assert native["images"]
    assert native["drawings"]

    empty_pdf = tmp_path / "empty.pdf"
    empty_document = pymupdf.open()
    empty_document.new_page(width=144, height=216)
    empty_document.save(empty_pdf)
    empty_document.close()
    empty_root = tmp_path / "empty-runtime"
    empty_result = _build_page_evidence(empty_pdf, _sha256(empty_pdf), 1, empty_root)
    assert empty_result["status"] == "succeeded"
    empty_manifest = _final_manifests(empty_root)[0]
    empty_native = json.loads(
        (empty_manifest.parent / "native.json").read_text(encoding="utf-8")
    )
    assert empty_native["spans"] == []


def test_render_coordinate_write_and_publish_fail_closed(tmp_path, monkeypatch):
    """驗證coordinate、render、寫入與發布失敗不留下final manifest。"""
    pdf = _make_pdf(tmp_path)
    source_hash = _sha256(pdf)

    def fail_render(page, *args, **kwargs):
        """模擬頁面渲染失敗。"""
        raise RuntimeError("render failed")

    with monkeypatch.context() as context:
        context.setattr(pymupdf.Page, "get_pixmap", fail_render)
        render_root = tmp_path / "render-failure"
        result = _build_page_evidence(pdf, source_hash, 1, render_root)
    assert result["reason"] == "RENDER_FAILED"
    assert _final_manifests(render_root) == []

    def outside_page_span(page, *args, **kwargs):
        """模擬與頁面區域不相交的原生文字bbox。"""
        return {
            "blocks": [
                {
                    "lines": [
                        {
                            "spans": [
                                {"bbox": [1000.0, 1000.0, 1010.0, 1010.0], "text": "x"}
                            ]
                        }
                    ]
                }
            ]
        }

    with monkeypatch.context() as context:
        context.setattr(pymupdf.Page, "get_text", outside_page_span)
        coordinate_root = tmp_path / "coordinate-failure"
        result = _build_page_evidence(pdf, source_hash, 1, coordinate_root)
    assert result["reason"] == "COORDINATE_VALIDATION_FAILED"
    assert _final_manifests(coordinate_root) == []

    disk_target = tmp_path / "not-a-directory"
    disk_target.write_text("occupied", encoding="utf-8")
    result = _build_page_evidence(pdf, source_hash, 1, disk_target)
    assert result["reason"] == "DISK_WRITE_FAILED"
    assert _final_manifests(disk_target) == []

    original_replace = os.replace

    def fail_manifest_publish(source, destination):
        """模擬final manifest的原子發布失敗。"""
        if Path(destination).name == "manifest.json":
            raise OSError("synthetic atomic publish failure")
        return original_replace(source, destination)

    publish_root = tmp_path / "publish-failure"
    with monkeypatch.context() as context:
        context.setattr(os, "replace", fail_manifest_publish)
        result = _build_page_evidence(pdf, source_hash, 1, publish_root)
    assert result["reason"] == "ATOMIC_PUBLISH_FAILED"
    assert _final_manifests(publish_root) == []


def test_actual_evidence_target_symlink_fails_closed(tmp_path):
    """本頁 digest target 是 symlink 時不得把產物寫到 root 外。"""
    pdf = _make_pdf(tmp_path)
    source_hash = _sha256(pdf)
    baseline = _build_page_evidence(pdf, source_hash, 1, tmp_path / "baseline")
    digest = baseline["evidence_ref"].removeprefix("evidence:sha256:")
    output_root = tmp_path / "target-check"
    target = output_root / "output" / digest
    target.parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    target.symlink_to(outside, target_is_directory=True)

    result = _build_page_evidence(pdf, source_hash, 1, output_root)

    assert result["reason"] == "ATOMIC_PUBLISH_FAILED"
    assert list(outside.iterdir()) == []


def test_excludes_only_zero_area_native_spans(tmp_path, monkeypatch):
    """驗證零面積 span 不阻斷頁面，且有效文字與 bbox 保持原值。"""
    pdf = _make_pdf(tmp_path)
    source_hash = _sha256(pdf)

    def spans_with_zero_area(page, *args, **kwargs):
        """回傳一個有效 span 與兩個無法形成區域的 span。"""
        return {
            "blocks": [
                {
                    "lines": [
                        {
                            "spans": [
                                {
                                    "bbox": [18.0, 18.0, 72.0, 30.0],
                                    "text": "Grounded text",
                                },
                                {
                                    "bbox": [40.0, 40.0, 40.0, 52.0],
                                    "text": "Zero width",
                                },
                                {
                                    "bbox": [60.0, 60.0, 80.0, 60.0],
                                    "text": "Zero height",
                                },
                            ]
                        }
                    ]
                }
            ]
        }

    with monkeypatch.context() as context:
        context.setattr(pymupdf.Page, "get_text", spans_with_zero_area)
        output_root = tmp_path / "zero-area-runtime"
        result = _build_page_evidence(pdf, source_hash, 1, output_root)

    assert result["status"] == "succeeded"
    manifest = _final_manifests(output_root)[0]
    native = json.loads(
        (manifest.parent / "native.json").read_text(encoding="utf-8")
    )
    assert native["spans"] == [
        {"bbox": [18.0, 18.0, 72.0, 30.0], "text": "Grounded text"}
    ]


def test_rejects_structurally_invalid_native_span_bbox(tmp_path, monkeypatch):
    """驗證反向或缺少座標的 span bbox 仍維持 fail closed。"""
    pdf = _make_pdf(tmp_path)
    source_hash = _sha256(pdf)
    invalid_bbox = [40.0, 40.0, 39.0, 52.0]

    def invalid_span(page, *args, **kwargs):
        """回傳結構性無效的原生 span bbox。"""
        return {
            "blocks": [
                {
                    "lines": [
                        {"spans": [{"bbox": invalid_bbox, "text": "Invalid"}]}
                    ]
                }
            ]
        }

    with monkeypatch.context() as context:
        context.setattr(pymupdf.Page, "get_text", invalid_span)
        output_root = tmp_path / "invalid-span-runtime"
        result = _build_page_evidence(pdf, source_hash, 1, output_root)

    assert result["status"] == "failed"
    assert result["reason"] == "COORDINATE_VALIDATION_FAILED"
    assert _final_manifests(output_root) == []
