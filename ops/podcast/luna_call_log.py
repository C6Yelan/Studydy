"""Luna 呼叫額度與本機紀錄；候選只在工作最終失敗後落地。"""

from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
from threading import Lock
import time
from uuid import uuid4


LIMIT_EXCEEDED = "LUNA_CALL_LIMIT_EXCEEDED"
FEATURES = {
    "material_analysis", "material_review", "material_consolidation",
    "note_summary", "note_full", "chapters", "assessment", "assessment_check",
    "cards", "podcast_title", "podcast_script", "podcast_scenes", "podcast_video",
    "voice", "topic", "research",
}
FIELDS = {"feature", "material_id", "learner_id", "work_id", "attempt_id", "call_limit", "budget", "stage"}
_current = ContextVar("luna_request", default=None)


def normalize_tags(value):
    """只接受呼叫端明定的識別欄位；未知欄位不進一般呼叫紀錄。"""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - FIELDS:
        raise ValueError("LUNA_TAGS_INVALID")
    if value.get("feature") is not None and value["feature"] not in FEATURES:
        raise ValueError("LUNA_TAGS_INVALID")
    for field in ("material_id", "learner_id", "work_id", "attempt_id"):
        item = value.get(field)
        if item is not None and (not isinstance(item, str) or not item or len(item) > 1024):
            raise ValueError("LUNA_TAGS_INVALID")
    if type(value.get("call_limit")) is not int or value["call_limit"] < 1:
        raise ValueError("LUNA_TAGS_INVALID")
    if value.get("budget", value.get("feature")) not in FEATURES | {"material", None}:
        raise ValueError("LUNA_TAGS_INVALID")
    if value.get("stage") not in {None, "analysis", "review", "consolidation"}:
        raise ValueError("LUNA_TAGS_INVALID")
    return {key: value.get(key) for key in FIELDS if key in value}


def scope_key(tags):
    if tags is None:
        return None
    # 研究可能在同一次 attempt 由沒有教材變成有教材；不能因此取得新額度。
    return (tags.get("learner_id"), tags.get("budget") or tags.get("feature"), tags.get("work_id"), tags.get("attempt_id"))


def log_paths(path):
    path = Path(path)
    return sorted(path.parent.glob(path.name + ".????-??-??")) + ([path] if path.exists() else [])


def read_events(path):
    for source in log_paths(path):
        for line in source.read_text(encoding="utf-8").splitlines():
            try:
                yield json.loads(line)
            except (TypeError, ValueError):
                # 不輸出原行，避免損壞檔案將非預期內容帶進終端或一般 log。
                raise RuntimeError("LUNA_CALL_LOG_INVALID") from None


def call_records(path):
    """started／finished 是同一 call_id 的一筆邏輯呼叫，不能加總為兩次。"""
    calls = {}
    blocked = []
    for event in read_events(path):
        if event["event"] == "blocked":
            blocked.append(event)
        elif event["event"] == "started":
            calls[event["call_id"]] = {**event, "outcome": "interrupted"}
        elif event["event"] == "finished" and event["call_id"] in calls:
            calls[event["call_id"]].update(
                outcome=event["outcome"], error_code=event["error_code"], elapsed_seconds=event["elapsed_seconds"]
            )
    return list(calls.values()) + blocked


class CallLog:
    def __init__(self, path, *, now=None):
        self.path = Path(path)
        self.failure_dir = self.path.parent / "failures"
        self.now = now or (lambda: datetime.now(UTC))
        self.lock = Lock()
        self.counts = Counter()
        self.reserved = Counter()
        self.pending = {}
        self.day = None
        self.cleanup()
        for event in read_events(self.path):
            if event["event"] == "started":
                self.counts[scope_key(event.get("tags"))] += 1
        if self.path.exists():
            with self.path.open(encoding="utf-8") as stream:
                first = stream.readline()
            if first:
                try:
                    self.day = json.loads(first)["at"][:10]
                except (TypeError, ValueError, KeyError):
                    raise RuntimeError("LUNA_CALL_LOG_INVALID") from None

    def cleanup(self):
        now = self.now()
        cutoff = (now - timedelta(days=90)).date()
        for source in log_paths(self.path):
            if source == self.path:
                date = datetime.fromtimestamp(source.stat().st_mtime, UTC).date()
            else:
                date = datetime.strptime(source.name[-10:], "%Y-%m-%d").date()
            if date <= cutoff:
                source.unlink()
        if self.failure_dir.exists():
            for source in self.failure_dir.glob("*.json"):
                if source.stat().st_mtime < (now - timedelta(days=30)).timestamp():
                    source.unlink()
        for key, entry in list(self.pending.items()):
            if entry["at"] < now - timedelta(days=30):
                del self.pending[key]

    def _write(self, event):
        self.cleanup()
        day = self.now().date().isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.day is not None and self.day != day and self.path.exists():
            self.path.rename(self.path.with_name(self.path.name + "." + self.day))
        self.day = day
        try:
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            raise RuntimeError("LUNA_CALL_LOG_FAILED") from None

    def reserve(self, tags):
        ticket = Ticket(self, tags)
        with self.lock:
            key = scope_key(tags)
            if tags is not None and self.counts[key] + self.reserved[key] >= tags["call_limit"]:
                self._write(ticket.event("blocked", "blocked", LIMIT_EXCEEDED))
                raise RuntimeError(LIMIT_EXCEEDED)
            self.reserved[key] += 1
        return ticket

    @contextmanager
    def request(self, tags, path):
        request = {"store": self, "tags": tags, "path": path}
        token = _current.set(request)
        try:
            yield
        finally:
            _current.reset(token)

    def finalize(self, tags, outcome, reason):
        if tags is None or outcome not in {"succeeded", "failed", "cancelled"}:
            raise ValueError("LUNA_TAGS_INVALID")
        if reason is not None and (not isinstance(reason, str) or len(reason) > 100 or not all(c.isupper() or c.isdigit() or c == "_" for c in reason)):
            raise ValueError("LUNA_TAGS_INVALID")
        with self.lock:
            self.cleanup()
            key = scope_key(tags)
            entry = self.pending.get(key)
            material = tags.get("budget") == "material"
            if outcome == "failed" and entry and not material:
                name = sha256(json.dumps(key).encode()).hexdigest() + ".json"
                target = self.failure_dir / name
                temporary = self.failure_dir / (name + ".tmp")
                value = {"at": self.now().isoformat(), "tags": tags, "reason": reason, "candidates": entry["candidates"]}
                try:
                    self.failure_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                    self.failure_dir.chmod(0o700)
                    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                        json.dump(value, stream, ensure_ascii=False)
                        stream.flush()
                        os.fsync(stream.fileno())
                    temporary.replace(target)
                except OSError:
                    temporary.unlink(missing_ok=True)
                    raise RuntimeError("LUNA_FAILURE_RECORD_FAILED") from None
            self.pending.pop(key, None)
        result = {"succeeded": True, "saved": outcome == "failed" and entry is not None and not material,
                  "candidate_available": entry is not None}
        if material and outcome == "failed" and entry:
            # 只有終態失敗才交回 backend 寫入既有教材 archive；此處不另存副本。
            result["candidates"] = entry["candidates"]
        return result


class Ticket:
    def __init__(self, store, tags):
        self.store, self.tags = store, tags
        self.call_id = str(uuid4())
        self.started_at = None
        self.at = store.now().isoformat()

    def event(self, event, outcome, error_code=None):
        return {"event": event, "call_id": self.call_id, "at": self.at if event == "started" else self.store.now().isoformat(),
                "tags": self.tags, "elapsed_seconds": round(time.monotonic() - self.started_at, 6) if self.started_at is not None else 0,
                "outcome": outcome, "error_code": error_code, "counted": event == "started"}

    def started(self):
        with self.store.lock:
            self.at = self.store.now().isoformat()
            self.started_at = time.monotonic()
            key = scope_key(self.tags)
            self.store.reserved[key] -= 1
            self.store.counts[key] += 1
            self.store._write(self.event("started", "running"))

    def finish(self, error_code=None):
        with self.store.lock:
            if self.started_at is None:
                self.store.reserved[scope_key(self.tags)] -= 1
                self.store._write(self.event("blocked", "blocked", error_code))
            else:
                outcome = "cancelled" if error_code in {"PODCAST_CANCELLED", "VIDEO_CANCELLED"} else "failed" if error_code else "succeeded"
                self.store._write(self.event("finished", outcome, error_code))


def begin_call():
    request = _current.get()
    return request["store"].reserve(request["tags"]) if request is not None else None


def capture_candidate(value, *, failed=False):
    request = _current.get()
    if request is None or request["tags"] is None:
        return
    # 正常教材回覆已由 backend 封存；解析／CLI 失敗的輸出先留記憶體等終態交回。
    if request["tags"].get("budget") == "material" and not failed:
        return
    if failed or request["path"] in {"/script", "/video"}:
        store = request["store"]
        with store.lock:
            entry = store.pending.setdefault(scope_key(request["tags"]), {"at": store.now(), "candidates": []})
            entry["candidates"].append({"candidate": value})
