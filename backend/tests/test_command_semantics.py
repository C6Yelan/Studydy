"""Command adapter contract；所有子程序都是本機合成工具，不呼叫真實模型。"""
import json
import sys
from pathlib import Path
import pytest
from runtime import command_semantics as command


def config(tmp_path,monkeypatch,script):
    child=tmp_path/'child.py';child.write_text(script)
    value={'schema':'semantic-command-config/v1','argv':[sys.executable,str(child),'{output}','{schema}'],'model_id':'synthetic-model',
           'model_revision':'fixture-v1'}
    path=tmp_path/'config.json';path.write_text(json.dumps(value));monkeypatch.setenv('STUDYDY_SEMANTIC_COMMAND_CONFIG',str(path))
    return value


def test_command_is_configurable_private_and_truthfully_identified(tmp_path,monkeypatch):
    monkeypatch.setenv('STUDYDY_DATABASE_DSN','synthetic-do-not-inherit')
    monkeypatch.setenv('VLLM_API_KEY','synthetic-do-not-inherit')
    value=config(tmp_path,monkeypatch,'import sys,json,os\nassert "STUDYDY_DATABASE_DSN" not in os.environ and "VLLM_API_KEY" not in os.environ\nfrom pathlib import Path\npayload=json.load(sys.stdin)\nassert "instructions" in payload\nPath(sys.argv[1]).write_text(json.dumps({"ok": True}))')
    identity=command.identity({'policy':'synthetic'},value)
    assert identity['model_id']=='synthetic-model' and identity['transport']=='command'
    assert 'argv' not in identity
    assert command.request('Return a JSON object',{}, {'type':'object'})=={'ok':True}


@pytest.mark.parametrize('script,reason',[
    ('raise SystemExit(7)','SEMANTIC_SERVICE_UNAVAILABLE'),
    ('import sys; from pathlib import Path; Path(sys.argv[1]).write_text("not json")','SEMANTIC_RESPONSE_INVALID'),
    ('import sys; from pathlib import Path; Path(sys.argv[1]).write_text("[]")','SEMANTIC_RESPONSE_INVALID'),
])
def test_command_failures_never_fallback(tmp_path,monkeypatch,script,reason):
    config(tmp_path,monkeypatch,script)
    with pytest.raises(command.CommandSemanticError,match=reason):command.request('x',{}, {})
    saved=list(tmp_path.glob('studydy-semantic-*/input.json'))
    assert len(saved)==1
    assert json.loads(saved[0].read_text())['instructions']=='x'
    if reason=='SEMANTIC_RESPONSE_INVALID':assert (saved[0].parent/'response.json').is_file()


def test_private_transport_has_no_input_output_call_or_execution_cap(tmp_path,monkeypatch):
    from runtime.semantic_service import material_request_fits
    config(tmp_path,monkeypatch,
        'import sys,json\nfrom pathlib import Path\npayload=json.load(sys.stdin)\n'
        'assert len(payload["input"]["sections"][0]["evidence"][0][3]) == 70000\n'
        'Path(sys.argv[1]).write_text(json.dumps({"result":"x" * 1100000}))')
    document={'sections':[{'evidence':[[0,1,'paragraph','x'*70000]]}]}
    assert material_request_fits(None,{'material_semantics':{'prompt':'synthetic'}},document)
    actual=command.subprocess.Popen
    timeouts=[]
    def launch(*args,**kwargs):
        process=actual(*args,**kwargs)
        communicate=process.communicate
        def observed(*args,**kwargs):
            timeouts.append(kwargs.get('timeout'))
            return communicate(*args,**kwargs)
        process.communicate=observed
        return process
    monkeypatch.setattr(command.subprocess,'Popen',launch)
    for _ in range(13):
        assert len(command.request('synthetic',document,{'type':'object'})['result'])==1100000
    assert timeouts==[None]*13


def test_command_batch_target_only_counts_new_content_and_keeps_large_catalog(tmp_path,monkeypatch):
    from copy import deepcopy
    from runtime.semantic_service import material_request_fits
    config(tmp_path,monkeypatch,'raise AssertionError("sizing must not start an executor")')
    lock=json.loads((Path(__file__).parents[2]/'local_ai/runtime-lock.json').read_text())
    request={'sections':[{'evidence':[[0,1,'paragraph','原文短句。'],[1,2,'paragraph','另一段原文。']]}],
             'existing_concepts':[]}
    assert material_request_fits(None,lock,request)
    request['existing_concepts']=[{'k':'old','l':'Existing','a':[],'c':['原有概念。'*20000],'e':[0]}]
    assert material_request_fits(None,lock,request)
    large=deepcopy(request)
    large['sections'][0]['evidence'][0][3]='本次新增原文。'*2000
    assert not material_request_fits(None,lock,large)
    # 分批目標不構成單一 Evidence 或累積 catalog 的拒絕門檻。
    large['sections'][0]['evidence'].pop()
    assert material_request_fits(None,lock,large)


@pytest.mark.parametrize('invalid',[False,True])
def test_scoped_outputs_survive_execution_and_are_removed_only_by_explicit_material_delete(tmp_path,monkeypatch,invalid):
    from uuid import uuid4
    from runtime.storage.analysis_archive import _material_directory,remove_material_analysis
    import tempfile
    artifacts=tmp_path/'artifacts';artifacts.mkdir(mode=0o700)
    monkeypatch.setenv('STUDYDY_ARTIFACT_ROOT',str(artifacts))
    owner,material,run=uuid4(),uuid4(),uuid4()
    material_directory=_material_directory(owner,material)
    destination=material_directory/run.hex/'call-000001'
    for directory in reversed([destination,*list(destination.parents)[:4]]):directory.mkdir(mode=0o700,exist_ok=True)
    script='import sys;from pathlib import Path;Path(sys.argv[1]).write_text('+repr('not json' if invalid else '{"ok":true}')+')'
    config(tmp_path,monkeypatch,script)
    with command.retain_call_outputs(destination):
        if invalid:
            with pytest.raises(command.CommandSemanticError,match='SEMANTIC_RESPONSE_INVALID'):command.request('source text',{}, {})
        else:assert command.request('source text',{}, {})=={'ok':True}
    working=Path(json.loads((destination/'working-directory.json').read_text())['path'])
    assert working.parent==Path(tempfile.gettempdir())
    assert working.is_dir() and (working/'response.json').is_file()
    assert (destination/'response.json').read_bytes()==(working/'response.json').read_bytes()
    assert (destination/'input.json').is_file() and (destination/'stderr.txt').is_file()
    remove_material_analysis(owner,material)
    assert not working.exists() and not material_directory.exists()
