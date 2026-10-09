"""固定來源的分集 Podcast；每次只確認一個腳本或音訊，重試不重做已保存結果。"""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import io
import math
import wave
from uuid import UUID, uuid4

from sqlalchemy import delete, or_, select

from pdf_evidence.ocr_page_evidence import canonical_sha256
from .card_sets import _material, _validate_selection
from .storage.artifacts import _key_digest, quarantine_source_pdf, reconcile_discarded_sources
from .storage.knowledge_structures import _prune_unreferenced_structures, _read_verified_document, _view
from .storage.source_artifacts import write_blob
from .storage.tables import Artifact, Material, Podcast, PodcastScenes, database_session
from .podcast_script import MAX_EPISODE_CLAIMS


class PodcastError(RuntimeError):
    pass


def _summary(row, material):
    return {"podcast_id": row.podcast_id, "material_id": row.material_id,
            "material_name": material.display_name, "name": row.name,
            "delivery": row.episodes[0]["delivery"],
            "knowledge_structure_revision": row.knowledge_structure_revision,
            "concept_ids": row.concept_ids, "status": row.status, "error_code": row.error_code,
            "version": row.version, "created_at": row.created_at,
            "episode_count": len(row.episodes),
            "completed_episodes": sum(bool(e.get("audio")) for e in row.episodes),
            "is_current_revision": row.knowledge_structure_revision == material.head_revision}


def plan_episodes(view, concept_ids):
    concepts = {c["concept_id"]: c for c in view["concepts"]}
    if not set(concept_ids) <= concepts.keys():
        raise PodcastError("REQUEST_INVALID")
    claims = []
    for identity in concept_ids:
        concept = concepts[identity]
        if not concept["claims"]:
            raise PodcastError("PODCAST_SOURCE_INSUFFICIENT")
        for claim in concept["claims"]:
            if not claim["evidence"]:
                raise PodcastError("PODCAST_SOURCE_INSUFFICIENT")
            if len(claim['text']) > 2400:
                raise PodcastError('PODCAST_SOURCE_TOO_LARGE')
            claims.append({**deepcopy(claim), "concept_id": identity, "label": concept["label"]})
    if not claims:
        return []
    # 只使用本 revision 已有的概念／關係與 Evidence；選取順序和 claim 原文不變。
    # 在同一硬容量內，先少切概念、再少切關係，才考慮集數與剩餘容量。
    positions = {}
    for i, claim in enumerate(claims):
        positions.setdefault(claim['concept_id'], []).append(i)
    relations = [r for r in view.get('relations', [])
                 if r['source_concept_id'] in positions and r['target_concept_id'] in positions]
    costs = [(0, 0, 0, 0)] + [None] * len(claims)
    previous = [None] * (len(claims) + 1)
    for end in range(1, len(claims) + 1):
        size = 0
        for start in range(end - 1, max(-1, end - MAX_EPISODE_CLAIMS - 1), -1):
            size += len(claims[start]['text'])
            if size > 2400:
                break
            inside = int(start > 0 and claims[start-1]['concept_id'] == claims[start]['concept_id'])
            crossed = 0
            if start:
                crossed = sum(min(positions[r['source_concept_id']] + positions[r['target_concept_id']]) < start
                              <= max(positions[r['source_concept_id']] + positions[r['target_concept_id']])
                              for r in relations)
                # 同一引用區塊通常是連續定義／步驟；同分時避免從中間切開。
                crossed += bool({e['evidence_id'] for e in claims[start-1]['evidence'] if 'evidence_id' in e}
                                & {e['evidence_id'] for e in claims[start]['evidence'] if 'evidence_id' in e})
            delta = (inside, crossed, 1, (MAX_EPISODE_CLAIMS - (end-start)) ** 2)
            cost = tuple(a+b for a, b in zip(costs[start], delta))
            if costs[end] is None or cost < costs[end]:
                costs[end], previous[end] = cost, start
    episodes = []
    end = len(claims)
    while end:
        start = previous[end]
        episodes.append({'claims': claims[start:end], 'script': None, 'audio': None})
        end = start
    return episodes[::-1]


def create_podcast(owner, material_id, revision, name, concept_ids, key, *, delivery, dsn=None):
    name = _validate_selection(name, concept_ids)
    if delivery not in {"solo", "dialogue"}:
        raise PodcastError("REQUEST_INVALID")
    digest = _key_digest(key)
    selection = {"revision": revision, "name": name, "concept_ids": concept_ids, "delivery": delivery}
    fingerprint = bytes.fromhex(canonical_sha256(selection))
    with database_session(dsn) as db:
        material = _material(db, owner, material_id)
        row = db.scalar(select(Podcast).where(Podcast.learner_id == owner,
            Podcast.material_id == material_id, Podcast.idempotency_key_sha256 == digest))
        if row:
            if bytes(row.request_fingerprint) != fingerprint:
                raise PodcastError("IDEMPOTENCY_CONFLICT")
            if row.status == "deleted":
                raise PodcastError("RESOURCE_NOT_FOUND")
            return _summary(row, material)
        document = _read_verified_document(db, owner, material_id, revision=revision)
        episodes = plan_episodes(_view(document, material_id), concept_ids)
        for episode in episodes:
            episode["delivery"] = delivery
        # 舊模式欄位保留歷史資料；新建固定儲存值，不再參與 API 或生成決策。
        row = Podcast(podcast_id=uuid4(), learner_id=owner, material_id=material_id,
            knowledge_structure_revision=revision, name=name, mode="quick", concept_ids=concept_ids,
            episodes=episodes, status="pending", version=1, created_at=datetime.now(UTC),
            idempotency_key_sha256=digest, request_fingerprint=fingerprint)
        db.add(row)
        db.flush()
        return _summary(row, material)


def _locked(db, owner, identity, *, allow_deleted=False, read=False):
    material_id = db.scalar(select(Podcast.material_id).where(
        Podcast.podcast_id == identity, Podcast.learner_id == owner))
    if material_id is None:
        raise PodcastError("RESOURCE_NOT_FOUND")
    material = _material(db, owner, material_id, read=read)
    row = db.get(Podcast, identity, populate_existing=True)
    if row is None or (row.status == "deleted" and not allow_deleted):
        raise PodcastError("RESOURCE_NOT_FOUND")
    return row, material


def list_podcasts(owner, *, dsn=None):
    with database_session(dsn) as db:
        rows = db.execute(select(Podcast, Material).join(Material,
            Podcast.material_id == Material.material_id).where(Podcast.learner_id == owner,
            Podcast.status != "deleted", Material.discard_requested_at.is_(None))
            .order_by(Podcast.created_at.desc())).all()
        return {"schema": "podcast-list/v1", "podcasts": [_summary(*row) for row in rows]}


def read_podcast(owner, identity, *, dsn=None):
    with database_session(dsn) as db:
        row, material = _locked(db, owner, identity, read=True)
        document = _read_verified_document(db, owner, row.material_id,
            revision=row.knowledge_structure_revision)
        view = _view(document, row.material_id)
        from .podcast_script import digest
        episodes=deepcopy(row.episodes)
        for episode in episodes:
            episode['script_sha256']=digest(episode['script']) if episode['script'] else None
        return {"schema": "podcast/v1", "run_id":document['run_id'], **_summary(row, material),
            "source_resolver": view["source_resolver"], "source_status": view["status"],
            "excluded_pages": view["excluded_pages"], "episodes": episodes}


def change_podcast(owner, identity, action, expected_version, name=None, *, dsn=None):
    with database_session(dsn) as db:
        row, material = _locked(db, owner, identity)
        if row.version != expected_version:
            raise PodcastError("PODCAST_CONFLICT")
        if action == "rename":
            row.name = _validate_selection(name, row.concept_ids)
        elif action == "cancel" and row.status in {"pending", "running"}:
            row.status = "cancelled"
            row.lease_token = row.lease_expires_at = None
        elif action == "retry" and row.status in {"failed", "cancelled"}:
            row.status = "pending"
            row.error_code = None
        else:
            raise PodcastError("PODCAST_CONFLICT")
        row.version += 1
        db.flush()
        return _summary(row, material)


def delete_podcast(owner, identity, *, dsn=None):
    artifacts = []
    try:
        with database_session(dsn) as db:
            row, material = _locked(db, owner, identity, allow_deleted=True)
            if row.status != "deleted":
                from .podcast_videos import discard
                artifacts.extend(discard(db,identity))
                db.execute(delete(PodcastScenes).where(PodcastScenes.podcast_id == identity))
                for episode in row.episodes:
                    if episode.get("audio"):
                        artifact_id = UUID(episode["audio"]["artifact_id"])
                        artifacts.append(artifact_id)
                        quarantine_source_pdf(db, artifact_id)
                        db.delete(db.get(Artifact, artifact_id))
                row.status = "deleted"
                row.lease_token = row.lease_expires_at = None
                row.knowledge_structure_revision = None
                row.name, row.concept_ids, row.episodes = "", [], []
                row.error_code = None
                row.version += 1
                db.flush()
                _prune_unreferenced_structures(db, owner, material.material_id, material.head_revision)
    finally:
        for artifact_id in artifacts:
            reconcile_discarded_sources(dsn=dsn, artifact_id=artifact_id)
    return {"schema": "podcast-deleted/v1", "podcast_id": identity}


def source_context(document, episode):
    """只帶入引用所在頁的原始區塊，補足表格欄列標題與省略主語；不改 claim 引用。"""
    selected_pages = {e["page_ref"] for c in episode["claims"] for e in c["evidence"]}
    binding = document["input_binding"]
    names = {s["source_id"]: s["original_name"] for s in binding["manifest"]["items"]}
    pages = {}
    for evidence in sorted(document["evidence"], key=lambda e: (e["page"], e["block_order"])):
        page_ref = evidence["page_ref"]
        if page_ref not in selected_pages:
            continue
        location = binding["bundle"]["pages"][evidence["page"] - 1]
        page = pages.setdefault(page_ref, {
            "source_id": location["source_id"], "source_name": names[location["source_id"]],
            "normalized_page": location["normalized_page"], "blocks": [],
        })
        page["blocks"].append({
            "evidence_id": evidence["evidence_id"], "block_order": evidence["block_order"],
            "text": evidence["exact_text"], "region": deepcopy(evidence["source_locator"]["region"]),
        })
    return pages


def claim_step(*, dsn=None):
    now = datetime.now(UTC)
    with database_session(dsn) as db:
        candidates = db.execute(select(Podcast.podcast_id, Podcast.learner_id).where(or_(
            Podcast.status == "pending",
            (Podcast.status == "running") & (Podcast.lease_expires_at < now)))
            .order_by(Podcast.created_at).limit(8)).all()
        for identity, owner in candidates:
            try:
                row, _ = _locked(db, owner, identity)
            except (PodcastError, RuntimeError):
                continue
            if row.status != "pending" and not (row.status == "running" and row.lease_expires_at < now):
                continue
            row.status, row.lease_token = "running", uuid4()
            row.lease_expires_at = now + timedelta(minutes=15)
            row.version += 1
            index = next(i for i, e in enumerate(row.episodes) if not e.get("audio"))
            episode = deepcopy(row.episodes[index])
            context = {}
            if episode["script"] is None:
                document = _read_verified_document(db, owner, row.material_id,
                    revision=row.knowledge_structure_revision)
                context = source_context(document, episode)
            return {"podcast_id": row.podcast_id, "owner": owner, "token": row.lease_token,
                "index": index, "episode": episode, "source_context": context}
    return None


def validate_script(script, episode):
    from .podcast_script import SCHEMA, validate
    if isinstance(script, dict) and script.get('schema') == SCHEMA:
        try:
            return validate(script, episode)
        except ValueError: raise PodcastError('PODCAST_SCRIPT_INVALID') from None
    claims = episode["claims"]
    if not isinstance(script, dict) or set(script) != {"segments", "provider"}:
        raise PodcastError("PODCAST_SCRIPT_INVALID")
    segments = script["segments"]
    if not isinstance(segments, list) or len(segments) != len(claims):
        raise PodcastError("PODCAST_SCRIPT_INVALID")
    for segment, claim in zip(segments, claims):
        if not isinstance(segment, dict) or segment.get("claim_id") != claim["claim_id"]:
            raise PodcastError("PODCAST_SCRIPT_INVALID")
        dialogue = episode["delivery"] == "dialogue"
        turns = segment.get("turns")
        speakers = {"host", "guest"} if dialogue else {"host"}
        if (set(segment) != {"claim_id", "turns"} or not isinstance(turns, list)
            or not 1 <= len(turns) <= (8 if dialogue else 3)
            or any(not isinstance(t, dict) or set(t) != {"speaker", "text"}
                or t["speaker"] not in speakers or not isinstance(t["text"], str)
                or not 5 <= len(t["text"].strip()) <= (400 if dialogue else 1600) for t in turns)
            or sum(len(t["text"]) for t in turns) > 1600):
            raise PodcastError("PODCAST_SCRIPT_INVALID")
    if episode["delivery"] == "dialogue" and {t["speaker"] for s in segments for t in s["turns"]} != {"host", "guest"}:
        raise PodcastError("PODCAST_SCRIPT_INVALID")
    if not isinstance(script["provider"], str) or not script["provider"]:
        raise PodcastError("PODCAST_SCRIPT_INVALID")
    return deepcopy(script)


def validate_audio(data):
    try:
        with wave.open(io.BytesIO(data), "rb") as audio:
            duration = audio.getnframes() / audio.getframerate()
            if (audio.getnchannels() != 1 or audio.getsampwidth() != 2
                or audio.getframerate() != 24000 or not 0.5 <= duration <= 1800
                or len(audio.readframes(audio.getnframes())) != audio.getnframes() * 2):
                raise ValueError()
        return duration
    except Exception:
        raise PodcastError("PODCAST_AUDIO_INVALID") from None


def finish_step(claim, *, script=None, audio=None, audio_provider=None, audio_mastering=None, error=None, dsn=None):
    with database_session(dsn) as db:
        try:
            row, _ = _locked(db, claim["owner"], claim["podcast_id"])
        except RuntimeError:
            return False
        if row.status != "running" or row.lease_token != claim["token"] or row.lease_expires_at <= datetime.now(UTC):
            return False
        episodes = deepcopy(row.episodes)
        episode = episodes[claim["index"]]
        if error:
            row.status, row.error_code = "failed", error
        else:
            if script is not None:
                episode["script"] = validate_script(script, episode)
            elif audio is not None and episode["script"]:
                if not isinstance(audio_provider, str) or not audio_provider.strip() or len(audio_provider) > 300:
                    raise PodcastError("PODCAST_AUDIO_INVALID")
                if audio_mastering is not None:
                    try:
                        if not (set(audio_mastering)=={'policy','integrated_lufs','true_peak_dbtp','loudness_range_lu'}):raise ValueError()
                        if not (audio_mastering['policy']=='podcast-mastering/v1'):raise ValueError()
                        if not (all(type(audio_mastering[k]) in (int,float) and math.isfinite(audio_mastering[k]) for k in ('integrated_lufs','true_peak_dbtp','loudness_range_lu'))):raise ValueError()
                    except (ValueError,KeyError,TypeError):raise PodcastError('PODCAST_AUDIO_INVALID') from None
                duration = validate_audio(audio)
                artifact = write_blob(db, row.learner_id, row.material_id, audio, "podcast_audio", "audio/wav")
                episode["audio"] = {"artifact_id": str(artifact.artifact_id),
                    "sha256": sha256(audio).hexdigest(), "duration_seconds": duration,
                    "provider": audio_provider, **({"mastering":audio_mastering} if audio_mastering is not None else {})}

            else:
                raise PodcastError("PODCAST_SCRIPT_INVALID")
            row.episodes = episodes
            if audio is not None:
                from .podcast_videos import enqueue
                enqueue(db,row,claim['index'])
                from .podcast_scenes import enqueue as enqueue_alignment
                enqueue_alignment(db,row,claim['index'])
            row.status = "ready" if all(e.get("audio") for e in episodes) else "pending"
        row.lease_token = row.lease_expires_at = None
        row.version += 1
        return True
