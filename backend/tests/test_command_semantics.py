"""Command adapter contract；所有子程序都是本機合成工具，不呼叫真實模型。"""
import json
from pathlib import Path
import sys
import pytest
from runtime import command_semantics as command


def config(tmp_path,monkeypatch,script,*,timeout=2,max_calls=2):
    child=tmp_path/'child.py';child.write_text(script)
    value={'schema':'semantic-command-config/v1','argv':[sys.executable,str(child),'{output}','{schema}'],'model_id':'synthetic-model',
           'model_revision':'fixture-v1','timeout_seconds':timeout,'max_input_bytes':4096,'max_calls':max_calls}
    path=tmp_path/'config.json';path.write_text(json.dumps(value));monkeypatch.setenv('STUDYDY_SEMANTIC_COMMAND_CONFIG',str(path));monkeypatch.setattr(command,'_calls',0)
    return value


def test_command_is_configurable_private_and_truthfully_identified(tmp_path,monkeypatch):
    monkeypatch.setenv('STUDYDY_DATABASE_DSN','synthetic-do-not-inherit')
    monkeypatch.setenv('VLLM_API_KEY','synthetic-do-not-inherit')
    value=config(tmp_path,monkeypatch,'import sys,json,os\nassert "STUDYDY_DATABASE_DSN" not in os.environ and "VLLM_API_KEY" not in os.environ\nfrom pathlib import Path\npayload=json.load(sys.stdin)\nassert "instructions" in payload\nPath(sys.argv[1]).write_text(json.dumps({"ok": True}))')
    identity=command.identity({'policy':'synthetic'},value)
    assert identity['model_id']=='synthetic-model' and identity['transport']=='command'
    assert 'argv' not in identity
    assert command.request('Return a JSON object',{}, {'type':'object'})=={'ok':True}
    assert command._calls==1


def test_command_failures_and_budget_never_fallback(tmp_path,monkeypatch):
    config(tmp_path,monkeypatch,'raise SystemExit(7)',max_calls=1)
    with pytest.raises(command.CommandSemanticError,match='SEMANTIC_SERVICE_UNAVAILABLE'):command.request('x',{}, {})
    with pytest.raises(command.CommandSemanticError,match='SEMANTIC_BUDGET_EXHAUSTED'):command.request('x',{}, {})


def test_command_timeout_and_invalid_output(tmp_path,monkeypatch):
    config(tmp_path,monkeypatch,'import time; time.sleep(10)',timeout=1)
    with pytest.raises(command.CommandSemanticError,match='SEMANTIC_SERVICE_TIMEOUT'):command.request('x',{}, {})
    config(tmp_path,monkeypatch,'import sys; from pathlib import Path; Path(sys.argv[1]).write_text("not json")')
    with pytest.raises(command.CommandSemanticError,match='SEMANTIC_RESPONSE_INVALID'):command.request('x',{}, {})


def test_material_sizing_matches_complete_command_payload(tmp_path, monkeypatch):
    from runtime.semantic_service import material_request_fits
    from knowledge_map.structure import semantic_response_schema
    value = config(tmp_path, monkeypatch, 'raise AssertionError("must not start executor")')
    document = {"sections": [{"evidence": [[0, 1, "paragraph", "教材內容" * 2000]]}], "catalog": ["既有概念" * 100]}
    lock = {"material_semantics": {"prompt": "依據教材分析" * 100}}
    schema = semantic_response_schema([0])
    payload_size = len(command.encode_payload(lock["material_semantics"]["prompt"], document, schema))
    assert payload_size > 6000
    path = tmp_path / 'config.json'
    value['max_input_bytes'] = payload_size
    path.write_text(json.dumps(value))
    assert material_request_fits(None, lock, document)
    value['max_input_bytes'] = payload_size - 1
    path.write_text(json.dumps(value))
    assert not material_request_fits(None, lock, document)
    with pytest.raises(command.CommandSemanticError, match='SEMANTIC_INPUT_TOO_LARGE'):
        command.request(lock['material_semantics']['prompt'], document, schema)
    assert command._calls == 0
