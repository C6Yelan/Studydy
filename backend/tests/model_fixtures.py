"""Gemma HTTP 邊界的受控回應；保留正式 tokenizer、預算與 JSON 解析流程。"""
import json as json_module
import httpx


def model_transport(monkeypatch, respond):
    def post(client, url, *, json: dict, cancellation_check=None):
        if cancellation_check:
            cancellation_check()
        if url.endswith('/tokenize'):
            value = {'count': 100, 'max_model_len': 65536}
        else:
            prompt, payload = json['messages'][-1]['content'].split('\nINPUT:\n', 1)
            schema = json['response_format']['json_schema']
            result = respond('/v1/chat/completions', {
                'prompt': prompt, 'request': json_module.loads(payload),
                'schema': schema['schema'], 'task': schema['name'],
            })
            value = {'choices': [{'finish_reason': 'stop', 'message': {'content': json_module.dumps(result)}}]}
        return httpx.Response(200, json=value, request=httpx.Request('POST', url))
    monkeypatch.setattr('runtime.semantic_service._post', post)
