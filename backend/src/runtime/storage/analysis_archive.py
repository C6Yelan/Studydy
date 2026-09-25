"""失敗保留可續接狀態；發布完成清理 checkpoint，品質提示不阻擋清理。"""

from pathlib import Path
import json
import logging
import os
import shutil
import stat
import tempfile
import traceback
from uuid import UUID

from sqlalchemy import select

from pdf_evidence.ocr_page_evidence import canonical_bytes, canonical_sha256
from .artifacts import _root, _sync_directory
from .tables import Material, MaterialProcessingRun, database_session
from ..material_runtime import same_material_runtime


class AnalysisArchiveError(RuntimeError):
    pass


_logger = logging.getLogger(__name__)


def _checkpoint_input_digest(run):
    return canonical_sha256({
        'source_artifact_id': str(run.source_artifact_id),
        'source_set_id': str(run.input_source_set_id),
        'base_revision': run.base_revision,
        'runtime_binding': run.runtime_binding,
    })


def _material_directory(owner, material):
    return _root() / 'analysis' / owner.hex / material.hex


def _same_analysis_inputs(first, second):
    return (
        first.source_artifact_id == second.source_artifact_id
        and first.input_source_set_id == second.input_source_set_id
        and first.base_revision == second.base_revision
    )


def _can_reuse_analysis(first, second):
    return _same_analysis_inputs(first, second) and same_material_runtime(
        first.runtime_lock_document,
        first.runtime_binding,
        second.runtime_lock_document,
        second.runtime_binding,
    )


def _read_envelope(path, run_id, signature):
    saved = json.loads(path.read_bytes())
    if (
        saved['signature'] != signature
        or saved['run_id'] != str(run_id)
        or canonical_sha256(saved['data']) != saved['data_sha256']
    ):
        raise ValueError
    return saved


class AnalysisArchive:
    def __init__(self, claim, *, dsn):
        self.run = claim.run
        self.worker_token = claim.worker_token
        self.dsn = dsn
        self.directory = _material_directory(self.run.learner_id, self.run.material_id) / self.run.run_id.hex
        self.signature = _checkpoint_input_digest(self.run)
        self.reused_from = None
        self._loaded = False
        self._checkpoint = None
        self._review_response_runs = []
        self._replayed_responses = set()
        self.review_reuses = []

    def load_checkpoint(self):
        if self._loaded:
            return self._checkpoint
        with database_session(self.dsn) as session:
            prior = session.scalars(select(MaterialProcessingRun).where(
                MaterialProcessingRun.learner_id == self.run.learner_id,
                MaterialProcessingRun.material_id == self.run.material_id,
                MaterialProcessingRun.status == 'failed',
                MaterialProcessingRun.created_at < self.run.created_at,
            ).order_by(MaterialProcessingRun.created_at.desc())).all()
            candidates = []
            for row in prior:
                if not _same_analysis_inputs(row, self.run):
                    continue
                path = self.directory.parent / row.run_id.hex / 'checkpoint.json'
                if not path.exists():
                    continue
                if not _can_reuse_analysis(row, self.run):
                    raise AnalysisArchiveError('ANALYSIS_RUNTIME_CHANGED')
                candidates.append((row.run_id, row.error_code, _checkpoint_input_digest(row)))
        self._review_response_runs = [
            (run_id, signature)
            for run_id, error_code, signature in candidates
            if error_code == 'SOURCE_UPDATE_NEEDS_REVIEW'
        ]
        for run_id, error_code, signature in candidates:
            path = self.directory.parent / run_id.hex / 'checkpoint.json'
            if not path.exists():
                continue
            try:
                saved = _read_envelope(path, run_id, signature)
                self.reused_from = str(run_id)
                self._checkpoint = saved['data']
                self._loaded = True
                if error_code in {'NO_CANONICAL_CONCEPT', 'NO_USABLE_ADDED_CONTENT'}:
                    # 沒有可用語意結果時保留 Evidence，重試語意，不能永遠重組同一個空結果。
                    self._checkpoint['restart_semantics'] = True
                    self._checkpoint['complete'] = False
                return self._checkpoint
            except (OSError, ValueError, KeyError, TypeError):
                raise AnalysisArchiveError('ANALYSIS_CHECKPOINT_INVALID') from None
        self._loaded = True
        return None

    def reuse_review_response(self, request):
        """同一輸入重試時沿用僅因複核旗標拒絕的回應，避免重複推論。"""
        if self._checkpoint is None or self._checkpoint.get('restart_semantics'):
            return None
        for run_id, signature in self._review_response_runs:
            for path in sorted((self.directory.parent / run_id.hex).glob('call-*/decoded.json'), reverse=True):
                if path in self._replayed_responses:
                    continue
                try:
                    data = _read_envelope(path, run_id, signature)['data']
                    # checkpoint 的 JSON 會排序字典 key；catalog 的陣列順序因此可能改變。
                    # 概念以 k 識別，比對完整內容，但不把 catalog 排序誤當輸入改變。
                    original_request = {
                        **data['request'],
                        'existing_concepts': sorted(data['request'].get('existing_concepts', []), key=lambda item: item['k']),
                    }
                    current_request = {
                        **request,
                        'existing_concepts': sorted(request.get('existing_concepts', []), key=lambda item: item['k']),
                    }
                    if original_request == current_request and data['response'].get('review_required') is True:
                        self._replayed_responses.add(path)
                        return data['response']
                except (OSError, ValueError, KeyError, TypeError):
                    raise AnalysisArchiveError('ANALYSIS_CHECKPOINT_INVALID') from None
        return None

    def _write(self, name, data):
        try:
            # 序列化與 hash 不需要教材鎖；檔案發布仍與刪除共用交易邊界。
            encoded = canonical_bytes({
                'run_id': str(self.run.run_id),
                'signature': self.signature,
                'reused_from_run': self.reused_from,
                'data_sha256': canonical_sha256(data),
                'data': data,
            })
            # 與整份教材刪除共用鎖序，禁止晚到 worker 重新建立已刪除的私人資料。
            with database_session(self.dsn) as session:
                material = session.scalar(select(Material).where(
                    Material.learner_id == self.run.learner_id,
                    Material.material_id == self.run.material_id,
                ).with_for_update())
                run = session.scalar(select(MaterialProcessingRun).where(
                    MaterialProcessingRun.run_id == self.run.run_id,
                ).with_for_update())
                if (
                    material is None or material.discard_requested_at is not None or run is None
                    or run.worker_token != self.worker_token or run.status != 'running'
                ):
                    raise AnalysisArchiveError('MATERIAL_RUN_UNAVAILABLE')
                for directory in reversed([self.directory, *list(self.directory.parents)[:3]]):
                    directory.mkdir(mode=0o700, exist_ok=True)
                    details = directory.stat(follow_symlinks=False)
                    if not stat.S_ISDIR(details.st_mode) or stat.S_IMODE(details.st_mode) != 0o700:
                        raise OSError
                destination = self.directory / name
                destination.parent.mkdir(mode=0o700, exist_ok=True)
                if destination.parent.is_symlink():
                    raise OSError
                with tempfile.NamedTemporaryFile(dir=self.directory, prefix='.writing-', delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, destination)
                _sync_directory(destination.parent)
        except AnalysisArchiveError:
            raise
        except Exception:
            raise AnalysisArchiveError('ANALYSIS_ARTIFACT_WRITE_FAILED') from None
        # 寫入失敗的 .writing-* 也保留，開發診斷不能把唯一的新資料再清掉。

    def save_checkpoint(self, data):
        self._write('checkpoint.json', data)

    def save_response(self, index, request, response):
        self._write(f'call-{index:06d}/decoded.json', {'request': request, 'response': response})

    def prepare_call(self, index, request):
        self._write(f'call-{index:06d}/request.json', request)

    def save_review(self, name, data):
        self._write(f'review/{name}.json', data)

    def prepare_review_call(self, index, key, request, *, attempt=0):
        name = f'call-{index:06d}' + (f'-repair-{attempt:02d}' if attempt else '')
        self.save_review(f'{name}/request', {'cache_key': key, 'request': request})

    def load_review(self, key, *, validate_response=None):
        # 同來源／模型設定的明確重試可接續；不重播未知或損毀的回應。
        with database_session(self.dsn) as session:
            prior = session.scalars(select(MaterialProcessingRun).where(
                MaterialProcessingRun.learner_id == self.run.learner_id,
                MaterialProcessingRun.material_id == self.run.material_id,
                MaterialProcessingRun.status.in_(('failed', 'cancelled')),
                MaterialProcessingRun.created_at < self.run.created_at).order_by(MaterialProcessingRun.created_at.desc())).all()
            candidates = [(r.run_id, _checkpoint_input_digest(r)) for r in prior if _can_reuse_analysis(r, self.run)]
        for run_id, signature in [(self.run.run_id, self.signature), *candidates]:
            directory = self.directory.parent / run_id.hex / 'review'
            path = directory / f'cache-{key}.json'
            try:
                if path.exists():
                    response = _read_envelope(path, run_id, signature)['data']
                    reuse_kind = 'saved_response'
                else:
                    # 驗證器修正後，可以重新核對原始回應；不用為同一輸入再付一次推論費用。
                    if validate_response is None:
                        continue
                    for request_path in sorted(directory.glob('call-*/request.json'), reverse=True):
                        request = _read_envelope(request_path, run_id, signature)
                        if request['data'].get('cache_key') != key:
                            continue
                        candidate_path = request_path.parent / 'response.json'
                        if not candidate_path.exists():
                            continue
                        response = _read_envelope(candidate_path, run_id, signature)['data']
                        try:
                            validate_response(response)
                        except ValueError:
                            continue
                        reuse_kind = 'revalidated_response'
                        break
                    else:
                        continue
                self.review_reuses.append({'run_id': str(run_id), 'cache_key': key, 'kind': reuse_kind})
                return response
            except Exception:
                raise AnalysisArchiveError('ANALYSIS_CHECKPOINT_INVALID') from None
        return None

    def save_failure(self, error):
        # 不保存 exception message／locals，避免把 DSN 或私人答案寫入一般診斷。
        self._write('failure.json', {
            'exception_type': type(error).__name__,
            'frames': [
                {'file': Path(frame.filename).name, 'function': frame.name, 'line': frame.lineno}
                for frame in traceback.extract_tb(error.__traceback__)
            ],
        })


def _checkpoint_directory(owner, material, run_id):
    directory = _material_directory(owner, material) / run_id.hex
    if any(path.is_symlink() for path in [directory, *list(directory.parents)[:3]]):
        raise AnalysisArchiveError('ANALYSIS_CHECKPOINT_CLEANUP_FAILED')
    return directory


def cleanup_published_checkpoints(owner, material_id, run_id, *, dsn):
    """只以已 commit 的發布結果判斷；needs_review 與目前 head 不影響清理。"""
    try:
        with database_session(dsn) as session:
            material = session.scalar(select(Material).where(
                Material.learner_id == owner, Material.material_id == material_id).with_for_update())
            run = session.scalar(select(MaterialProcessingRun).where(
                MaterialProcessingRun.learner_id == owner, MaterialProcessingRun.material_id == material_id,
                MaterialProcessingRun.run_id == run_id).with_for_update())
            if (material is None or material.discard_requested_at is not None or run is None
                or run.status not in {'succeeded', 'partial'} or run.progress_stage != 'completed'
                or run.completed_at is None or not isinstance(run.output_binding, dict)
                or run.output_binding.get('processing') != run.status
                or not isinstance(run.output_binding.get('knowledge_structure_revision'), str)):
                return False
            directory = _checkpoint_directory(owner, material_id, run_id)
            checkpoint = directory / 'checkpoint.json'
            if checkpoint.is_symlink():
                raise AnalysisArchiveError('ANALYSIS_CHECKPOINT_CLEANUP_FAILED')
            if not checkpoint.exists():
                return False
            # 完整狀態刪除後仍能追查沿用哪次失敗分析，避免把舊呼叫量冒稱本次新推論。
            receipt = {'run_id': str(run_id),
                       'knowledge_structure_revision': run.output_binding['knowledge_structure_revision']}
            try:
                saved = _read_envelope(checkpoint, run_id, _checkpoint_input_digest(run))
                receipt['reused_from_run'] = saved['reused_from_run']
            except (ValueError, KeyError, TypeError):
                # 發布已由 DB 確認；損毀的恢復狀態不再有用途，也不能反過來卡住清理。
                _logger.warning('ANALYSIS_CHECKPOINT_METADATA_UNAVAILABLE', extra={'run_id': str(run_id)})
            with tempfile.NamedTemporaryFile(dir=directory, prefix='.writing-', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(canonical_bytes(receipt)); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, directory / 'completion.json')
            _sync_directory(directory)

            prior = session.scalars(select(MaterialProcessingRun).where(
                MaterialProcessingRun.learner_id == owner, MaterialProcessingRun.material_id == material_id,
                MaterialProcessingRun.status == 'failed', MaterialProcessingRun.created_at < run.created_at))
            for failed in prior:
                if not _can_reuse_analysis(failed, run):
                    continue
                previous = _checkpoint_directory(owner, material_id, failed.run_id)
                path = previous / 'checkpoint.json'
                if path.exists() or path.is_symlink():
                    path.unlink()
                    _sync_directory(previous)
            # 最後才移除成功 run 的 checkpoint；中途清理失敗時，reconciliation 還找得到它。
            checkpoint.unlink()
            _sync_directory(directory)
            return True
    except AnalysisArchiveError:
        raise
    except Exception:
        raise AnalysisArchiveError('ANALYSIS_CHECKPOINT_CLEANUP_FAILED') from None


def reconcile_published_checkpoints(*, dsn):
    """補完發布 commit 後崩潰或檔案清理失敗；未完成／失敗作業仍保留。"""
    for path in (_root() / 'analysis').glob('*/*/*/checkpoint.json'):
        try:
            owner, material, run = (UUID(hex=path.parents[index].name) for index in (2, 1, 0))
        except ValueError:
            continue
        try:
            cleanup_published_checkpoints(owner, material, run, dsn=dsn)
        except AnalysisArchiveError:
            _logger.warning('ANALYSIS_CHECKPOINT_CLEANUP_FAILED', extra={'run_id': str(run)})


def remove_material_analysis(owner,material):
    path=_material_directory(owner,material)
    if not path.exists():return
    shutil.rmtree(path)


def has_analysis_checkpoint(owner,material,run):
    return (_material_directory(owner,material)/run.hex/'checkpoint.json').is_file()


def reconcile_removed_material_analysis(*,dsn):
    """補完使用者刪除教材已 commit、檔案清理卻中斷的情況；現存教材一律保留。"""
    for directory in (_root()/'analysis').glob('*/*'):
        try:owner,material=UUID(hex=directory.parent.name),UUID(hex=directory.name)
        except ValueError:continue
        with database_session(dsn) as session:
            exists=session.scalar(select(Material.material_id).where(Material.learner_id==owner,
                Material.material_id==material).with_for_update())
            if exists is None:remove_material_analysis(owner,material)
