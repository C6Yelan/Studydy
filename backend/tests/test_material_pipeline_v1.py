import hashlib
import json
from pathlib import Path

import pymupdf
import httpx

import pdf_evidence.material_pipeline as pipeline


class Client:
    def post(self, url, **kwargs):
        assert url.endswith("/tokenize")
        return httpx.Response(200, json={"count": 100, "max_model_len": 32768}, request=httpx.Request("POST", url))


def _settings(tmp_path: Path) -> dict:
    lock = json.loads((Path(__file__).parents[2] / "local_ai/runtime-lock.json").read_text())
    return {
        "private_runtime_root": str(tmp_path / "runtime"),
        "runtime_lock": lock,
        "python_executable": str(tmp_path / "ocr/runtime/bin/python3.12"),
        "site_packages": str(tmp_path / "ocr/runtime/lib/python3.12/site-packages"),
        "ocr_model_root": str(tmp_path / "models/unlimited-ocr"),
    }


def _pdf(path: Path, pages: int, *, blank_first: bool = False) -> None:
    document = pymupdf.open()
    for page_number in range(1, pages + 1):
        page = document.new_page(width=612, height=792)
        if not (blank_first and page_number == 1):
            if page_number == 1:
                page.insert_text((72, 72), "Public Algorithms", fontsize=20)
            page.insert_text((72, 120), f"Public lesson {page_number} explains a deterministic learning concept with evidence.", fontsize=12)
    document.save(path)
    document.close()


def _request(path: Path) -> dict:
    return {
        "media_type": "application/pdf",
        "source_path": str(path),
        "expected_source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _semantic(calls: list[dict]):
    def call(_client, **arguments):
        request = arguments["request"]
        calls.append(request)
        first = next(item for section in request["sections"] for item in section["evidence"] if item[2] != "heading")
        return {
            "concepts": [{
                "k": "algorithm", "l": "Algorithm", "a": [],
                "c": [{"m": None, "s": [[first[0], 0, 0]]}],
            }],
            "relations": [],
        }
    return call


def test_eight_native_pages_use_one_unified_semantic_call_without_ocr(tmp_path, monkeypatch):
    source = tmp_path / "eight.pdf"
    _pdf(source, 8)
    monkeypatch.setattr(pipeline, "start_ocr_process", lambda _settings: (_ for _ in ()).throw(AssertionError("native PDF must not load OCR")))
    calls: list[dict] = []
    structure = pipeline.analyze_material(
        _request(source), _settings(tmp_path), client=Client(), semantic_call=_semantic(calls)
    )
    assert structure["metrics"]["semantic_calls"] == 1
    assert structure["metrics"]["ocr_calls"] == 0
    assert len(calls) == 1
    assert len({item[1] for section in calls[0]["sections"] for item in section["evidence"]}) == 8
    assert structure["initial_learning_path"][0]["concept_id"] == structure["concepts"][0]["concept_id"]


class FailedOcr:
    def request(self, _request, _timeout):
        raise pipeline.LocalAIError("CHILD_EXITED")

    def close(self):
        pass

    def abort(self):
        pass


def test_ocr_failure_excludes_only_scan_and_semantics_still_runs(tmp_path, monkeypatch):
    source = tmp_path / "mixed.pdf"
    _pdf(source, 2, blank_first=True)
    monkeypatch.setattr(pipeline, "start_ocr_process", lambda _settings: FailedOcr())
    calls: list[dict] = []
    structure = pipeline.analyze_material(
        _request(source), _settings(tmp_path), client=Client(), semantic_call=_semantic(calls)
    )
    assert structure["metrics"]["ocr_calls"] == 1
    assert structure["metrics"]["semantic_calls"] == 1
    assert structure["status"]["processing"] == "partial"
    assert structure["excluded_pages"] == [{
        "page_ref": structure["excluded_pages"][0]["page_ref"],
        "page": 1,
        "stage": "evidence",
        "reason_code": "CHILD_EXITED",
    }]
    assert {item[1] for section in calls[0]["sections"] for item in section["evidence"]} == {2}
