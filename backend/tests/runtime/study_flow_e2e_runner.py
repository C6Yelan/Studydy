from __future__ import annotations

from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import sys
import threading

import pymupdf

import full_stack_e2e_harness
from pdf_evidence.pipeline.transport import _canonical_sha256
from runtime.material_processing import ControlledResourceUpload


_ORIGINAL_CREATE_APP = full_stack_e2e_harness.create_app


def _safe_pdf() -> bytes:
    document = pymupdf.open()
    page = document.new_page(width=420, height=600)
    page.insert_text((40, 60), "Study Flow Topic", fontsize=16)
    page.insert_text((40, 110), "Grounded study flow explanation.", fontsize=11)
    content = document.tobytes()
    document.close()
    return content


class _StudyFlowProvider:
    """提供真後端學習流程所需的本機 deterministic structured output。"""

    def __init__(self) -> None:
        owner = self

        class RequestHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                if self.path != "/v1/structured-generation":
                    self.send_error(404)
                    return
                request_body = json.loads(
                    self.rfile.read(int(self.headers["Content-Length"]))
                )
                operation = request_body["operation"]
                try:
                    output = owner._output(operation, request_body["payload"])
                except Exception:
                    self.send_error(500)
                    return
                response = {
                    "schema": "structured-generation-response/v1",
                    "request_id": request_body["request_id"],
                    "operation": operation,
                    "runtime_binding_sha256": _canonical_sha256(
                        request_body["runtime_binding"]
                    ),
                    "output": output,
                }
                encoded = json.dumps(response).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, _format: str, *_arguments: object) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), RequestHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    @staticmethod
    def _output(operation: str, payload: dict) -> dict:
        if operation == "page_structure":
            # bbox 精確對應安全 PDF native span，先換算成模型契約的 normalized_render_1000。
            return {
                "elements": [
                    {
                        "id": "heading-1",
                        "type": "heading",
                        "bbox": [
                            95.23809523809523,
                            71.33333206176758,
                            387.3905000232515,
                            107.97332763671875,
                        ],
                        "text": "Study Flow Topic",
                    },
                    {
                        "id": "paragraph-1",
                        "type": "paragraph",
                        "bbox": [
                            95.23809523809523,
                            146.95833841959634,
                            483.9308602469308,
                            172.1483357747396,
                        ],
                        "text": "Grounded study flow explanation.",
                    },
                ],
                "reading_order": ["heading-1", "paragraph-1"],
                "spatial_relations": [],
            }
        if operation == "visual_alignment_adjudication":
            return {"decision": "retain"}
        if operation == "concept_candidate":
            context = payload["concept_context"]
            return {
                "name": context["elements"][0]["text"],
                "definition": context["elements"][1]["text"],
                "scope": "This source page.",
                "evidence_ids": [item["evidence_id"] for item in context["evidence"]],
            }
        if operation == "concept_content":
            context = payload["summary_context"]
            evidence_id = context["groups"][0]["members"][0]["evidence_ids"][0]
            return {
                "summary": "Grounded summary for the supplied concepts.",
                "summary_evidence_ids": [evidence_id],
                "relation_clues": [],
            }
        raise ValueError("UNKNOWN_TEST_PROVIDER_OPERATION")

    def __enter__(self) -> "_StudyFlowProvider":
        self.thread.start()
        return self

    def __exit__(self, *_arguments: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def _study_resources(subject: str) -> tuple[ControlledResourceUpload, ...]:
    if subject not in {"data_structures", "economics"}:
        return ()
    return (
        ControlledResourceUpload(
            title="Study Flow Topic reference",
            topics=["Study Flow Topic"],
            keywords=["Study Flow Topic"],
            source_locator=f"https://example.edu/{subject}/study-flow.pdf",
            license_status="cc_by",
            use_boundary="attribution_required",
            checked_at="2026-08-15T00:00:00+08:00",
            learning_use="supplemental",
            source=io.BytesIO(_safe_pdf()),
        ),
    )


def _create_test_app(settings, _resource_supplier):
    return _ORIGINAL_CREATE_APP(settings, _study_resources)


def _backend_child(endpoint: str) -> int:
    original_settings = full_stack_e2e_harness.ApiSettings

    def provider_settings(
        environment, origin, secure_cookie, local_config, worker_interval, dsn
    ):
        configured = deepcopy(local_config)
        configured["endpoint_url"] = endpoint
        return original_settings(environment, origin, secure_cookie, configured, worker_interval, dsn)

    full_stack_e2e_harness.ApiSettings = provider_settings
    return full_stack_e2e_harness.main()


def main() -> int:
    full_stack_e2e_harness.create_app = _create_test_app
    full_stack_e2e_harness.__file__ = str(Path(__file__).resolve())
    if "--backend-child" in sys.argv:
        endpoint = os.environ.get("STUDYDY_E2E_PROVIDER_ENDPOINT", "")
        if not endpoint.startswith("http://127.0.0.1:"):
            return 2
        return _backend_child(endpoint)
    with _StudyFlowProvider() as provider:
        os.environ["STUDYDY_E2E_PROVIDER_ENDPOINT"] = provider.endpoint
        try:
            return full_stack_e2e_harness.main()
        finally:
            os.environ.pop("STUDYDY_E2E_PROVIDER_ENDPOINT", None)


if __name__ == "__main__":
    raise SystemExit(main())
