"""驗證題組的品質提示、歷史題目比較與完全相同題目防重。"""

from copy import deepcopy

from learning_adaptation import assessment_sets as sets
from learning_adaptation.answer_events import read_answer_events
from assessment_fixtures import answer, concept_fixture, create, model_for, read
from product_fixtures import closed_loop


def test_review_flag_keeps_safe_question_with_quality_issues_and_rejects_duplicate(closed_loop):
    fixture = concept_fixture(closed_loop, 2, source_review_required=True)
    set_id = create(fixture)
    base_model = model_for(fixture)
    model_calls = []

    def model(client, **kwargs):
        model_calls.append(deepcopy(kwargs))
        response = base_model(client, **kwargs)
        if kwargs["task"] == "assessment_check":
            for verdict in response["verdicts"]:
                verdict["quality_issues"] = ["weak_distractors", "uneven_options"]
                if kwargs["request"]["prior_questions"]:
                    verdict["duplicate_prior_index"] = 0
        return response

    while work := sets.claim_set_work(dsn=fixture["dsn"]):
        sets.execute_set_work(work, dsn=fixture["dsn"], semantic_call=model)

    group = read(fixture, set_id)
    assert group["status"] == "partial_ready"
    assert group["verified_count"] == 1
    assert read_answer_events(
        fixture["learner"], fixture["study"].study_session_id, dsn=fixture["dsn"],
    ) == ()
    assert any(call["request"].get("prior_questions") for call in model_calls)


def test_bounded_prior_context_still_rejects_exact_duplicate_outside_context(closed_loop):
    fixture = concept_fixture(closed_loop, 1)
    published_prompts = []
    model_calls = []

    def publish(prompt):
        set_id = create(fixture, key=f"round-{len(published_prompts)}")
        base_model = model_for(fixture)
        original_prompt_by_generated_prompt = {}

        def model(client, **kwargs):
            model_calls.append(deepcopy(kwargs))
            if kwargs["task"] == "assessment":
                response = base_model(client, **kwargs)
                for candidate in response["candidates"]:
                    original_prompt_by_generated_prompt[prompt] = candidate["prompt"]
                    candidate["prompt"] = prompt
                return response

            # 基礎模型用原題幹查答案；測試交給 checker 的仍是新題幹。
            request = deepcopy(kwargs["request"])
            for question in request["questions"]:
                question["prompt"] = original_prompt_by_generated_prompt[question["prompt"]]
            return base_model(client, **{**kwargs, "request": request})

        while work := sets.claim_set_work(dsn=fixture["dsn"]):
            sets.execute_set_work(work, dsn=fixture["dsn"], semantic_call=model)
        return set_id, read(fixture, set_id)

    for index in range(4):
        prompt = f"情境 {index}：" + "教材內容" * 500 + " Signal 0 uses which code?"
        model_calls.clear()
        set_id, group = publish(prompt)
        assert group["status"] == "ready"

        request = next(call["request"] for call in model_calls if call["task"] == "assessment")
        assert len(request["prior_questions"]) <= 1
        if published_prompts:
            assert request["prior_questions"][0]["prompt"] == published_prompts[-1]

        published_prompts.append(prompt)
        answer(fixture, set_id)

    model_calls.clear()
    _, group = publish(published_prompts[0])
    assert group["status"] == "failed"
    assert group["verified_count"] == 0
    assert all(call["task"] == "assessment" for call in model_calls)
