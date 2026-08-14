"""建立與驗證 runtime 資料庫連線。"""

from __future__ import annotations

import os
from typing import Any

import psycopg
from psycopg import Connection
from psycopg.conninfo import conninfo_to_dict

DATABASE_DSN_ENV = "STUDYDY_DATABASE_DSN"


class DatabaseConfigurationError(RuntimeError):
    """資料庫設定缺漏或格式無效。"""


class DatabaseConnectionError(RuntimeError):
    """資料庫連線失敗；訊息不包含 DSN。"""


def resolve_database_dsn(dsn: str | None = None) -> str:
    """取得並驗證 DSN，但不回報可能含密碼的原始錯誤。"""

    resolved_dsn = dsn if dsn is not None else os.environ.get(DATABASE_DSN_ENV)
    if resolved_dsn is None or not resolved_dsn.strip():
        raise DatabaseConfigurationError("DATABASE_DSN_MISSING")

    try:
        conninfo_to_dict(resolved_dsn)
    except (psycopg.ProgrammingError, TypeError, ValueError):
        raise DatabaseConfigurationError("DATABASE_DSN_INVALID") from None
    return resolved_dsn


def connect_database(
    dsn: str | None = None,
    *,
    autocommit: bool = False,
) -> Connection[Any]:
    """建立 PostgreSQL 連線，並以固定錯誤避免洩漏連線資訊。"""

    resolved_dsn = resolve_database_dsn(dsn)
    try:
        return psycopg.connect(resolved_dsn, autocommit=autocommit)
    except psycopg.Error:
        raise DatabaseConnectionError("DATABASE_CONNECTION_FAILED") from None
