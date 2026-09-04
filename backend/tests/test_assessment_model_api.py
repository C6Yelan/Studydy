import json

import httpx

from learning_adaptation.assessment_model_api import request_assessment_text


def test_assessment_request_preserves_fixed_input_order_and_disables_thinking():
    observed = []

    def respond(request):
        observed.append(request)
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"count": 10, "max_model_len": 100})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "{}"},
                    }
                ]
            },
        )

    request_document = {"target_claim": "second", "evidence": "first"}
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert request_assessment_text(
            client,
            base_url="http://127.0.0.1:8000",
            model="fixed-model",
            prompt_template="fixed prompt",
            request_document=request_document,
            response_format={"type": "json_schema"},
            max_model_len=100,
            max_tokens=20,
        ) == "{}"

    body = json.loads(observed[1].content)
    assert body["messages"][0]["content"] == (
        'fixed prompt\nINPUT:\n{"target_claim":"second","evidence":"first"}'
    )
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
