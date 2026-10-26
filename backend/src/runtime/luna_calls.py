"""網站 Luna 工作的歸屬及終態診斷；候選只在最終失敗後寫入私人目錄。"""

from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from uuid import uuid4
import json
import logging
import os


LIMIT_EXCEEDED = "LUNA_CALL_LIMIT_EXCEEDED"
LIMIT_MESSAGE = "這次生成用量超過上限，已停止。可以稍後重試。"
LIMITS = {
    "material": 1144,
    "note_summary": 274,
    "note_full": 324,
    "assessment": 2,
    "podcast_script": 37,
    "podcast_title": 1,
    "podcast_scenes": 1,
    "podcast_video": 1,
    "voice": 10,
    "topic": 1,
    "research": 1832,
}
_current = ContextVar("luna_work", default=None)
_logger = logging.getLogger(__name__)
_pending = {}
_pending_lock = Lock()


def identity(value):
    """複合主鍵保留全部欄位；不把來源內容加入識別字。"""
    if isinstance(value, (list, tuple)):
        return json.dumps([str(part) for part in value], separators=(",", ":"))
    return str(value) if value is not None else None


@dataclass
class Work:
    tags: dict
    archive: str | None = None
    archive_writer: object = None
    parent: object = None
    children: list = field(default_factory=list)
    candidates: list = field(default_factory=list)
    reasons: list = field(default_factory=list)
    notified_tags: dict = field(default_factory=dict)
    lock: Lock = field(default_factory=Lock)
    outcome: str | None = None
    reason: str | None = None
    token: object = None


def _key(value):
    return (value["learner_id"], value["budget"], value["work_id"], value["attempt_id"])


def start(feature, work_id, attempt_id, material_id, learner_id, *,
          call_limit=None, budget=None, archive=None, enabled=True, defer_to_parent=False):
    budget = budget or feature
    work = Work({
        "feature": feature,
        "material_id": identity(material_id),
        "learner_id": identity(learner_id),
        "work_id": identity(work_id),
        "attempt_id": identity(attempt_id),
        "call_limit": LIMITS[budget] if call_limit is None else call_limit,
        "budget": budget,
    }, archive=archive)
    if enabled:
        with _pending_lock:
            previous = _pending.get(_key(work.tags))
            if previous is not None:
                work.candidates = previous.candidates
                work.reasons = previous.reasons
                work.notified_tags = previous.notified_tags
                work.lock = previous.lock
            _pending[_key(work.tags)] = work
    if defer_to_parent:
        work.parent = _current.get()
    work.token = _current.set(work if enabled else None)
    cleanup()
    return work


def set_archive_writer(writer):
    work = _current.get()
    if work is not None:
        work.archive_writer = writer


def tags(*, feature=None, stage=None):
    work = _current.get()
    if work is None:
        return None
    value = dict(work.tags)
    if feature is not None:
        value["feature"] = feature
    if stage is not None:
        value["stage"] = stage
    return value


def sent(value):
    work = _current.get()
    if work is not None and value is not None:
        with work.lock:
            work.notified_tags[value["feature"]] = value


def capture(candidate, *, call_tags=None):
    work = _current.get()
    if work is not None and work.archive is None:
        with work.lock:
            work.candidates.append({"tags": call_tags or tags(), "candidate": candidate})
    return candidate


def rejected(reason):
    work = _current.get()
    if work is not None:
        with work.lock:
            work.reasons.append(reason)


def completed(outcome, reason=None):
    """只能在所屬業務交易成功提交後呼叫；pending／失去 lease 不是成功。"""
    work = _current.get()
    if work is not None:
        work.outcome, work.reason = outcome, reason


def _directory():
    root = os.environ.get("STUDYDY_ARTIFACT_ROOT")
    if not root:
        return None
    directory = Path(root) / "failures"
    directory.mkdir(mode=0o700, exist_ok=True)
    directory.chmod(0o700)
    return directory


def cleanup():
    try:
        directory = _directory()
        if directory is None:
            return
        cutoff = (datetime.now(UTC) - timedelta(days=30)).timestamp()
        for path in directory.glob("*.json"):
            if path.stat().st_mtime < cutoff:
                path.unlink()
    except OSError:
        _logger.warning("LUNA_FAILURE_CLEANUP_FAILED")


def _finalize(work):
    """同 attempt 跨 worker 小步保留記憶體；只有業務終態才清除。"""
    try:
        if work.outcome is None:
            return
        if work.outcome == "failed":
            try:
                directory = _directory()
                if directory is None:
                    raise OSError("artifact root unavailable")
                document = {
                    "at": datetime.now(UTC).isoformat(),
                    "tags": work.tags,
                    "reason": work.reason,
                    "rejections": work.reasons,
                    **({"archive_reference": work.archive} if work.archive is not None else {
                        "candidates": work.candidates,
                        "candidate_available": bool(work.candidates),
                        "candidate_missing_reason": None if work.candidates else "NO_CANDIDATE_IN_THIS_EXECUTION",
                    }),
                }
                path = directory / f"{uuid4()}.json"
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                    json.dump(document, output, ensure_ascii=False)
            except (OSError, TypeError, ValueError):
                _logger.warning("LUNA_FAILURE_RECORD_FAILED")
        if work.notified_tags:
            from .provider_http import provider

            for value in work.notified_tags.values():
                try:
                    result = provider("/jobs/luna-result", {
                        "tags": value,
                        "outcome": work.outcome,
                        "reason": work.reason,
                    })
                    if work.archive is not None and result.get("candidates"):
                        if work.archive_writer is None:
                            raise RuntimeError("LUNA_ARCHIVE_UNAVAILABLE")
                        work.archive_writer(result["candidates"])
                except Exception:
                    # 終態已提交，不回滾產品結果；診斷失敗必須如實回報，不能宣稱保存成功。
                    _logger.warning("LUNA_FAILURE_NOTIFICATION_FAILED")
        for child in work.children:
            if work.outcome == "cancelled":
                child.outcome, child.reason = "cancelled", None
            _finalize(child)
    finally:
        if work.outcome is not None:
            with _pending_lock:
                _pending.pop(_key(work.tags), None)
            work.candidates.clear()
            work.reasons.clear()


def close(work):
    try:
        if work.parent is not None:
            work.parent.children.append(work)
        else:
            _finalize(work)
    finally:
        _current.reset(work.token)


def discard(feature, work_id, learner_id):
    """使用者取消／重試後清掉舊 attempt 的記憶體，不產生失敗檔。"""
    with _pending_lock:
        obsolete = [work for key, work in _pending.items()
                    if key[:3] == (identity(learner_id), feature, identity(work_id))]
    for work in obsolete:
        work.outcome, work.reason = "cancelled", None
        _finalize(work)
