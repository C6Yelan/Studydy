from __future__ import annotations

import os
from pathlib import Path
import secrets
import subprocess
import time
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
import pytest

from runtime.storage.migrations import DEFAULT_MIGRATIONS_DIR

POSTGRES_IMAGE = (
    "postgres:18.4-bookworm@"
    "sha256:882236b897e39051d2368c5ccc6cda944904723506b2dfc97f2a8f5bc9afa382"
)


class DatabaseDsn(str):
    def __repr__(self) -> str:
        return "<redacted database DSN>"


def _run_docker(*arguments: str, environment: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        ["docker", *arguments],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    if completed.returncode != 0:
        pytest.fail("DISPOSABLE_POSTGRES_DOCKER_FAILED", pytrace=False)
    return completed.stdout.strip()


@pytest.fixture(scope="session")
def migrations_dir() -> Path:
    return DEFAULT_MIGRATIONS_DIR


@pytest.fixture(scope="session")
def postgres_dsn() -> DatabaseDsn:
    container_name = f"studydy-postgres-test-{uuid4().hex}"
    password = secrets.token_hex(32)
    docker_environment = os.environ.copy()
    docker_environment["POSTGRES_PASSWORD"] = password

    _run_docker(
        "run",
        "--detach",
        "--rm",
        "--name",
        container_name,
        "--publish",
        "127.0.0.1::5432",
        "--tmpfs",
        (
            "/var/lib/postgresql:rw,noexec,nosuid,nodev,size=1g,"
            "mode=0700,uid=999,gid=999"
        ),
        "--shm-size",
        "256m",
        "--env",
        "POSTGRES_PASSWORD",
        "--env",
        "POSTGRES_DB=studydy_test",
        "--env",
        "POSTGRES_USER=studydy_test_owner",
        "--env",
        "PGDATA=/var/lib/postgresql/18/docker",
        "--env",
        (
            "POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256 "
            "--data-checksums --encoding=UTF8 --locale=C"
        ),
        POSTGRES_IMAGE,
        environment=docker_environment,
    )
    try:
        port_output = _run_docker("port", container_name, "5432/tcp")
        port = port_output.rsplit(":", 1)[-1]
        dsn = DatabaseDsn(
            "host=127.0.0.1 "
            f"port={port} "
            "dbname=studydy_test user=studydy_test_owner "
            f"password={password} connect_timeout=2"
        )

        deadline = time.monotonic() + 60
        while True:
            try:
                with psycopg.connect(dsn):
                    break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    pytest.fail("DISPOSABLE_POSTGRES_NOT_READY", pytrace=False)
                time.sleep(0.2)
        yield dsn
    finally:
        _run_docker("rm", "--force", container_name)


@pytest.fixture
def clean_database_dsn(postgres_dsn: str):
    """每個 test 取得新建、空白且只屬於本次 session container 的 database。"""

    database_name = f"studydy_case_{uuid4().hex}"
    connection_fields = conninfo_to_dict(postgres_dsn)
    with psycopg.connect(postgres_dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    connection_fields["dbname"] = database_name
    case_dsn = DatabaseDsn(make_conninfo(**connection_fields))
    try:
        with psycopg.connect(case_dsn) as connection:
            assert connection.execute(
                "SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname='public'"
            ).fetchone() == (0,)
        yield case_dsn
    finally:
        with psycopg.connect(postgres_dsn, autocommit=True) as connection:
            connection.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname=%s AND pid<>pg_backend_pid()",
                (database_name,),
            )
            connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database_name)))
