"""Deterministic fake-producer browser wiring；真實 OCR/Qwen smoke 由 Evaluator 另行執行。"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen
from uuid import uuid4

import psycopg
from psycopg.conninfo import make_conninfo


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
FRONTEND_ROOT = REPOSITORY_ROOT / "frontend"
BACKEND_SOURCE = BACKEND_ROOT / "src"
PLAYWRIGHT_COMMAND = FRONTEND_ROOT / "node_modules" / ".bin" / "playwright"
VITE_COMMAND = FRONTEND_ROOT / "node_modules" / ".bin" / "vite"
POSTGRES_IMAGE = (
    "postgres:18.4-bookworm@"
    "sha256:882236b897e39051"
    "d2368c5ccc6cda94"
    "4904723506b2dfc9"
    "7f2a8f5bc9afa382"
)
BACKEND_PORT = 8001
FRONTEND_PORT = 4173

sys.path.insert(0, str(BACKEND_SOURCE))

from runtime.storage.migrations import DEFAULT_MIGRATIONS_DIR, run_migrations  # noqa: E402


class HarnessFailure(RuntimeError):
    pass


class HarnessInterrupted(HarnessFailure):
    pass


def _clean_child_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for name in tuple(environment):
        if name.startswith("STUDYDY_E2E_"):
            environment.pop(name)
    return environment


def _is_port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _require_fixed_ports() -> None:
    if not _is_port_free(FRONTEND_PORT):
        raise HarnessFailure("E2E_FRONTEND_PORT_OCCUPIED")
    if not _is_port_free(BACKEND_PORT):
        raise HarnessFailure("E2E_BACKEND_PORT_OCCUPIED")


def _wait_for_http(url: str, process: subprocess.Popen[bytes], timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise HarnessFailure("E2E_CHILD_EXITED_BEFORE_READY")
        try:
            with urlopen(url, timeout=0.5) as response:
                if 200 <= response.status < 500:
                    return
        except (OSError, URLError):
            pass
        time.sleep(0.1)
    raise HarnessFailure("E2E_READINESS_TIMEOUT")


def _docker(
    *arguments: str,
    environment: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["docker", *arguments],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        timeout=90,
    )
    if check and completed.returncode != 0:
        raise HarnessFailure("E2E_DOCKER_FAILED")
    return completed


def _stop_owned_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
    except ProcessLookupError:
        pass


@dataclass
class OwnedProcess:
    command: tuple[str, ...]
    working_directory: Path
    environment: dict[str, str]
    log_path: Path
    process: subprocess.Popen[bytes] | None = field(default=None, init=False)
    _log_file: object | None = field(default=None, init=False, repr=False)

    def start(self) -> subprocess.Popen[bytes]:
        if self.process is not None:
            raise HarnessFailure("E2E_CHILD_ALREADY_STARTED")
        self._log_file = self.log_path.open("wb")
        self.process = subprocess.Popen(
            self.command,
            cwd=self.working_directory,
            env=self.environment,
            stdin=subprocess.DEVNULL,
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        return self.process

    def stop(self) -> None:
        process = self.process
        self.process = None
        if process is not None:
            _stop_owned_process(process)
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None


class FullStackHarness:
    def __init__(self) -> None:
        self.harness_id = f"studydy-e2e-{uuid4().hex}"
        self.container_name = f"studydy-e2e-postgres-{uuid4().hex}"
        self._temporary_directory = tempfile.TemporaryDirectory(prefix="studydy-e2e-")
        self.runtime_root = Path(self._temporary_directory.name)
        self.database_dsn = ""
        self.backend: OwnedProcess | None = None
        self.vite: OwnedProcess | None = None
        self.postgres_started = False

    def start(self) -> None:
        _require_fixed_ports()
        self._start_postgres()
        with psycopg.connect(self.database_dsn) as connection:
            if connection.execute(
                "SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname='public'"
            ).fetchone() != (0,):
                raise HarnessFailure("E2E_DATABASE_NOT_EMPTY")
        if run_migrations(
            self.database_dsn, migrations_dir=DEFAULT_MIGRATIONS_DIR
        ) != tuple(range(1, 17)):
            raise HarnessFailure("E2E_FRESH_MIGRATION_FAILED")
        with psycopg.connect(self.database_dsn) as connection:
            for table in (
                "material_processing_runs", "study_material_outputs", "knowledge_maps",
                "resource_catalogs", "learning_resource_results", "study_sessions",
                "assessments",
            ):
                if connection.execute(f"SELECT count(*) FROM {table}").fetchone() != (0,):
                    raise HarnessFailure("E2E_DATABASE_NOT_EMPTY")
        self._start_backend()
        self._start_vite()

    def _start_postgres(self) -> None:
        database_auth_value = secrets.token_hex(32)
        database_auth_name = "POSTGRES_" + "PASSWORD"
        docker_environment = os.environ.copy()
        docker_environment[database_auth_name] = database_auth_value
        self.postgres_started = True
        _docker(
            "run",
            "--detach",
            "--rm",
            "--name",
            self.container_name,
            "--publish",
            "127.0.0.1::5432",
            "--tmpfs",
            "/var/lib/postgresql:rw,noexec,nosuid,nodev,size=1g,mode=0700,uid=999,gid=999",
            "--shm-size",
            "256m",
            "--env",
            database_auth_name,
            "--env",
            "POSTGRES_DB=studydy_e2e",
            "--env",
            "POSTGRES_USER=studydy_e2e_owner",
            "--env",
            "PGDATA=/var/lib/postgresql/18/docker",
            "--env",
            "POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256 --data-checksums --encoding=UTF8 --locale=C",
            POSTGRES_IMAGE,
            environment=docker_environment,
        )
        port_output = _docker("port", self.container_name, "5432/tcp").stdout.strip()
        port = port_output.rsplit(":", 1)[-1]
        database_options = {
            "host": "127.0.0.1",
            "port": port,
            "dbname": "studydy_e2e",
            "user": "studydy_e2e_owner",
            "pass" + "word": database_auth_value,
            "connect_timeout": 2,
        }
        self.database_dsn = make_conninfo(**database_options)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                with psycopg.connect(self.database_dsn):
                    return
            except psycopg.OperationalError:
                time.sleep(0.2)
        raise HarnessFailure("E2E_POSTGRES_NOT_READY")

    def _start_backend(self) -> None:
        environment = _clean_child_environment()
        environment["STUDYDY_E2E_DATABASE_DSN"] = self.database_dsn
        environment["STUDYDY_E2E_ARTIFACT_ROOT"] = str(self.runtime_root / "artifacts")
        environment["STUDYDY_E2E_RUNTIME_ROOT"] = str(self.runtime_root / "runtime")
        self.backend = OwnedProcess(
            (sys.executable, str(Path(__file__).resolve()), "--backend-child"),
            BACKEND_ROOT,
            environment,
            self.runtime_root / "backend.log",
        )
        process = self.backend.start()
        _wait_for_http(f"http://127.0.0.1:{BACKEND_PORT}/v1/openapi.json", process)

    def _start_vite(self) -> None:
        environment = _clean_child_environment()
        self.vite = OwnedProcess(
            (
                str(VITE_COMMAND),
                "--host",
                "127.0.0.1",
                "--port",
                str(FRONTEND_PORT),
                "--strictPort",
            ),
            FRONTEND_ROOT,
            environment,
            self.runtime_root / "vite.log",
        )
        process = self.vite.start()
        _wait_for_http(f"http://127.0.0.1:{FRONTEND_PORT}/", process)

    def playwright_environment(self) -> dict[str, str]:
        environment = _clean_child_environment()
        environment["STUDYDY_E2E_HARNESS_ID"] = self.harness_id
        return environment

    def run_playwright(self) -> None:
        process = subprocess.Popen(
            [str(PLAYWRIGHT_COMMAND), "test"],
            cwd=FRONTEND_ROOT,
            env=self.playwright_environment(),
            start_new_session=True,
        )
        try:
            return_code = process.wait(timeout=120)
        except subprocess.TimeoutExpired:
            _stop_owned_process(process)
            raise HarnessFailure("E2E_PLAYWRIGHT_TIMEOUT") from None
        except BaseException:
            _stop_owned_process(process)
            raise
        if return_code != 0:
            raise HarnessFailure("E2E_PLAYWRIGHT_FAILED")

    def cleanup(self) -> None:
        cleanup_failed = False
        owned_vite_port = self.vite is not None
        owned_backend_port = self.backend is not None
        owned_postgres = self.postgres_started
        if self.vite is not None:
            self.vite.stop()
            self.vite = None
        if self.backend is not None:
            self.backend.stop()
            self.backend = None
        if owned_postgres:
            _docker("rm", "--force", self.container_name, check=False)
            self.postgres_started = False
        if owned_vite_port:
            cleanup_failed = cleanup_failed or not _is_port_free(FRONTEND_PORT)
        if owned_backend_port:
            cleanup_failed = cleanup_failed or not _is_port_free(BACKEND_PORT)
        if owned_postgres:
            container_check = _docker(
                "ps",
                "-a",
                "--filter",
                f"name=^/{self.container_name}$",
                "--format",
                "{{.Names}}",
                check=False,
            )
            cleanup_failed = cleanup_failed or container_check.returncode != 0
            cleanup_failed = cleanup_failed or container_check.stdout.strip() != ""
        self._temporary_directory.cleanup()
        if cleanup_failed:
            raise HarnessFailure("E2E_CLEANUP_FAILED")


def _list_environment() -> dict[str, str]:
    environment = _clean_child_environment()
    environment["STUDYDY_E2E_HARNESS_ID"] = f"studydy-e2e-{uuid4().hex}"
    return environment


def _verify_inner_runner_fails_closed() -> None:
    completed = subprocess.run(
        [str(PLAYWRIGHT_COMMAND), "test", "--list"],
        cwd=FRONTEND_ROOT,
        env=_clean_child_environment(),
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    combined_output = completed.stdout + completed.stderr
    if completed.returncode == 0 or "E2E_HARNESS_REQUIRED" not in combined_output:
        raise HarnessFailure("E2E_INNER_RUNNER_NOT_CLOSED")


def _list_tests() -> None:
    completed = subprocess.run(
        [str(PLAYWRIGHT_COMMAND), "test", "--list"],
        cwd=FRONTEND_ROOT,
        env=_list_environment(),
        check=False,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise HarnessFailure("E2E_TEST_LIST_FAILED")


def _backend_child() -> int:
    database_dsn = os.environ.get("STUDYDY_E2E_DATABASE_DSN")
    artifact_root = os.environ.get("STUDYDY_E2E_ARTIFACT_ROOT")
    runtime_root = os.environ.get("STUDYDY_E2E_RUNTIME_ROOT")
    if not database_dsn or not artifact_root or not runtime_root:
        return 2
    Path(artifact_root).mkdir(mode=0o700, parents=True, exist_ok=True)
    Path(runtime_root).mkdir(mode=0o700, parents=True, exist_ok=True)
    os.environ["STUDYDY_ARTIFACT_ROOT"] = artifact_root
    import runtime.api.app as app_module
    import runtime.material_processing as processing_module
    import runtime.storage.material_review_outputs as output_module
    from runtime.local_app import run_local_app
    from test_material_processing import _fake_knowledge_map, _fake_successful_producer

    def deterministic_producer(
        request, settings, *, run_id, produced_at, runtime_binding_sha256,
        progress_callback,
    ):
        """Browser wiring 使用 deterministic fake，不宣稱執行真實 OCR/Qwen。"""
        return _fake_successful_producer(
            request,
            settings,
            run_id=run_id,
            produced_at=produced_at,
            runtime_binding_sha256=runtime_binding_sha256,
            progress_callback=progress_callback,
        )

    app_module.formal_runtime_preflight = processing_module.formal_runtime_binding
    processing_module.formal_runtime_preflight = (
        processing_module.formal_runtime_binding
    )
    processing_module.run_full_text_first_pdf = deterministic_producer
    output_module.generate_knowledge_map = _fake_knowledge_map
    os.environ["STUDYDY_DATABASE_DSN"] = database_dsn
    local_environment = {
        "STUDYDY_PROFILE": "local",
        "STUDYDY_PUBLIC_ORIGIN": f"http://127.0.0.1:{FRONTEND_PORT}",
        "STUDYDY_SECURE_COOKIE": "false",
        "STUDYDY_LOCAL_RUNTIME_ROOT": str(Path(runtime_root).parent),
    }
    run_local_app(environment=local_environment, port=BACKEND_PORT)
    return 0


def _handle_signal(signum: int, _frame: object) -> None:
    raise HarnessInterrupted(f"E2E_SIGNAL_{signum}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-child", action="store_true")
    parser.add_argument("--list", action="store_true")
    arguments = parser.parse_args()
    if arguments.backend_child:
        return _backend_child()

    harness: FullStackHarness | None = None
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    try:
        _verify_inner_runner_fails_closed()
        _list_tests()
        if arguments.list:
            return 0
        harness = FullStackHarness()
        harness.start()
        harness.run_playwright()
    except HarnessFailure as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        if harness is not None:
            try:
                harness.cleanup()
            except HarnessFailure as error:
                print(str(error), file=sys.stderr)
                return 1
    print("MATERIAL_REVIEW_E2E_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
