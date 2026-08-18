import runtime.storage.artifacts as resource_artifacts
from runtime.storage.source_pdf import (
    PublishedSourcePdf,
    VerifiedSourcePdf,
    open_verified_source_pdf,
    publish_idempotent_source_pdf,
)


def test_source_pdf_has_neutral_dedicated_surface():
    assert PublishedSourcePdf.__name__ == "PublishedSourcePdf"
    assert VerifiedSourcePdf.__name__ == "VerifiedSourcePdf"
    assert callable(open_verified_source_pdf)
    assert callable(publish_idempotent_source_pdf)
    assert not hasattr(resource_artifacts, "open_verified_source_pdf")
    assert not hasattr(resource_artifacts, "publish_idempotent_source_pdf")
