"""既有 Podcast WAV 音訊逐檔轉成無損 FLAC 的維護腳本；只由管理者手動執行，服務不會自動呼叫。

在後端容器內執行（需要與服務相同的身分、artifact root 與資料庫設定）：

    python -m runtime.podcast_audio_flac --dry-run
    python -m runtime.podcast_audio_flac --backup-dir /data/artifacts/.podcast-wav-backup [--limit N]

每一檔：
(a) 整檔核對 DB 雜湊後，把原 WAV 複製到備份目錄並讀回核對；
(b) 轉成 FLAC（WAV 標頭以 foreign metadata 保存）；
(c) FLAC 解碼回 PCM 與原 WAV 的 PCM 逐位元比對，並還原整份 WAV 與原檔逐位元比對；
    一致才在同一個 artifact ID 上換成 FLAC、更新 media_type／sha256／size，再刪除 WAV；
(d) 任何一步失敗都保留該檔原狀並回報，繼續處理下一檔，結束碼為 1。
artifact ID 與分集保存的 `audio.sha256`（原 WAV 的雜湊）都不變，既有影片、分鏡對齊、
時間軸與收聽進度的綁定因此維持有效；備份由管理者驗收後自行刪除。

正式執行全程持有一個執行層級的 PostgreSQL advisory lock：同時只能有一個轉檔程序，
上次中斷的收尾、暫存寫入、提交與提交後刪除 WAV 都在這個鎖內。暫存檔名含每次執行的 ID。
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import stat
import sys
from uuid import UUID, uuid4

from sqlalchemy import select, text

from .audio import FLAC_MEDIA_TYPE, WAV_MEDIA_TYPE, _decode_pcm, _wav_parts, flac_to_wav, wav_to_flac
from .storage.artifacts import _lock_artifact_discard, _object_path, _root, _sync_directory
from .storage.database import database_session
from .storage.tables import Artifact

_NEW_SUFFIX = ".flac-new"
_OLD_SUFFIX = ".wav-before-flac"
_RUN_LOCK = 4_817_602_311_905_772_031
# FLAC 暫存最大不超過原 WAV＋少量 metadata；預留 1 MiB。
_FLAC_OVERHEAD = 1024 * 1024


class ConversionError(RuntimeError):
    pass


def _candidates(dsn, limit):
    with database_session(dsn) as db:
        query = (
            select(Artifact.artifact_id, Artifact.sha256, Artifact.size_bytes)
            .where(Artifact.kind == "podcast_audio", Artifact.media_type == WAV_MEDIA_TYPE)
            .order_by(Artifact.created_at, Artifact.artifact_id)
        )
        if limit is not None:
            query = query.limit(limit)
        return [(row.artifact_id, bytes(row.sha256), row.size_bytes) for row in db.execute(query)]


def _read_verified(path, digest, size):
    data = path.read_bytes()
    if len(data) != size or sha256(data).digest() != digest:
        raise ConversionError("WAV_HASH_MISMATCH")
    return data


def _write_synced(path, data, mode, run):
    """先寫入 `<名稱>.<run>.tmp` 再改名；任何失敗都刪掉暫存。"""
    temporary = path.with_name(f"{path.name}.{run}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
        _sync_directory(path.parent)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _backup(backup_dir, artifact_id, wav, run):
    target = backup_dir / f"{artifact_id.hex}.wav"
    if not (target.exists() and sha256(target.read_bytes()).digest() == sha256(wav).digest()):
        target.unlink(missing_ok=True)
        _write_synced(target, wav, 0o400, run)
    if target.read_bytes() != wav:
        raise ConversionError("BACKUP_MISMATCH")
    return target


def _encode_checked(wav):
    flac = wav_to_flac(wav)
    # 獨立再解一次：PCM 逐位元一致，且整份 WAV（含標頭區塊）可原樣還原。
    if _decode_pcm(flac) != _wav_parts(wav)[1] or flac_to_wav(flac) != wav:
        raise ConversionError("FLAC_ROUNDTRIP_MISMATCH")
    return flac


def _settle(root, artifact_id, old, dsn):
    """依 DB 實際狀態收尾一次中斷的替換：已提交 FLAC 就刪暫存 WAV，未提交就還原 WAV。"""
    destination = _object_path(root, artifact_id)
    with database_session(dsn) as db:
        _lock_artifact_discard(db, artifact_id)
        row = db.get(Artifact, artifact_id)
        if not old.exists():
            return
        current = destination.read_bytes() if destination.exists() else None
        if (
            row is not None
            and row.media_type == FLAC_MEDIA_TYPE
            and current is not None
            and sha256(current).digest() == bytes(row.sha256)
        ):
            old.unlink()
        elif (
            row is not None
            and row.media_type == WAV_MEDIA_TYPE
            and sha256(old.read_bytes()).digest() == bytes(row.sha256)
        ):
            os.replace(old, destination)
            _sync_directory(destination.parent)
        elif row is None:
            old.unlink()
        else:
            raise ConversionError("MANUAL_RECOVERY_REQUIRED")
        _sync_directory(old.parent)


def recover(dsn=None, backup_dir=None):
    """收尾上次中斷的執行；呼叫端須持有執行鎖。回傳收尾的替換數。"""
    root = _root()
    staging = root / ".staging"
    journals = sorted(staging.glob(f"*{_OLD_SUFFIX}"))
    for old in journals:
        _settle(root, UUID(hex=old.name.split(".", 1)[0]), old, dsn)
    for leftover in [*staging.glob(f"*{_NEW_SUFFIX}"), *staging.glob(f"*{_NEW_SUFFIX}.*.tmp")]:
        leftover.unlink(missing_ok=True)
    if backup_dir is not None and Path(backup_dir).is_dir():
        for leftover in Path(backup_dir).glob("*.wav.*.tmp"):
            leftover.unlink(missing_ok=True)
    _sync_directory(staging)
    return len(journals)


def _replace(root, artifact_id, expected_digest, expected_size, flac, dsn, run):
    staging = root / ".staging"
    new = staging / f"{artifact_id.hex}.{run}{_NEW_SUFFIX}"
    old = staging / f"{artifact_id.hex}.{run}{_OLD_SUFFIX}"
    destination = _object_path(root, artifact_id)
    replaced = False
    try:
        _write_synced(new, flac, 0o400, run)
        with database_session(dsn) as db:
            # 與刪除 Podcast／教材的 quarantine 互斥；檢查期間 artifact 不能被換掉。
            _lock_artifact_discard(db, artifact_id)
            row = db.get(Artifact, artifact_id, with_for_update=True)
            if (
                row is None
                or row.kind != "podcast_audio"
                or row.media_type != WAV_MEDIA_TYPE
                or bytes(row.sha256) != expected_digest
                or row.size_bytes != expected_size
            ):
                raise ConversionError("ARTIFACT_CHANGED")
            details = destination.stat(follow_symlinks=False)
            if not stat.S_ISREG(details.st_mode) or details.st_size != expected_size:
                raise ConversionError("ARTIFACT_CHANGED")
            os.link(destination, old)
            _sync_directory(staging)
            os.replace(new, destination)
            replaced = True
            _sync_directory(destination.parent)
            row.media_type = FLAC_MEDIA_TYPE
            row.sha256 = sha256(flac).digest()
            row.size_bytes = len(flac)
            db.flush()
    except Exception:
        new.unlink(missing_ok=True)
        # 未提交就還原 WAV；提交結果不明時依 DB 實際狀態收尾。
        if replaced or old.exists():
            _settle(root, artifact_id, old, dsn)
        raise
    old.unlink()
    _sync_directory(staging)


def _require_space(root, backup_dir, candidates):
    """轉換峰值：備份位置要放下全部 WAV；artifact 磁碟要放下最大一檔的 FLAC 暫存
    （舊 WAV 在提交前仍保留）。兩者同一個檔案系統時合併計算。"""
    if not candidates:
        return
    backups = sum(size for _, _, size in candidates)
    staging = max(size for _, _, size in candidates) + _FLAC_OVERHEAD
    if os.stat(backup_dir).st_dev == os.stat(root).st_dev:
        if shutil.disk_usage(root).free < backups + staging:
            raise ConversionError("DISK_SPACE_INSUFFICIENT")
    else:
        if shutil.disk_usage(backup_dir).free < backups:
            raise ConversionError("BACKUP_SPACE_INSUFFICIENT")
        if shutil.disk_usage(root).free < staging:
            raise ConversionError("ARTIFACT_SPACE_INSUFFICIENT")


def _convert(root, backup_dir, dry_run, limit, dsn, report, recovered):
    run = uuid4().hex[:12]
    candidates = _candidates(dsn, limit)
    if not dry_run:
        _require_space(root, backup_dir, candidates)
    summary = {
        "dry_run": dry_run,
        "recovered": recovered,
        "candidates": len(candidates),
        "converted": 0,
        "failed": 0,
        "wav_bytes": 0,
        "flac_bytes": 0,
    }
    for artifact_id, digest, size in candidates:
        item = {"artifact_id": str(artifact_id), "wav_bytes": size}
        try:
            wav = _read_verified(_object_path(root, artifact_id), digest, size)
            if not dry_run:
                _backup(backup_dir, artifact_id, wav, run)
            flac = _encode_checked(wav)
            item["flac_bytes"] = len(flac)
            if not dry_run:
                _replace(root, artifact_id, digest, size, flac, dsn, run)
            item["status"] = "would_convert" if dry_run else "converted"
            summary["converted"] += 1
            summary["wav_bytes"] += size
            summary["flac_bytes"] += len(flac)
        except Exception as error:
            item["status"] = "failed"
            item["error"] = str(error) or type(error).__name__
            summary["failed"] += 1
        report(json.dumps(item, ensure_ascii=False))
    summary["saved_bytes"] = summary["wav_bytes"] - summary["flac_bytes"]
    report(json.dumps({"summary": summary}, ensure_ascii=False))
    return summary


def convert(*, backup_dir=None, dry_run=False, limit=None, dsn=None, report=print):
    root = _root()
    if dry_run:
        return _convert(root, None, True, limit, dsn, report, 0)
    if backup_dir is None:
        raise ConversionError("BACKUP_DIR_REQUIRED")
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with database_session(dsn) as guard:
        if not guard.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": _RUN_LOCK}):
            raise ConversionError("ANOTHER_CONVERSION_RUNNING")
        recovered = recover(dsn, backup_dir)
        return _convert(root, backup_dir, False, limit, dsn, report, recovered)


def main(argv=None):
    parser = argparse.ArgumentParser(description="既有 Podcast WAV 逐檔轉為無損 FLAC")
    parser.add_argument("--backup-dir", help="正式轉換前保存原 WAV 的目錄")
    parser.add_argument("--dry-run", action="store_true", help="只列出會轉換的檔案與預估節省空間")
    parser.add_argument("--limit", type=int, help="只處理最早建立的前 N 檔")
    arguments = parser.parse_args(argv)
    if not arguments.dry_run and not arguments.backup_dir:
        parser.error("正式轉換必須指定 --backup-dir")
    if arguments.limit is not None and arguments.limit < 1:
        parser.error("--limit 必須大於 0")
    summary = convert(
        backup_dir=arguments.backup_dir,
        dry_run=arguments.dry_run,
        limit=arguments.limit,
    )
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
