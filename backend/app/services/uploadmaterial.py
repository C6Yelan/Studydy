from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile, status


def save_upload_file(
    upload: UploadFile,
    upload_dir: Path,
    allowed_exts: set[str],
) -> tuple[Path, str, str, str, int]:
    if not upload.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing filename",
        )

    original_name = Path(upload.filename).name
    ext = Path(original_name).suffix.lower()
    if not ext:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported file type",
        )

    normalized_allowed = {e.strip().lower() for e in allowed_exts if e.strip()}
    if ext not in normalized_allowed:
        allowed_msg = ", ".join(sorted(normalized_allowed))
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file type. Allowed: {allowed_msg}",
        )

    try:
        upload_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create upload directory",
        ) from e

    stored_name = f"{uuid.uuid4().hex}{ext}"
    stored_path = upload_dir / stored_name

    # UploadFile wraps a SpooledTemporaryFile; read from the underlying file object.
    try:
        upload.file.seek(0)
    except Exception:
        pass

    size = 0
    try:
        with stored_path.open("wb") as out_file:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                out_file.write(chunk)
    except Exception as e:
        try:
            stored_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to store uploaded file",
        ) from e

    return stored_path, original_name, stored_name, ext, size

