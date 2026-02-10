import os
from pathlib import Path

MIN_PASSWORD_LENGTH = 8
VERIFICATION_CODE_LENGTH = 6
VERIFICATION_CODE_EXPIRE_MINUTES = 10
PBKDF2_ITERATIONS = 100_000


def _normalize_ext_list(raw: str) -> list[str]:
    exts: list[str] = []
    for item in raw.split(","):
        ext = item.strip().lower()
        if not ext:
            continue
        if not ext.startswith("."):
            ext = f".{ext}"
        exts.append(ext)
    return exts


_BACKEND_DIR = Path(__file__).resolve().parents[2]
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(_BACKEND_DIR / "uploads")))
ALLOWED_UPLOAD_EXTS = _normalize_ext_list(
    os.getenv("ALLOWED_UPLOAD_EXTS", ".pdf,.docx,.pptx")
)
