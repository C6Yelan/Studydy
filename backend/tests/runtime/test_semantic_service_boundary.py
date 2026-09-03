from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[3]


def test_backend_has_no_qwen_process_ownership_path():
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "backend" / "src").rglob("*.py")
    )

    for obsolete_name in (
        "start_concept_server",
        "LocalConceptServer",
        "concept_server_executable",
        "concept_model_root",
        "concept_site_packages",
        "concept_kv_cache_bytes",
        "process_guard",
    ):
        assert obsolete_name not in source
    assert not (
        ROOT / "backend" / "src" / "pdf_evidence" / "process_guard.py"
    ).exists()


def test_all_semantic_service_consumers_use_the_authenticated_client_factory():
    expected_consumers = {
        "backend/src/pdf_evidence/text_first_run.py",
        "backend/src/knowledge_map/local_generation.py",
        "backend/src/learning_adaptation/assessment_generation.py",
    }
    for relative_path in expected_consumers:
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "semantic_service_client" in source
