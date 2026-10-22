"""Compose 的資料初始化與 API 入口；不管理主機程序或模型生命週期。"""

import argparse
import os
from pathlib import Path
import subprocess
from uuid import UUID

from .storage.database import configure_database

from .local_app import run_local_app
from .semantic_service import _origin, SERVICE_URL_ENV
from .storage.migrations import run_migrations
from .storage.baseline import BaselineError


def initialize_data(root: Path, uid: int, gid: int) -> None:
    if uid <= 0 or gid <= 0 or root.is_symlink():
        raise ValueError("CONTAINER_DATA_INVALID")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    os.chown(root, uid, gid)
    for relative, owner, group in (
        ("artifacts", uid, gid),
        ("models", uid, gid),
        ("models/unlimited-ocr", uid, gid),
        ("postgres", 999, 999),
    ):
        path = root / relative
        if path.is_symlink():
            raise ValueError("CONTAINER_DATA_INVALID")
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.chmod(0o700)
        os.chown(path, owner, group)


def check_sandbox() -> None:
    result = subprocess.run(
        ["bwrap", "--unshare-all", "--ro-bind", "/", "/", "/usr/bin/true"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("CONTAINER_SANDBOX_UNAVAILABLE")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("init", "migrate", "serve", "prepare-read-indexes", "queue-chapters", "fill-podcast-titles"),
    )
    # queue-chapters：限定教材（可重複）；--requeue 連已完成的章節也重新排入。
    parser.add_argument("--material", action="append", type=UUID, default=[])
    parser.add_argument("--requeue", action="store_true")
    arguments = parser.parse_args()
    command = arguments.command
    stage = "configuration"
    try:
        if command == "init":
            stage = "data_initialization"
            initialize_data(
                Path("/data"),
                int(os.environ["STUDYDY_UID"]),
                int(os.environ["STUDYDY_GID"]),
            )
            return
        if command == "serve":
            stage = "semantic_configuration"
            _origin(os.environ[SERVICE_URL_ENV])
            stage = "sandbox"
            check_sandbox()
        stage = "database_configuration"
        configure_database(os.environ)
        # 啟動時核對已套用的 checksum，再以交易執行待套用的 migration。
        stage = "migrations"
        run_migrations()
        if command == "migrate":
            return
        if command == "prepare-read-indexes":
            from .storage.knowledge_reads import backfill

            stage = "read_indexes"
            print(f"Prepared read indexes: {backfill()}", flush=True)
            return
        if command == "queue-chapters":
            from .knowledge_chapters import queue_existing

            stage = "chapter_queue"
            for material_id, action in queue_existing(
                material_ids=arguments.material or None, requeue=arguments.requeue
            ):
                print(f"{material_id} {action}", flush=True)
            return
        if command == "fill-podcast-titles":
            from .podcast_titles import fill_missing

            stage = "podcast_titles"
            filled, failed = fill_missing()
            print(f"Podcast titles filled={filled} failed={failed}", flush=True)
            return
        stage = "api"
        run_local_app(host="0.0.0.0", port=8001)
    except BaselineError as error:
        print(f"BASELINE_FAILED: {error}", flush=True)
        raise SystemExit(1) from None
    except Exception as error:
        # 不把含密碼的 conninfo、模型位址或 SQL 例外輸出至容器 log。
        print(
            f"CONTAINER_OPERATION_FAILED stage={stage} error_type={type(error).__name__}",
            flush=True,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
