"""固定來源的分集 Podcast；每次只確認一個腳本或音訊，重試不重做已保存結果。"""
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
import io
import json
import math
import wave
from uuid import UUID, uuid4

from sqlalchemy import delete, or_, select, update

from content_identity import canonical_sha256
from .card_sets import _material, _validate_selection
from .storage.artifacts import _key_digest, quarantine_source_pdf, reconcile_discarded_sources
from .storage.tables import KnowledgeStructure
from .storage.knowledge_structures import _prune_unreferenced_structures, _read_verified_document, _view
from .storage.source_artifacts import write_blob
from .storage.tables import Artifact, Material, Podcast, PodcastScenes, PodcastVideo, database_session
from .podcast_script import MAX_EPISODE_CLAIMS, MAX_SOURCE_TOKENS, MAX_SCRIPT_INPUT_TOKENS, script_input
from . import retrieval
from .semantic_service import _luna_token_count
from .errors import SourceError


from .generation_jobs import WORKER_LEASE_TTL


class PodcastError(RuntimeError):
    pass


def _summary(row, material, db):
    videos = list(db.scalars(select(PodcastVideo.status).where(PodcastVideo.podcast_id == row.podcast_id)))
    scenes = list(db.scalars(select(PodcastScenes.status).where(PodcastScenes.podcast_id == row.podcast_id)))
    # 分鏡 worker 只接 ready 節目；失敗節目的待處理分鏡不代表仍在生成。
    active_children = videos + (scenes if row.status == 'ready' else [])
    generation = {'active': row.status in ('pending', 'running') or any(status in ('pending', 'running') for status in active_children),
                  'completed_videos': videos.count('ready'), 'failed_videos': videos.count('failed'),
                  'preview_ready': row.status == 'ready' and all(e.get('audio') for e in row.episodes)
                                   and videos.count('ready') == len(row.episodes),
                  'retryable_videos': row.status == 'ready' and
                                      (len(videos) < len(row.episodes) or any(s in ('failed', 'cancelled') for s in videos))}
    return {"podcast_id": row.podcast_id, "material_id": row.material_id,
            "material_name": material.display_name, "name": row.name,
            "delivery": row.episodes[0]["delivery"],
            "knowledge_structure_revision": row.knowledge_structure_revision,
            "concept_ids": row.concept_ids, "status": row.status, "error_code": row.error_code,
            "version": row.version, "created_at": row.created_at,
            "episode_count": len(row.episodes), "generation": generation,
            "completed_episodes": sum(bool(e.get("audio")) for e in row.episodes),
            "is_current_revision": row.knowledge_structure_revision == material.head_revision}


def plan_episodes(view, concept_ids, source_pages):
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
    # 每個邊界的關係代價只計算一次；千頁教材不在每個候選分集重掃整張關係圖。
    relation_cuts = [0] * (len(claims) + 1)
    for relation in relations:
        ends = positions[relation['source_concept_id']] + positions[relation['target_concept_id']]
        relation_cuts[min(ends) + 1] += 1
        relation_cuts[max(ends) + 1] -= 1
    for i in range(1, len(relation_cuts)):
        relation_cuts[i] += relation_cuts[i-1]
    costs = [(0, 0, 0, 0)] + [None] * len(claims)
    previous = [None] * (len(claims) + 1)
    # 依 provider 實際送入模型的投影估算，不把僅供回填的 hash／locator 算成講稿輸入。
    projected = script_input(claims, source_pages)
    token_sizes = [_luna_token_count(json.dumps(source, ensure_ascii=False)) + 4
                   for source in projected['sources']]
    # 引用片語可能只佔原區塊一小部分；分集必須另計索引原文及定位欄位，不能只算 claim.quote。
    block_costs = {}; page_costs = {}
    for (page_ref, page), compact in zip(source_pages.items(), projected['page_context']):
        page_costs[page_ref] = _luna_token_count(json.dumps({**compact,'blocks':[]},ensure_ascii=False)) + 4
        for block, compact_block in zip(page['blocks'], compact['blocks']):
            block_costs[block['evidence_id']] = (page_ref, _luna_token_count(json.dumps(compact_block,ensure_ascii=False)) + 4)
    if any(e['evidence_id'] not in block_costs for claim in claims for e in claim['evidence']):
        raise PodcastError('PODCAST_SOURCE_INSUFFICIENT')
    for end in range(1, len(claims) + 1):
        tokens = 0
        context_tokens = 2; seen_blocks = set(); seen_pages = set()
        for start in range(end - 1, max(-1, end - MAX_EPISODE_CLAIMS - 1), -1):
            tokens += token_sizes[start]
            for evidence in claims[start]['evidence']:
                identity = evidence['evidence_id']
                if identity in seen_blocks:continue
                page_ref, cost = block_costs[identity]
                context_tokens += cost; seen_blocks.add(identity)
                if page_ref not in seen_pages:
                    context_tokens += page_costs[page_ref]; seen_pages.add(page_ref)
            if context_tokens > MAX_SOURCE_TOKENS or tokens + context_tokens + 32 > MAX_SCRIPT_INPUT_TOKENS:
                break
            if costs[start] is None:
                continue
            inside = int(start > 0 and claims[start-1]['concept_id'] == claims[start]['concept_id'])
            crossed = 0
            if start:
                crossed = relation_cuts[start]
                # 同一引用區塊通常是連續定義／步驟；同分時避免從中間切開。
                crossed += bool({e['evidence_id'] for e in claims[start-1]['evidence'] if 'evidence_id' in e}
                                & {e['evidence_id'] for e in claims[start]['evidence'] if 'evidence_id' in e})
            delta = (inside, crossed, 1, (MAX_EPISODE_CLAIMS - (end-start)) ** 2)
            cost = tuple(a+b for a, b in zip(costs[start], delta))
            if costs[end] is None or cost < costs[end]:
                costs[end], previous[end] = cost, start
    if previous[-1] is None:
        raise PodcastError('PODCAST_SOURCE_TOO_LARGE')
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
            return _summary(row, material, db)
        document = _read_verified_document(db, owner, material_id, revision=revision)
        retrieval._scope(db, owner, material_id, revision)
        view = _view(document, material_id)
        selected = set(concept_ids)
        ids = {e['evidence_id'] for c in view['concepts'] if c['concept_id'] in selected
               for claim in c['claims'] for e in claim['evidence']}
        passages = retrieval.evidence_context(db, owner, material_id, revision, ids)
        pages = _context_pages([p for p in passages if p['evidence_id'] in ids])
        episodes = plan_episodes(view, concept_ids, pages)
        for episode in episodes:
            episode["delivery"] = delivery
        # 舊模式欄位保留歷史資料；新建固定儲存值，不再參與 API 或生成決策。
        row = Podcast(podcast_id=uuid4(), learner_id=owner, material_id=material_id,
            knowledge_structure_revision=revision, name=name, mode="quick", concept_ids=concept_ids,
            episodes=episodes, status="pending", version=1, created_at=datetime.now(UTC),
            idempotency_key_sha256=digest, request_fingerprint=fingerprint)
        db.add(row)
        db.flush()
        return _summary(row, material, db)


def _locked(db, owner, identity, *, allow_deleted=False, read=False):
    material_id = db.scalar(select(Podcast.material_id).where(
        Podcast.podcast_id == identity, Podcast.learner_id == owner))
    if material_id is None:
        raise PodcastError("RESOURCE_NOT_FOUND")
    if read:
        # 固定 revision 的唯讀驗證可能耗時；不持有教材鎖阻擋生成結果保存。
        material = db.scalar(select(Material).where(Material.material_id == material_id,
            Material.learner_id == owner, Material.discard_requested_at.is_(None)))
        if material is None:
            raise PodcastError('RESOURCE_NOT_FOUND')
    else:
        material = _material(db, owner, material_id)
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
        return {"schema": "podcast-list/v1", "podcasts": [_summary(*row, db) for row in rows]}


def read_podcast(owner, identity, *, dsn=None):
    with database_session(dsn) as db:
        row, material = _locked(db, owner, identity, read=True)
        from .storage.knowledge_reads import metadata
        meta = metadata(db, owner, row.material_id, row.knowledge_structure_revision)
        run_id = db.scalar(select(KnowledgeStructure.run_id).where(
            KnowledgeStructure.learner_id == owner, KnowledgeStructure.material_id == row.material_id,
            KnowledgeStructure.structure_revision == row.knowledge_structure_revision))
        # 播放頁僅需來源入口與狀態，不讀整份教材或展開概念視圖。
        from .podcast_script import digest
        episodes=deepcopy(row.episodes)
        for episode in episodes:
            episode['script_sha256']=digest(episode['script']) if episode['script'] else None
        return {"schema": "podcast/v1", "run_id":str(run_id), **_summary(row, material, db),
            "source_resolver": meta['source_resolver'],
            "source_status": meta['status'],
            "excluded_pages": meta['excluded_pages'], "episodes": episodes}


def change_podcast(owner, identity, action, expected_version, name=None, *, dsn=None):
    if action == 'cancel':
        # 與教材一致：取消整份生成即刪除，不留下可接續的暫停項目。
        return delete_podcast(owner, identity, expected_version=expected_version, dsn=dsn)
    with database_session(dsn) as db:
        row, material = _locked(db, owner, identity)
        if row.version != expected_version:
            raise PodcastError("PODCAST_CONFLICT")
        if action == 'retry':
            retrieval._scope(db,owner,row.material_id,row.knowledge_structure_revision)
        if action == "rename":
            row.name = _validate_selection(name, row.concept_ids)
        elif action == "retry" and row.status == "ready":
            from .podcast_videos import enqueue
            if not _summary(row, material, db)['generation']['retryable_videos']:
                raise PodcastError("PODCAST_CONFLICT")
            for index in range(len(row.episodes)):
                video = enqueue(db, row, index)
                if video.status in ('failed', 'cancelled'):
                    video.status = 'pending'
                    video.error_code = None
                    video.lease_token = video.lease_expires_at = None
                    video.version += 1
        elif action == "retry" and row.status in {"failed", "cancelled"}:
            if row.status == 'cancelled':
                for child in (PodcastVideo, PodcastScenes):
                    db.execute(update(child).where(child.podcast_id == identity, child.status == 'cancelled')
                               .values(status='pending', error_code=None, version=child.version + 1))
            row.status = "pending"
            row.error_code = None
        else:
            raise PodcastError("PODCAST_CONFLICT")
        row.version += 1
        db.flush()
        return _summary(row, material, db)


def delete_podcast(owner, identity, *, expected_version=None, dsn=None):
    artifacts = []
    try:
        with database_session(dsn) as db:
            row, material = _locked(db, owner, identity, allow_deleted=True)
            if expected_version is not None and row.version != expected_version:
                raise PodcastError("PODCAST_CONFLICT")
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


def _context_pages(passages):
    """還原已取回的原文；不連續片段須標出缺口，不能假接成完整區塊。"""
    pages = {}
    ends = {}
    for passage in passages:
        evidence = passage['evidence']
        page_ref = evidence["page_ref"]
        page = pages.setdefault(page_ref, {
            "source_id": evidence["source_id"], "source_name": evidence['source_name'],
            "normalized_page": evidence["normalized_page"], "blocks": [],
        })
        existing = next((block for block in page['blocks'] if block['evidence_id'] == evidence['evidence_id']), None)
        if existing:
            if passage['start'] != ends[evidence['evidence_id']]:
                existing['text'] += '\n[…]\n'
            existing['text'] += passage['text']
            ends[evidence['evidence_id']] = passage['end']
            continue
        page["blocks"].append({
            "evidence_id": evidence["evidence_id"], "block_order": evidence["block_order"],
            "text": passage['text'], "region": deepcopy(evidence["source_locator"]["region"]),
        })
        ends[evidence['evidence_id']] = passage['end']
    return pages


def source_context(db, owner, material_id, revision, episode):
    """全部必要引用先保留，再以剩餘容量補同頁鄰接區塊。"""
    ids = {e['evidence_id'] for c in episode['claims'] for e in c['evidence']}
    passages = retrieval.evidence_context(db, owner, material_id, revision, ids)
    selected = [p for p in passages if p['evidence_id'] in ids]
    pages = _context_pages(selected)
    def fits(context):
        projected = script_input(episode['claims'], context)
        return (_luna_token_count(json.dumps(projected['page_context'], ensure_ascii=False)) <= MAX_SOURCE_TOKENS
                and _luna_token_count(json.dumps(projected, ensure_ascii=False)) <= MAX_SCRIPT_INPUT_TOKENS)
    if not fits(pages):
        raise PodcastError('PODCAST_SOURCE_TOO_LARGE')
    for passage in passages:
        if passage['evidence_id'] in ids:continue
        proposed = _context_pages(sorted([*selected,passage],key=lambda p:p['position']))
        if fits(proposed):
            selected.append(passage);pages = proposed
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
            row.lease_expires_at = now + WORKER_LEASE_TTL
            row.version += 1
            index = next(i for i, e in enumerate(row.episodes) if not e.get("audio"))
            episode = deepcopy(row.episodes[index])
            context = {}
            try:
                if episode["script"] is None:
                    context = source_context(db, owner, row.material_id, row.knowledge_structure_revision, episode)
                else:
                    retrieval._scope(db,owner,row.material_id,row.knowledge_structure_revision)
            except (PodcastError, SourceError) as error:
                row.status='failed';row.error_code=str(error);row.lease_token=row.lease_expires_at=None
                continue
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
