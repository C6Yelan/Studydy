"""PDF extractor implementation."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from pypdf import PdfReader

from app.services.extraction.base import BaseExtractor
from app.services.extraction.models import ExtractionWarning, Segment


class PdfExtractor(BaseExtractor):
    def extract(
        self,
        file_path: Path,
        source_path: str,
        doc_uid: str,
        extracted_at: str,
    ) -> tuple[list[Segment], list[ExtractionWarning]]:
        reader = PdfReader(str(file_path))
        segments: list[Segment] = []
        warnings: list[ExtractionWarning] = []

        for page_index, page in enumerate(reader.pages, start=1):
            locator = {"page": page_index}
            try:
                text = page.extract_text() or ""
            except Exception as exc:
                text = ""
                warnings.append(
                    ExtractionWarning(
                        code="pdf_text_extract_error",
                        message=f"Page text extraction raised error: {exc}",
                        locator=locator,
                    )
                )
            segments.append(
                Segment(
                    segment_id=str(uuid4()),
                    doc_uid=doc_uid,
                    source_path=source_path,
                    file_type="pdf",
                    unit_type="page",
                    unit_index=page_index,
                    text=text,
                    locator=locator,
                    extracted_at=extracted_at,
                )
            )
            if not text.strip():
                warnings.append(
                    ExtractionWarning(
                        code="empty_text_page",
                        message=(
                            "No extractable text in page; this may be a scanned or image-based PDF."
                        ),
                        locator=locator,
                    )
                )

        if not segments:
            warnings.append(
                ExtractionWarning(
                    code="pdf_no_pages",
                    message="PDF contains no pages.",
                )
            )

        return segments, warnings
