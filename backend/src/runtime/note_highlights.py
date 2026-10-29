"""個人標記：列出、新增、刪除；位置隨目前筆記內容重新對應，原位置與段落快照保留在資料庫。"""
from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from . import material_notes, note_anchors
from .errors import SourceError
from .storage import knowledge_reads
from .storage.database import database_session
from .storage.tables import MaterialNotes, NoteHighlight

STYLES = ("yellow", "green", "blue", "pink", "bold", "underline", "strikethrough")


def resolve(view, rows):
    """依目前可見筆記內容產生回應；無法對應的標記位置為 None，其餘資料保留。"""
    result = []
    for h in rows:
        found = note_anchors.locate(view["paragraphs"], {
            "original_revision": h.structure_revision, "paragraph_id": h.paragraph_id,
            "anchor": h.paragraph_snapshot["anchor"], "block_path": h.block_path,
            "start": h.start_offset, "end": h.end_offset, "quote": h.quote, "original_generation": h.generation},
            view["content_revision"], view["content_generation"])
        paragraph_id, path, start, end = found or (None, None, None, None)
        result.append({"highlight_id": str(h.highlight_id), "paragraph_id": paragraph_id,
            "block_path": path, "start": start, "end": end, "quote": h.quote, "style": h.style,
            "created_at": h.created_at.isoformat(), "original_revision": h.structure_revision,
            "original_generation": h.generation, "original_paragraph": h.paragraph_snapshot})
    return result


def _rows(db, owner, material_id, kind):
    return list(db.scalars(select(NoteHighlight).where(NoteHighlight.learner_id == owner,
        NoteHighlight.material_id == material_id, NoteHighlight.kind == kind)
        .order_by(NoteHighlight.created_at, NoteHighlight.highlight_id)))


def listing(owner, material_id, revision, kind, *, dsn=None, view=None):
    view = view or material_notes.read(owner, material_id, revision, kind, dsn=dsn)
    with database_session(dsn) as db:
        return resolve(view, _rows(db, owner, material_id, kind))


def add(owner, material_id, revision, kind, expected_generation, paragraph_id, block_path,
        start, end, quote, style, *, dsn=None):
    with database_session(dsn) as db:
        material_notes._material(db, owner, material_id, lock=True)
        knowledge_reads.metadata(db, owner, material_id, revision)
        row = db.get(MaterialNotes, (owner, material_id, revision, kind))
        if row is None or row.generation != expected_generation:
            raise SourceError("REVISION_CONFLICT")
        paragraph = next((p for p in row.paragraphs or [] if p["id"] == paragraph_id), None)
        if paragraph is None or not note_anchors.valid_range(paragraph, block_path, start, end, quote):
            raise SourceError("REQUEST_INVALID")
        highlight = NoteHighlight(highlight_id=uuid4(), learner_id=owner, material_id=material_id,
            kind=kind, structure_revision=revision, generation=row.generation, paragraph_id=paragraph_id,
            block_path=block_path, start_offset=start, end_offset=end, quote=quote, style=style,
            paragraph_snapshot=deepcopy(paragraph), created_at=datetime.now(UTC))
        db.add(highlight)
        db.flush()
        return {"highlight_id": str(highlight.highlight_id), "paragraph_id": paragraph_id,
            "block_path": block_path, "start": start, "end": end, "quote": quote, "style": style,
            "created_at": highlight.created_at.isoformat(), "original_revision": revision,
            "original_generation": row.generation, "original_paragraph": highlight.paragraph_snapshot}


def remove(owner, material_id, kind, highlight_id, *, dsn=None):
    with database_session(dsn) as db:
        material_notes._material(db, owner, material_id, lock=True)
        highlight = db.get(NoteHighlight, highlight_id, with_for_update=True)
        if highlight is None or (highlight.learner_id, highlight.material_id, highlight.kind) != (owner, material_id, kind):
            raise SourceError("RESOURCE_NOT_FOUND")
        db.delete(highlight)
