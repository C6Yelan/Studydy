"""保存每批模型回應與可續接狀態；只隨使用者明確刪除教材而清理。"""
from pathlib import Path
import json
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


class AnalysisArchiveError(RuntimeError):
    pass


def _signature(run):
    return canonical_sha256({
        'source_artifact_id':str(run.source_artifact_id),
        'source_set_id':str(run.input_source_set_id),
        'base_revision':run.base_revision,
        'runtime_binding':run.runtime_binding,
    })


def _material_directory(owner,material):
    return _root()/'analysis'/owner.hex/material.hex


class AnalysisArchive:
    def __init__(self,claim,*,dsn):
        self.run=claim.run
        self.worker_token=claim.worker_token
        self.dsn=dsn
        self.directory=_material_directory(self.run.learner_id,self.run.material_id)/self.run.run_id.hex
        self.signature=_signature(self.run)
        self.reused_from=None
        self._loaded=False
        self._checkpoint=None
        self._review_response_runs=[]
        self._replayed_responses=set()

    def load_checkpoint(self):
        if self._loaded:return self._checkpoint
        with database_session(self.dsn) as session:
            prior=session.scalars(select(MaterialProcessingRun).where(
                MaterialProcessingRun.learner_id==self.run.learner_id,
                MaterialProcessingRun.material_id==self.run.material_id,
                MaterialProcessingRun.status=='failed',
                MaterialProcessingRun.created_at<self.run.created_at,
            ).order_by(MaterialProcessingRun.created_at.desc())).all()
            candidates=[(row.run_id,row.error_code) for row in prior if _signature(row)==self.signature]
        self._review_response_runs=[run_id for run_id,error_code in candidates if error_code=='SOURCE_UPDATE_NEEDS_REVIEW']
        for run_id,error_code in candidates:
            path=self.directory.parent/run_id.hex/'checkpoint.json'
            if not path.exists():continue
            try:
                saved=json.loads(path.read_bytes())
                if (saved['signature']!=self.signature or saved['run_id']!=str(run_id)
                    or canonical_sha256(saved['data'])!=saved['data_sha256']):raise ValueError
                self.reused_from=str(run_id)
                self._checkpoint=saved['data'];self._loaded=True
                if error_code in {'NO_CANONICAL_CONCEPT','NO_USABLE_ADDED_CONTENT'}:
                    # 沒有可用語意結果時保留 Evidence，重試語意，不能永遠重組同一個空結果。
                    self._checkpoint['restart_semantics']=True
                    self._checkpoint['complete']=False
                return self._checkpoint
            except (OSError,ValueError,KeyError,TypeError):
                raise AnalysisArchiveError('ANALYSIS_CHECKPOINT_INVALID') from None
        self._loaded=True
        return None

    def reuse_review_response(self,request):
        """舊流程僅因複核旗標拒絕的回應，可在同一輸入上重用，不再付一次模型成本。"""
        if self._checkpoint is None or self._checkpoint.get('restart_semantics'):return None
        for run_id in self._review_response_runs:
            for path in sorted((self.directory.parent/run_id.hex).glob('call-*/decoded.json'),reverse=True):
                if path in self._replayed_responses:continue
                try:
                    saved=json.loads(path.read_bytes());data=saved['data']
                    if (saved['signature']!=self.signature or saved['run_id']!=str(run_id)
                        or canonical_sha256(data)!=saved['data_sha256']):raise ValueError
                    # checkpoint 的 JSON 會排序字典 key；catalog 的陣列順序因此可能改變。
                    # 概念以 k 識別，比對完整內容，但不把 catalog 排序誤當輸入改變。
                    original_request={**data['request'],'existing_concepts':sorted(data['request'].get('existing_concepts',[]),key=lambda item:item['k'])}
                    current_request={**request,'existing_concepts':sorted(request.get('existing_concepts',[]),key=lambda item:item['k'])}
                    if original_request==current_request and data['response'].get('review_required') is True:
                        self._replayed_responses.add(path)
                        return data['response']
                except (OSError,ValueError,KeyError,TypeError):
                    raise AnalysisArchiveError('ANALYSIS_CHECKPOINT_INVALID') from None
        return None

    def _write(self,name,data):
        try:
            # 與整份教材刪除共用鎖序，禁止晚到 worker 重新建立已刪除的私人資料。
            with database_session(self.dsn) as session:
                material=session.scalar(select(Material).where(Material.learner_id==self.run.learner_id,
                    Material.material_id==self.run.material_id).with_for_update())
                run=session.scalar(select(MaterialProcessingRun).where(MaterialProcessingRun.run_id==self.run.run_id).with_for_update())
                if (material is None or material.discard_requested_at is not None or run is None
                    or run.worker_token!=self.worker_token):
                    raise AnalysisArchiveError('MATERIAL_RUN_UNAVAILABLE')
                for directory in reversed([self.directory,*list(self.directory.parents)[:3]]):
                    directory.mkdir(mode=0o700,exist_ok=True)
                    details=directory.stat(follow_symlinks=False)
                    if not stat.S_ISDIR(details.st_mode) or stat.S_IMODE(details.st_mode)!=0o700:raise OSError
                destination=self.directory/name
                destination.parent.mkdir(mode=0o700,exist_ok=True)
                if destination.parent.is_symlink():raise OSError
                envelope={'run_id':str(self.run.run_id),'signature':self.signature,'reused_from_run':self.reused_from,
                          'data_sha256':canonical_sha256(data),'data':data}
                with tempfile.NamedTemporaryFile(dir=self.directory,prefix='.writing-',delete=False) as stream:
                    temporary=Path(stream.name)
                    stream.write(canonical_bytes(envelope));stream.flush();os.fsync(stream.fileno())
                os.replace(temporary,destination)
                _sync_directory(destination.parent)
        except AnalysisArchiveError:raise
        except Exception:raise AnalysisArchiveError('ANALYSIS_ARTIFACT_WRITE_FAILED') from None
        # 寫入失敗的 .writing-* 也保留，開發診斷不能把唯一的新資料再清掉。

    def save_checkpoint(self,data):
        self._write('checkpoint.json',data)

    def save_response(self,index,request,response):
        self._write(f'call-{index:06d}/decoded.json',{'request':request,'response':response})

    def prepare_call(self,index,request):
        self._write(f'call-{index:06d}/request.json',request)
        return self.directory/f'call-{index:06d}'

    def save_failure(self,error):
        # 不保存 exception message／locals，避免把 DSN 或私人答案寫入一般診斷。
        self._write('failure.json',{'exception_type':type(error).__name__,
            'frames':[{'file':Path(frame.filename).name,'function':frame.name,'line':frame.lineno}
                      for frame in traceback.extract_tb(error.__traceback__)]})


def remove_material_analysis(owner,material):
    path=_material_directory(owner,material)
    if not path.exists():return
    for record in path.glob('*/call-*/working-directory.json'):
        value=json.loads(record.read_bytes())
        working=Path(value['path'])
        if not working.exists():continue
        if (working.parent!=Path(tempfile.gettempdir()) or not working.name.startswith('studydy-semantic-')
            or working.is_symlink() or (working/'.archive-owner').read_text()!=value['nonce']):
            raise AnalysisArchiveError('ANALYSIS_CHECKPOINT_INVALID')
        shutil.rmtree(working)
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
