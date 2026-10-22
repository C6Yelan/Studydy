"""OCR 執行次數包含局部重辨識，發布後仍須原值讀回。"""

from copy import deepcopy
from io import BytesIO
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from product_fixtures import (
    ORIGIN, _app, closed_loop, publish_fixture_structure, seed_pdf, seed_run,
)
from runtime import material_processing as processing
from runtime.storage.database import database_session
from runtime.storage.tables import MaterialProcessingRun
from test_ocr_recovery import Archive, fixture_data
from test_material_pipeline_v1 import Client, _request, _settings
from pdf_evidence import material_pipeline as pipeline, ocr_recovery


def test_single_page_recovery_survives_publication_and_run_api(closed_loop, tmp_path, monkeypatch):
    learner, _, _, _, dsn, token = closed_loop
    source, saved, binding = fixture_data(tmp_path)
    settings = _settings(tmp_path)
    original = seed_pdf(learner.learner_id, BytesIO(source.read_bytes()), "ocr-recovery", dsn=dsn)
    run = seed_run(learner.learner_id, original.material_id, "ocr-run", settings, dsn=dsn)
    claim = processing.claim_next_material_processing_run(dsn=dsn)
    assert claim.run.run_id == run.run_id
    requests = []

    class OCR:
        def request(self, request, timeout, **kwargs):
            requests.append(request)
            return {
                "schema": "local-ocr-response/v1", "request_id": request["request_id"],
                "blocks": [{"type": "table", "bbox": [0, 0, 1000, 1000], "text": "Recovered table content."}],
            }
        def close(self): pass
        def abort(self): pass

    monkeypatch.setattr(ocr_recovery, "start_ocr_process", lambda _: OCR())
    monkeypatch.setattr(pipeline, "material_request_fits", lambda client, lock, request, **kwargs:
                        all(len(row[3]) < 100 for section in request["sections"] for row in section["evidence"]))
    result = pipeline.analyze_material(
        [_request(source)], binding, settings, run_id=str(run.run_id),
        client=Client(), analysis_archive=Archive(saved),
        semantic_call=lambda *args, **kwargs: {
            "concepts": [{"k": "continued", "l": "Continued", "a": [], "c": [{"m": None, "s": [1]}]}],
            "relations": [],
        },
    )
    assert len(requests) == 1 and result["metrics"]["ocr_calls"] == 2
    for stage in ("evidence", "semantics", "publishing"):
        processing._record_progress(run.run_id, stage, 1, 1, dsn=dsn)
    published = publish_fixture_structure(learner.learner_id, original.material_id, run.run_id, result, dsn=dsn)
    assert published.document["metrics"]["ocr_calls"] == 2
    stored = processing.read_material_processing_run(learner.learner_id, run.run_id, dsn=dsn)
    assert stored.output_binding["ocr_calls"] == 2
    client = TestClient(_app(dsn, tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set("studydy_session", token)
    response = client.get(f"/v1/material-processing-runs/{run.run_id}")
    assert response.status_code == 200, response.json()
    assert response.json()["output_binding"]["page_count"] == 1
    assert response.json()["status"] == published.document["status"]["processing"]


@pytest.mark.parametrize("count", [0, 2, -1, True, 1.5, "2"])
def test_completed_run_accepts_only_nonnegative_integer_ocr_counts(closed_loop, count):
    learner, _, _, document, dsn, _ = closed_loop
    with database_session(dsn) as db:
        row = db.get(MaterialProcessingRun, UUID(document["run_id"]))
        row.output_binding = {**deepcopy(row.output_binding), "ocr_calls": count}
        if type(count) is int and count >= 0:
            assert processing._row(row).output_binding["ocr_calls"] == count
        else:
            with pytest.raises(processing.MaterialProcessingError, match="^MATERIAL_RUN_INVALID$"):
                processing._row(row)
        db.rollback()
