"""已知 0032 終態的一次性接軌；不重播歷史 DML，也不改業務資料。"""

from datetime import datetime, UTC
import hashlib
import json
from pathlib import Path

from psycopg import sql
from psycopg.types.json import Jsonb

from content_identity import canonical_bytes, canonical_sha256
from .database import connect_database

BASELINE_ID = "studydy-schema-20261019"
BASELINE_VERSIONS = 7
CONTROL_TABLE = "schema_baseline"
MANIFEST_PATH = Path(__file__).with_name("legacy_0032.json")
CONTROL_SQL = """CREATE TABLE public.schema_baseline (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    baseline_id text NOT NULL,
    prefix_sha256 character(64) NOT NULL CHECK (prefix_sha256 ~ '^[0-9a-f]{64}$'),
    method text NOT NULL CHECK (method IN ('fresh', 'adopted')),
    recorded_at timestamptz NOT NULL,
    legacy_ledger jsonb,
    verification jsonb NOT NULL
)"""


class BaselineError(RuntimeError):
    """只回傳固定原因碼；不回顯 SQL、教材或連線設定。"""


def legacy_manifest():
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def prefix_digest(migrations):
    if len(migrations) < BASELINE_VERSIONS:
        raise BaselineError("BASELINE_FILES_INCOMPLETE")
    return canonical_sha256(
        {str(m.version): m.sql_sha256 for m in migrations[:BASELINE_VERSIONS]}
    )


def read_marker(connection):
    if (
        connection.execute("SELECT to_regclass('public.schema_baseline')").fetchone()[0]
        is None
    ):
        return None
    rows = connection.execute(
        "SELECT baseline_id,prefix_sha256,method,legacy_ledger,verification FROM schema_baseline"
    ).fetchall()
    if len(rows) != 1:
        raise BaselineError("BASELINE_MARKER_INVALID")
    return rows[0]


def verify_marker(marker, migrations):
    if marker[0] != BASELINE_ID or marker[1] != prefix_digest(migrations):
        raise BaselineError("BASELINE_IDENTITY_MISMATCH")


def record_fresh(connection, migrations):
    connection.execute(
        "INSERT INTO schema_baseline VALUES (true,%s,%s,%s,%s,NULL,%s)",
        (BASELINE_ID, prefix_digest(migrations), "fresh", datetime.now(UTC), Jsonb({})),
    )


def business_tables(connection):
    return [
        name
        for (name,) in connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' "
            "AND tablename NOT IN ('schema_migrations','schema_baseline') ORDER BY tablename"
        )
    ]


def schema_snapshot(connection):
    """核對 PostgreSQL 實體定義；舊帳本結構也要驗，僅排除新增的接軌收據。"""
    connection.execute("SET LOCAL search_path TO public, pg_catalog")
    scope = "n.nspname='public' AND c.relname <> 'schema_baseline'"
    statements = {
        "relations": f"""SELECT c.relname,c.relkind,c.relpersistence,c.relrowsecurity,
            c.relforcerowsecurity,c.relreplident,c.reloptions,c.relacl::text,
            pg_get_userbyid(c.relowner)=current_user,obj_description(c.oid,'pg_class'),
            CASE WHEN c.relkind IN ('v','m') THEN pg_get_viewdef(c.oid,false) END
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE {scope} AND c.relkind IN ('r','p','v','m','S','f') ORDER BY c.relname""",
        "columns": f"""SELECT c.relname,a.attnum,a.attname,format_type(a.atttypid,a.atttypmod),
            a.attnotnull,a.attidentity,a.attgenerated,a.attstorage,a.attcompression,
            a.attcollation::regcollation::text,pg_get_expr(d.adbin,d.adrelid,false),
            col_description(c.oid,a.attnum)
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_attribute a ON a.attrelid=c.oid
            LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
            WHERE {scope} AND c.relkind IN ('r','p','v','m','S','f')
            AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum""",
        "constraints": f"""SELECT c.relname,k.conname,k.contype,k.condeferrable,
            k.condeferred,k.convalidated,k.conenforced,pg_get_constraintdef(k.oid,false)
            FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE {scope} ORDER BY c.relname,k.conname""",
        "indexes": f"""SELECT c.relname,i.relname,pg_get_indexdef(x.indexrelid),
            x.indisvalid,x.indisready,x.indislive,x.indisreplident,x.indnullsnotdistinct,
            i.reloptions FROM pg_index x JOIN pg_class c ON c.oid=x.indrelid
            JOIN pg_class i ON i.oid=x.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE {scope} ORDER BY c.relname,i.relname""",
        "triggers": f"""SELECT c.relname,t.tgname,t.tgenabled,pg_get_triggerdef(t.oid,false)
            FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE {scope} AND NOT t.tgisinternal ORDER BY c.relname,t.tgname""",
        "functions": """SELECT p.oid::regprocedure::text,pg_get_functiondef(p.oid),
            p.proacl::text,pg_get_userbyid(p.proowner)=current_user
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' AND p.prokind IN ('f','p') ORDER BY 1""",
        "policies": """SELECT tablename,policyname,permissive,roles,cmd,qual,with_check
            FROM pg_policies WHERE schemaname='public' ORDER BY tablename,policyname""",
        "extensions": """SELECT e.extname,e.extversion,n.nspname FROM pg_extension e
            JOIN pg_namespace n ON n.oid=e.extnamespace ORDER BY e.extname""",
        "schema_permissions": """SELECT nspname,nspacl::text,pg_get_userbyid(nspowner)
            FROM pg_namespace WHERE nspname='public' """,
        "default_permissions": """SELECT defaclobjtype,defaclacl::text
            FROM pg_default_acl WHERE defaclnamespace IN (0,'public'::regnamespace)
            ORDER BY defaclobjtype,defaclacl::text""",
    }
    return {
        name: connection.execute(statement).fetchall()
        for name, statement in statements.items()
    }


def data_snapshot(connection):
    """逐表、逐列比較全部欄位；JSON 陣列順序、NULL 與 bytes 不被省略。"""
    connection.execute("SET LOCAL TIME ZONE 'UTC'")
    result = {}
    for table in business_tables(connection):
        digest = hashlib.sha256()
        count = 0
        with connection.cursor(name="baseline_rows") as rows:
            rows.execute(
                sql.SQL(
                    'SELECT to_jsonb(t)::text FROM {} t ORDER BY to_jsonb(t)::text COLLATE "C"'
                ).format(sql.Identifier(table))
            )
            for (value,) in rows:
                data = value.encode("utf-8")
                digest.update(len(data).to_bytes(8, "big"))
                digest.update(data)
                count += 1
        result[table] = {"rows": count, "sha256": digest.hexdigest()}
    return result


def verify_content(connection, artifact_root):
    """讀取既有 codec 後驗身分；不重算保存值或重新生成任何教材。"""
    from knowledge_map.structure import validate_knowledge_structure
    from .evidence_json import EvidenceJSONB
    from .artifacts import _verify_file

    root = Path(artifact_root)
    if (
        not root.is_absolute()
        or root.resolve() != root
        or not (root / "objects").is_dir()
        or (root / "objects").is_symlink()
    ):
        raise BaselineError("BASELINE_ARTIFACT_ROOT_INVALID")
    decode = EvidenceJSONB().process_result_value
    counts = {"knowledge_structures": 0, "source_sets": 0, "artifacts": 0}
    for revision, stored in connection.execute(
        "SELECT structure_revision,document FROM knowledge_structures"
    ):
        document = decode(stored, None)
        if document.get("revision") != revision or not validate_knowledge_structure(
            document
        ):
            raise BaselineError("BASELINE_KNOWLEDGE_IDENTITY_INVALID")
        counts["knowledge_structures"] += 1
    for stored, digest in connection.execute(
        "SELECT manifest,digest FROM material_source_sets"
    ):
        manifest = decode(stored, None)
        if (
            not isinstance(manifest, dict)
            or not isinstance(manifest.get("items"), list)
            or not manifest["items"]
            or canonical_sha256(manifest) != digest
        ):
            raise BaselineError("BASELINE_SOURCE_IDENTITY_INVALID")
        counts["source_sets"] += 1
    from ..topics import proposal

    for status, stored, approved in connection.execute(
        "SELECT status,proposal,approved_sha256 FROM topic_scopes WHERE status IN ('ready','approved')"
    ):
        value = decode(stored, None)
        try:
            proposal(value)
        except Exception:
            raise BaselineError("BASELINE_PROPOSAL_INVALID") from None
        if status == "approved" and canonical_sha256(value) != approved:
            raise BaselineError("BASELINE_PROPOSAL_INVALID")
    for identity, size, digest in connection.execute(
        "SELECT artifact_id,size_bytes,sha256 FROM artifacts"
    ):
        try:
            with _verify_file(root / "objects" / identity.hex, bytes(digest), size):
                pass
        except Exception:
            raise BaselineError("BASELINE_ARTIFACT_INVALID") from None
        counts["artifacts"] += 1
    # 所有封存／checkpoint 檔案也納入前後比對，非 DB Artifact 的檔案不會漏掉。
    digest = hashlib.sha256()
    files = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BaselineError("BASELINE_ARTIFACT_INVALID")
        if not path.is_file():
            continue
        with path.open("rb") as stream:
            file_digest = hashlib.file_digest(stream, "sha256").hexdigest()
        digest.update(
            canonical_bytes(
                [str(path.relative_to(root)), path.stat().st_size, file_digest]
            )
        )
        files += 1
    return {**counts, "files": files, "files_sha256": digest.hexdigest()}


def _legacy_ledger(connection):
    if (
        connection.execute("SELECT to_regclass('public.schema_migrations')").fetchone()[
            0
        ]
        is None
    ):
        raise BaselineError("BASELINE_LEGACY_LEDGER_MISSING")
    rows = connection.execute(
        "SELECT version,sql_sha256,applied_at FROM schema_migrations ORDER BY version"
    ).fetchall()
    expected = legacy_manifest()["migrations"]
    if (
        len(rows) != len(expected)
        or {str(version): digest for version, digest, _ in rows} != expected
    ):
        raise BaselineError("BASELINE_LEGACY_LEDGER_MISMATCH")
    return [
        [version, digest, timestamp.isoformat()] for version, digest, timestamp in rows
    ]


def check_or_adopt(dsn, migrations, artifact_root, *, apply=False):
    """明確停寫後才能 Apply；同交易鎖住業務表並重驗，不信任先前 Check。"""
    from .migrations import (
        MIGRATION_LOCK_KEY,
        _read_applied_migrations,
        _verify_applied_migrations,
    )

    with connect_database(dsn, autocommit=True) as connection:
        connection.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
        try:
            with connection.transaction():
                connection.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"
                    + ("" if apply else " READ ONLY")
                )
                marker = read_marker(connection)
                if marker is not None:
                    verify_marker(marker, migrations)
                    applied = _read_applied_migrations(connection)
                    _verify_applied_migrations(migrations, applied)
                    if len(applied) == BASELINE_VERSIONS:
                        if (
                            canonical_sha256(schema_snapshot(connection))
                            != legacy_manifest()["schema_sha256"]
                        ):
                            raise BaselineError("BASELINE_SCHEMA_MISMATCH")
                    content = verify_content(connection, artifact_root)
                    return {
                        "baseline_id": BASELINE_ID,
                        "already_adopted": True,
                        **content,
                    }
                if apply:
                    tables = ["schema_migrations", *business_tables(connection)]
                    connection.execute(
                        sql.SQL("LOCK TABLE {} IN SHARE MODE").format(
                            sql.SQL(",").join(sql.Identifier(table) for table in tables)
                        )
                    )
                ledger = _legacy_ledger(connection)
                schema = canonical_sha256(schema_snapshot(connection))
                manifest = legacy_manifest()
                alignment = schema == manifest["known_schema_repair_sha256"]
                if schema != manifest["schema_sha256"] and not alignment:
                    raise BaselineError("BASELINE_SCHEMA_MISMATCH")
                before = data_snapshot(connection)
                content = verify_content(connection, artifact_root)
                proof = {
                    "source_schema_sha256": schema,
                    "schema_sha256": manifest["schema_sha256"],
                    "aligned_known_schema": alignment,
                    "data": before,
                    "content": content,
                }
                if apply:
                    if alignment:
                        # 此處只接受完整 fingerprint 相符的已知差異；任意損壞不走修補。
                        connection.execute(
                            Path(__file__)
                            .with_name("legacy_0032_alignment.sql")
                            .read_text()
                        )
                    connection.execute(CONTROL_SQL)
                    connection.execute(
                        "INSERT INTO schema_baseline VALUES (true,%s,%s,%s,%s,%s,%s)",
                        (
                            BASELINE_ID,
                            prefix_digest(migrations),
                            "adopted",
                            datetime.now(UTC),
                            Jsonb(ledger),
                            Jsonb(proof),
                        ),
                    )
                    connection.execute("DELETE FROM schema_migrations")
                    with connection.cursor() as cursor:
                        cursor.executemany(
                            "INSERT INTO schema_migrations VALUES (%s,%s,%s)",
                            [
                                (m.version, m.sql_sha256, datetime.now(UTC))
                                for m in migrations[:BASELINE_VERSIONS]
                            ],
                        )
                    if (
                        canonical_sha256(schema_snapshot(connection))
                        != manifest["schema_sha256"]
                        or data_snapshot(connection) != before
                    ):
                        raise BaselineError("BASELINE_DATA_CHANGED")
                    if verify_content(connection, artifact_root) != content:
                        raise BaselineError("BASELINE_ARTIFACT_CHANGED")
                return {
                    "baseline_id": BASELINE_ID,
                    "applied": apply,
                    "schema_alignment": alignment,
                    "tables": len(before),
                    **content,
                }
        finally:
            connection.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))


def restore_legacy_ledger(dsn, migrations):
    """只撤回控制帳本；有後續 DDL 時拒絕，不以 Git 回退代替資料核對。"""
    from .migrations import (
        MIGRATION_LOCK_KEY,
        _read_applied_migrations,
        _verify_applied_migrations,
    )

    with connect_database(dsn, autocommit=True) as connection:
        connection.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
        try:
            with connection.transaction():
                marker = read_marker(connection)
                if marker is None:
                    raise BaselineError("BASELINE_MARKER_INVALID")
                verify_marker(marker, migrations)
                applied = _read_applied_migrations(connection)
                _verify_applied_migrations(migrations, applied)
                if (
                    marker[2] != "adopted"
                    or not marker[3]
                    or len(applied) != BASELINE_VERSIONS
                ):
                    raise BaselineError("BASELINE_ROLLBACK_UNAVAILABLE")
                if (
                    len(marker[3]) != len(legacy_manifest()["migrations"])
                    or {str(row[0]): row[1] for row in marker[3]}
                    != legacy_manifest()["migrations"]
                ):
                    raise BaselineError("BASELINE_LEGACY_LEDGER_MISMATCH")
                connection.execute(
                    sql.SQL("LOCK TABLE {} IN SHARE MODE").format(
                        sql.SQL(",").join(
                            sql.Identifier(table)
                            for table in [
                                "schema_migrations",
                                CONTROL_TABLE,
                                *business_tables(connection),
                            ]
                        )
                    )
                )
                if (
                    canonical_sha256(schema_snapshot(connection))
                    != legacy_manifest()["schema_sha256"]
                ):
                    raise BaselineError("BASELINE_SCHEMA_MISMATCH")
                before = data_snapshot(connection)
                connection.execute("DELETE FROM schema_migrations")
                with connection.cursor() as cursor:
                    cursor.executemany(
                        "INSERT INTO schema_migrations VALUES (%s,%s,%s)", marker[3]
                    )
                connection.execute("DROP TABLE schema_baseline")
                if data_snapshot(connection) != before:
                    raise BaselineError("BASELINE_DATA_CHANGED")
                return {"baseline_id": BASELINE_ID, "restored_legacy_ledger": True}
        finally:
            connection.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))


def main():
    """維護入口不載入 HTTP API；應用啟動失敗時仍可核對或回退控制帳本。"""
    import argparse
    import os
    from .database import configure_database
    from .migrations import load_migrations

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "apply", "restore-ledger"))
    action = parser.parse_args().action
    try:
        if not os.environ.get("STUDYDY_DATABASE_DSN"):
            configure_database(os.environ)
        migrations = load_migrations()
        if action == "restore-ledger":
            result = restore_legacy_ledger(None, migrations)
        else:
            result = check_or_adopt(
                None,
                migrations,
                Path(os.environ["STUDYDY_ARTIFACT_ROOT"]),
                apply=action == "apply",
            )
        print(json.dumps(result), flush=True)
    except BaselineError as error:
        print(json.dumps({"error": str(error)}), flush=True)
        raise SystemExit(1) from None
    except Exception:
        print(json.dumps({"error": "BASELINE_OPERATION_FAILED"}), flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
