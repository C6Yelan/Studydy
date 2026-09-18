"""開發用命令 transport；執行器與模型從 private 環境設定載入，不固定供應商。"""
from __future__ import annotations
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
from threading import Lock
from pdf_evidence.ocr_page_evidence import canonical_sha256

class CommandSemanticError(RuntimeError):pass

def settings():
    path=os.environ.get('STUDYDY_SEMANTIC_COMMAND_CONFIG')
    if path is None:return None
    try:
        value=json.loads(Path(path).read_text())
        if set(value)!={'schema','argv','model_id','model_revision','timeout_seconds','max_input_bytes','max_calls'} or value['schema']!='semantic-command-config/v1':raise ValueError
        if not isinstance(value['argv'],list) or not value['argv'] or not all(isinstance(a,str) and a and '\x00' not in a for a in value['argv']):raise ValueError
        if not Path(value['argv'][0]).is_absolute() or not os.access(value['argv'][0],os.X_OK):raise ValueError
        if not all(isinstance(value[k],str) and 0<len(value[k])<200 for k in ('model_id','model_revision')):raise ValueError
        if not all(type(value[k]) is int and value[k]>0 for k in ('timeout_seconds','max_input_bytes','max_calls')):raise ValueError
        return value
    except (OSError,ValueError,TypeError,KeyError):raise CommandSemanticError('SEMANTIC_SERVICE_CONFIG_INVALID') from None

def identity(lock,config=None):
    config=settings() if config is None else config
    if config is None:return None
    public={'transport':'command','model_id':config['model_id'],'model_revision':config['model_revision'],
            'config_sha256':canonical_sha256(config)}
    return {**public,'runtime_lock_sha256':canonical_sha256({'base_runtime_lock':lock,'execution':public})}

_calls=0
_budget_lock=Lock()

def _unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError("DUPLICATE_JSON_KEY")
        result[key]=value
    return result

def _reject_constant(value):raise ValueError("NONFINITE_JSON")

def encode_payload(prompt, document, schema):
    """分批檢查與實際執行使用相同 UTF-8 封包，包含 prompt 與輸出 schema。"""
    return json.dumps({'instructions': prompt, 'input': document, 'response_schema': schema}, ensure_ascii=False).encode()


def request(prompt,document,schema):
    global _calls
    config=settings()
    if config is None:raise CommandSemanticError('SEMANTIC_SERVICE_CONFIG_INVALID')
    payload=encode_payload(prompt,document,schema)
    if len(payload)>config['max_input_bytes']:raise CommandSemanticError('SEMANTIC_INPUT_TOO_LARGE')
    with _budget_lock:
        if _calls>=config['max_calls']:raise CommandSemanticError('SEMANTIC_BUDGET_EXHAUSTED')
        _calls+=1
    with tempfile.TemporaryDirectory(prefix='studydy-semantic-') as directory:
        root=Path(directory);schema_path=root/'schema.json';output=root/'response.json'
        schema_path.write_text(json.dumps(schema))
        argv=[a.replace('{schema}',str(schema_path)).replace('{output}',str(output)).replace('{model}',config['model_id']).replace('{workdir}',str(root)) for a in config['argv']]
        # 外部語意執行器不繼承產品 DSN、artifact 路徑或另一個模型服務的憑證。
        environment={name:value for name,value in os.environ.items() if not name.startswith("STUDYDY_") and name!="VLLM_API_KEY"}
        process=subprocess.Popen(argv,env=environment,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,cwd=root,start_new_session=True)
        try:process.communicate(payload,timeout=config['timeout_seconds'])
        except subprocess.TimeoutExpired:
            os.killpg(process.pid,signal.SIGKILL);process.communicate();raise CommandSemanticError('SEMANTIC_SERVICE_TIMEOUT') from None
        if process.returncode:raise CommandSemanticError('SEMANTIC_SERVICE_UNAVAILABLE')
        try:
            data=output.read_bytes()
            if not data or len(data)>1048576:raise ValueError
            result=json.loads(data,object_pairs_hook=_unique_object,parse_constant=_reject_constant)
            if not isinstance(result,dict):raise ValueError
            return result
        except (OSError,ValueError):raise CommandSemanticError('SEMANTIC_RESPONSE_INVALID') from None
