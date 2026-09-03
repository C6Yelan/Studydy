from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
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
TEST_POSTGRES_DSN_ENVIRONMENT = "STUDYDY_TEST_POSTGRES_DSN"


class DatabaseDsn(str):
    def __repr__(self) -> str:
        return "<redacted database DSN>"


def _run_docker(*arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["docker", *arguments],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        pytest.fail("DISPOSABLE_POSTGRES_DOCKER_UNAVAILABLE", pytrace=False)
    if completed.returncode != 0:
        pytest.fail("DISPOSABLE_POSTGRES_DOCKER_FAILED", pytrace=False)
    return completed.stdout.strip()


def _test_postgres_dsn_from_environment(
    environment: Mapping[str, str],
) -> DatabaseDsn | None:
    """只接受明確 test control database；錯誤不回顯 DSN。"""

    raw_dsn = environment.get(TEST_POSTGRES_DSN_ENVIRONMENT)
    if raw_dsn is None:
        return None
    if (
        not isinstance(raw_dsn, str)
        or not raw_dsn
        or "\x00" in raw_dsn
    ):
        raise ValueError("TEST_POSTGRES_DSN_INVALID")
    try:
        fields = conninfo_to_dict(raw_dsn)
    except Exception:
        raise ValueError("TEST_POSTGRES_DSN_INVALID") from None
    database_name = fields.get("dbname")
    host = fields.get("host", "")
    if (
        not isinstance(database_name, str)
        or not database_name.startswith("studydy_test")
        or not isinstance(host, str)
        or (
            host not in {"", "127.0.0.1", "localhost"}
            and not host.startswith("/")
        )
    ):
        raise ValueError("TEST_POSTGRES_DSN_INVALID")
    production_dsn = environment.get("STUDYDY_DATABASE_DSN")
    if production_dsn:
        try:
            production_fields = conninfo_to_dict(production_dsn)
        except Exception:
            raise ValueError("TEST_POSTGRES_DSN_INVALID") from None
        target_fields = ("host", "hostaddr", "port", "dbname", "user", "service")
        if all(
            fields.get(name) == production_fields.get(name)
            for name in target_fields
        ):
            raise ValueError("TEST_POSTGRES_DSN_INVALID")
    fields.setdefault("connect_timeout", "2")
    return DatabaseDsn(make_conninfo(**fields))


def _wait_for_postgres(dsn: DatabaseDsn) -> None:
    deadline = time.monotonic() + 60
    while True:
        try:
            with psycopg.connect(dsn) as connection:
                version = connection.execute(
                    "SHOW server_version_num"
                ).fetchone()
                if version is None or not 180_000 <= int(version[0]) < 190_000:
                    pytest.fail("DISPOSABLE_POSTGRES_VERSION_MISMATCH", pytrace=False)
                can_create = connection.execute(
                    "SELECT rolcreatedb OR rolsuper FROM pg_roles "
                    "WHERE rolname=current_user"
                ).fetchone()
                if can_create != (True,):
                    pytest.fail(
                        "DISPOSABLE_POSTGRES_CREATE_DATABASE_REQUIRED",
                        pytrace=False,
                    )
                break
        except psycopg.OperationalError:
            if time.monotonic() >= deadline:
                pytest.fail("DISPOSABLE_POSTGRES_NOT_READY", pytrace=False)
            time.sleep(0.2)
        except psycopg.Error:
            pytest.fail("DISPOSABLE_POSTGRES_CREATE_DATABASE_REQUIRED", pytrace=False)


@pytest.fixture(scope="session")
def migrations_dir() -> Path:
    return DEFAULT_MIGRATIONS_DIR


@pytest.fixture(scope="session")
def postgres_dsn() -> DatabaseDsn:
    try:
        external_dsn = _test_postgres_dsn_from_environment(os.environ)
    except ValueError:
        pytest.fail("TEST_POSTGRES_DSN_INVALID", pytrace=False)
    if external_dsn is not None:
        _wait_for_postgres(external_dsn)
        yield external_dsn
        return

    container_name = f"studydy-postgres-test-{uuid4().hex}"

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
        "POSTGRES_HOST_AUTH_METHOD=trust",
        "--env",
        "POSTGRES_DB=studydy_test",
        "--env",
        "POSTGRES_USER=studydy_test_owner",
        "--env",
        "PGDATA=/var/lib/postgresql/18/docker",
        "--env",
        (
            "POSTGRES_INITDB_ARGS=--auth-host=trust "
            "--data-checksums --encoding=UTF8 --locale=C"
        ),
        POSTGRES_IMAGE,
    )
    try:
        port_output = _run_docker("port", container_name, "5432/tcp")
        port = port_output.rsplit(":", 1)[-1]
        dsn = DatabaseDsn(
            "host=127.0.0.1 "
            f"port={port} "
            "dbname=studydy_test user=studydy_test_owner "
            "connect_timeout=2"
        )

        _wait_for_postgres(dsn)
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
