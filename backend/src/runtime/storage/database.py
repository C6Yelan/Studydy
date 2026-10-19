from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from threading import Lock
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.engine import Engine
from typing import Any

import psycopg
from psycopg import Connection
from psycopg.conninfo import conninfo_to_dict, make_conninfo

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


def configure_database(environment) -> None:
    password = environment["POSTGRES_PASSWORD"]
    if not password:
        raise ValueError("CONTAINER_DATABASE_INVALID")
    os.environ["STUDYDY_DATABASE_DSN"] = make_conninfo(
        host="postgres",
        port="5432",
        dbname=environment["POSTGRES_DB"],
        user=environment["POSTGRES_USER"],
        password=password,
        connect_timeout="5",
    )


def database_ready(dsn: str | None = None) -> bool:
    """健康檢查只做有界連線與 SELECT；不等待工作交易或觸發模型。"""
    try:
        with psycopg.connect(
            resolve_database_dsn(dsn), connect_timeout=2,
            options="-c statement_timeout=1000", autocommit=True,
        ) as connection:
            return connection.execute("SELECT 1").fetchone() == (1,)
    except (psycopg.Error, DatabaseConfigurationError):
        return False


_database_engines: dict[str, Engine] = {}


_database_engine_lock = Lock()


def _database_engine(resolved: str) -> Engine:
    # 每個程序／DSN 共用連線池；Session 與交易仍按每次操作建立。
    with _database_engine_lock:
        engine = _database_engines.get(resolved)
        if engine is None:
            engine = create_engine(
                "postgresql+psycopg://",
                creator=lambda: psycopg.connect(resolved, connect_timeout=5),
                pool_size=5,
                max_overflow=10,
                pool_timeout=5,
                pool_pre_ping=True,
                hide_parameters=True,
            )
            _database_engines[resolved] = engine
        return engine


def dispose_database_engine(dsn: str) -> None:
    """釋放指定環境的池；隔離測試刪除自己的 DB 前呼叫。"""
    resolved = resolve_database_dsn(dsn)
    with _database_engine_lock:
        engine = _database_engines.pop(resolved, None)
    if engine is not None:
        engine.dispose()


@contextmanager
def database_session(dsn: str | None = None) -> Generator[Session, None, None]:
    engine = _database_engine(resolve_database_dsn(dsn))
    with Session(engine, expire_on_commit=False) as session, session.begin():
        # SET LOCAL 隨交易結束恢復，不讓池中的下一筆交易承接逾時設定。
        session.execute(text("SET LOCAL lock_timeout = '5s'"))
        session.execute(text("SET LOCAL statement_timeout = '60s'"))
        yield session
