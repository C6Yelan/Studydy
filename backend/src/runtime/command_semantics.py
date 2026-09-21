"""開發用命令 transport；執行器與模型從 private 環境設定載入，不固定供應商。"""
from __future__ import annotations
from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
from uuid import uuid4
from pdf_evidence.ocr_page_evidence import canonical_sha256

class CommandSemanticError(RuntimeError):pass

_call_output_directory = ContextVar('semantic_call_output_directory', default=None)

@contextmanager
def retain_call_outputs(directory):
    token=_call_output_directory.set(directory)
    try:yield
    finally:_call_output_directory.reset(token)

def settings():
    path=os.environ.get('STUDYDY_SEMANTIC_COMMAND_CONFIG')
    if path is None:return None
    try:
        value=json.loads(Path(path).read_text())
        if set(value)!={'schema','argv','model_id','model_revision'} or value['schema']!='semantic-command-config/v1':raise ValueError
        if not isinstance(value['argv'],list) or not value['argv'] or not all(isinstance(a,str) and a and '\x00' not in a for a in value['argv']):raise ValueError
        if not Path(value['argv'][0]).is_absolute() or not os.access(value['argv'][0],os.X_OK):raise ValueError
        if not all(isinstance(value[k],str) and 0<len(value[k])<200 for k in ('model_id','model_revision')):raise ValueError
        return value
    except (OSError,ValueError,TypeError,KeyError):raise CommandSemanticError('SEMANTIC_SERVICE_CONFIG_INVALID') from None

def identity(lock,config=None):
    config=settings() if config is None else config
    if config is None:return None
    public={'transport':'command','model_id':config['model_id'],'model_revision':config['model_revision'],
            'config_sha256':canonical_sha256(config)}
    return {**public,'runtime_lock_sha256':canonical_sha256({'base_runtime_lock':lock,'execution':public})}

def _unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError("DUPLICATE_JSON_KEY")
        result[key]=value
    return result

def _reject_constant(value):raise ValueError("NONFINITE_JSON")

def encode_payload(prompt, document, schema):
    """執行器的 UTF-8 stdin 封包，包含 prompt 與輸出 schema。"""
    return json.dumps({'instructions': prompt, 'input': document, 'response_schema': schema}, ensure_ascii=False).encode()


def request(prompt,document,schema):
    config=settings()
    if config is None:raise CommandSemanticError('SEMANTIC_SERVICE_CONFIG_INVALID')
    payload=encode_payload(prompt,document,schema)
    # 保留原本與專案隔離的執行目錄，避免執行器額外載入專案上下文。
    # 工作目錄與保存目錄都不自動清除；教材刪除可依 nonce 核對後清理。
    destination=_call_output_directory.get()
    if destination is None:
        destination=Path(tempfile.mkdtemp(prefix='studydy-semantic-',dir=Path(os.environ['STUDYDY_SEMANTIC_COMMAND_CONFIG']).resolve().parent))
    root=Path(tempfile.mkdtemp(prefix='studydy-semantic-'))
    try:
        nonce=uuid4().hex
        (root/'.archive-owner').write_text(nonce)
        (destination/'working-directory.json').write_text(json.dumps({'path':str(root),'nonce':nonce}))
        schema_path=root/'schema.json';output=root/'response.json'
        (root/'input.json').write_bytes(payload)
        schema_path.write_text(json.dumps(schema))
        argv=[a.replace('{schema}',str(schema_path)).replace('{output}',str(output)).replace('{model}',config['model_id']).replace('{workdir}',str(root)) for a in config['argv']]
        # 外部語意執行器不繼承產品 DSN、artifact 路徑或另一個模型服務的憑證。
        environment={name:value for name,value in os.environ.items() if not name.startswith("STUDYDY_") and name!="VLLM_API_KEY"}
        with os.fdopen(os.open(root/'stdout.txt',os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600),'wb') as stdout, \
             os.fdopen(os.open(root/'stderr.txt',os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600),'wb') as stderr:
            process=subprocess.Popen(argv,env=environment,stdin=subprocess.PIPE,stdout=stdout,stderr=stderr,cwd=root,start_new_session=True)
            process.communicate(payload)
        (root/'execution.json').write_text(json.dumps({'return_code':process.returncode}))
        if process.returncode:raise CommandSemanticError('SEMANTIC_SERVICE_UNAVAILABLE')
        try:
            data=output.read_bytes()
            if not data:raise ValueError
            result=json.loads(data,object_pairs_hook=_unique_object,parse_constant=_reject_constant)
            if not isinstance(result,dict):raise ValueError
            return result
        except (OSError,ValueError):raise CommandSemanticError('SEMANTIC_RESPONSE_INVALID') from None
    except OSError:raise CommandSemanticError('SEMANTIC_ARTIFACT_WRITE_FAILED') from None
    finally:
        # 即使 exit 非零或 JSON 無效，也先保留原始輸出；明確刪除教材後不重建目錄。
        if destination.is_dir():
            try:
                for name in ('input.json','schema.json','response.json','stdout.txt','stderr.txt','execution.json'):
                    source=root/name
                    if source.is_file():
                        shutil.copyfile(source,destination/name)
                        (destination/name).chmod(0o600)
            except OSError:raise CommandSemanticError('SEMANTIC_ARTIFACT_WRITE_FAILED') from None
