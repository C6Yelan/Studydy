"""Base interfaces for document extractors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from app.services.extraction.models import ExtractionWarning, Segment


class BaseExtractor(ABC):
    """Contract for file-type specific extractors."""

    @abstractmethod
    def extract(
        self,
        file_path: Path,
        source_path: str,
        doc_uid: str,
        extracted_at: str,
    ) -> tuple[list[Segment], list[ExtractionWarning]]:
        """Extract segments and non-fatal warnings from a document."""
