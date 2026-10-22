from pathlib import Path
from importlib.metadata import distribution
import shutil
import subprocess
import tempfile

root = Path.cwd()
base = Path(tempfile.mkdtemp(prefix='studydy-office-'))
overlay = base / 'deps'
socket = base / 'socket'
overlay.mkdir()
socket.mkdir()
base.chmod(0o755)
socket.chmod(0o777)
pg = base.name + '-pg'
runner = base.name + '-runner'
image = 'sha256:40172a3c484fd67dd80bc0ac014320a8cb3c6e44d8f7d32f4cb385d212a9e398'
pg_image = 'postgres:18.4-bookworm@sha256:882236b897e39051d2368c5ccc6cda944904723506b2dfc97f2a8f5bc9afa382'
def run(args, check=True):
    return subprocess.run(args, check=check)
bootstrap = """
import os
import sys
from psycopg.conninfo import make_conninfo

for name in tuple(os.environ):
    if name.startswith("PG") or name in {
        "STUDYDY_DATABASE_DSN", "STUDYDY_TEST_POSTGRES_DSN"
    }:
        os.environ.pop(name, None)
os.environ["STUDYDY_TEST_POSTGRES_DSN"] = make_conninfo(
    host="/test-pg", dbname="studydy_test", user="studydy_test_owner", connect_timeout=2
)
import pytest
raise SystemExit(pytest.main(sys.argv[1:]))
"""
try:
    for name in ['pytest', 'pluggy', 'iniconfig', 'packaging', 'pygments']:
        dist = distribution(name)
        for entry in dist.files or []:
            if '..' in entry.parts or '__pycache__' in entry.parts:
                continue
            source = Path(dist.locate_file(entry))
            if source.is_file():
                target = overlay / entry
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        print(f'overlay {name}=={dist.version}', flush=True)
    run(['docker','run','--detach','--pull','never','--name',pg,'--network','none',
         '--tmpfs','/var/lib/postgresql:rw,noexec,nosuid,nodev,size=1g,mode=0700,uid=999,gid=999',
         '--tmpfs','/var/run/postgresql:rw,nosuid,nodev,mode=3777',
         '--mount',f'type=bind,src={socket},dst=/test-pg',
         '--env','POSTGRES_HOST_AUTH_METHOD=trust','--env','POSTGRES_DB=studydy_test',
         '--env','POSTGRES_USER=studydy_test_owner','--env','PGDATA=/var/lib/postgresql/18/docker',
         '--env','POSTGRES_INITDB_ARGS=--auth-local=trust --auth-host=trust --data-checksums --encoding=UTF8 --locale=C',
         pg_image,'postgres','-c','unix_socket_directories=/var/run/postgresql,/test-pg'])
    import time
    time.sleep(3)
    run(['docker','logs',pg],check=False)
    # The existing fixture waits for PostgreSQL readiness and checks version/role.
    args=['docker','run','--rm','--pull','never','--name',runner,'--network','none','--read-only',
          '--cap-drop','ALL','--security-opt','no-new-privileges:true',
          '--security-opt',f'seccomp={root}/ops/docker/bubblewrap-seccomp.json',
          '--tmpfs','/tmp:rw,exec,nosuid,nodev,size=3g,mode=1777',
          '--mount',f'type=bind,src={socket},dst=/test-pg,readonly',
          '--mount',f'type=bind,src={overlay},dst=/testdeps,readonly']
    for path in ['backend/src','backend/tests','backend/migrations','local_ai']:
        args += ['--mount',f'type=bind,src={root / path},dst=/app/{path},readonly']
    args += ['--env','PYTHONPATH=/app/backend/src:/app/backend/tests:/app/local_ai/src:/testdeps',
             '--env','PYTHONDONTWRITEBYTECODE=1','--env','STUDYDY_DATABASE_DSN=',
             '--entrypoint','/app/backend/.venv/bin/python',image,'-B','-c',bootstrap,'-q','-o','cache_dir=/tmp/pytest-cache',
             'backend/tests/runtime/sources/test_source_normalization.py::test_real_converter_inside_sandbox[.docx]',
             'backend/tests/runtime/sources/test_source_normalization.py::test_real_converter_inside_sandbox[.pptx]',
             'backend/tests/runtime/sources/test_source_normalization.py::test_legacy_office_source_is_saved_and_converted',
             'backend/tests/test_office_external_resources.py::test_real_conversion_skips_private_image_and_keeps_text',
             'backend/tests/test_office_external_resources.py::test_downloaded_public_image_survives_offline_conversion']
    result = run(args, check=False)
    print(f'pytest_exit_code={result.returncode}', flush=True)
finally:
    run(['docker','logs',pg],check=False)
    run(['docker','rm','--force',runner,pg],check=False)
    shutil.rmtree(base)
    run(['docker','ps','-a','--filter',f'name={base.name}','--format','{{.Names}} {{.Status}}'])
    print(f'cleanup temporary_directory_exists={base.exists()}',flush=True)

raise SystemExit(result.returncode)
