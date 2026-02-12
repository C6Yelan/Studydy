from __future__ import annotations

from python_multipart.multipart import MultipartParser, QuerystringParser

# Match FastAPI/Starlette checks for installed multipart support.
__version__ = "0.0.999"

__all__ = ["__version__", "MultipartParser", "QuerystringParser"]

