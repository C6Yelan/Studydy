"""在 OCR 容器內下載固定 snapshot，不載入模型，也不覆寫未知的模型目錄。"""

import argparse
import json
from pathlib import Path
import re


def download_ocr(root: Path, revision: str) -> None:
    from huggingface_hub import snapshot_download

    marker = root / ".studydy-revision"
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError("OCR_REVISION_INVALID")
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink() or marker.is_symlink() or (marker.exists() and marker.read_text() != revision):
        raise ValueError("OCR_TARGET_NOT_EMPTY")
    if not marker.exists():
        if any(root.iterdir()):
            raise ValueError("OCR_TARGET_NOT_EMPTY")
        marker.write_text(revision)
    snapshot_download("baidu/Unlimited-OCR", revision=revision, local_dir=root, ignore_patterns=["*.pdf"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        lock = json.loads(args.lock.read_text())
        download_ocr(args.output, lock["ocr"]["revision"])
    except Exception:
        print("OCR_DOWNLOAD_FAILED: check target directory, model revision and network", flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
