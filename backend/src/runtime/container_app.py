"""Compose 的資料初始化與 API 入口；不管理主機程序或模型生命週期。"""

import argparse
import os
from pathlib import Path
import subprocess

from psycopg.conninfo import make_conninfo

from .local_app import run_local_app
from .semantic_service import _origin, SERVICE_URL_ENV
from .storage.migrations import run_migrations


def initialize_data(root: Path, uid: int, gid: int) -> None:
    if uid <= 0 or gid <= 0 or root.is_symlink():
        raise ValueError("CONTAINER_DATA_INVALID")
    for relative, owner, group in (
        ("artifacts", uid, gid), ("models", uid, gid),
        ("models/unlimited-ocr", uid, gid), ("postgres", 999, 999),
    ):
        path = root / relative
        if path.is_symlink():
            raise ValueError("CONTAINER_DATA_INVALID")
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.chmod(0o700)
        os.chown(path, owner, group)


def configure_database(environment) -> None:
    password = environment["POSTGRES_PASSWORD"]
    if not password:
        raise ValueError("CONTAINER_DATABASE_INVALID")
    os.environ["STUDYDY_DATABASE_DSN"] = make_conninfo(
        host="postgres", port="5432", dbname=environment["POSTGRES_DB"],
        user=environment["POSTGRES_USER"], password=password, connect_timeout="5",
    )


def check_sandbox() -> None:
    result = subprocess.run(
        ["bwrap", "--unshare-all", "--ro-bind", "/", "/", "/usr/bin/true"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=5, check=False,
    )
    if result.returncode:
        raise RuntimeError("CONTAINER_SANDBOX_UNAVAILABLE")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "migrate", "serve"))
    command = parser.parse_args().command
    try:
        if command == "init":
            initialize_data(Path("/data"), int(os.environ["STUDYDY_UID"]), int(os.environ["STUDYDY_GID"]))
            return
        if command == "serve":
            _origin(os.environ[SERVICE_URL_ENV])
            check_sandbox()
        configure_database(os.environ)
        # 新環境直接套用 schema；既有 schema 仍經原有 checksum／交易檢查。
        run_migrations()
        if command == "migrate":
            return
        run_local_app(host="0.0.0.0", port=8001)
    except Exception:
        # 不把含密碼的 conninfo、模型位址或 SQL 例外輸出至容器 log。
        print("CONTAINER_OPERATION_FAILED: check configuration, database and sandbox", flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
