"""Podcast 短集名：受控模型回應；不呼叫真實模型。"""
from product_fixtures import closed_loop
from runtime import podcast_titles, podcasts
from runtime.storage.database import database_session
from runtime.storage.tables import Podcast
from test_podcasts import create, script, wav


def test_title_validation_rejects_vague_or_oversized_names():
    assert podcast_titles.validate({"title": "  有依據的回答  "}) == "有依據的回答"
    for bad in ({"title": "第一集"}, {"title": "本集重點"}, {"title": "x"}, {"title": "長" * 19},
                {"title": "兩行\n標題"}, {"title": 3}, {}, None):
        assert podcast_titles.validate(bad) is None


def test_new_scripts_get_a_title_and_failures_keep_the_concept_label(closed_loop):
    owner, _, _, _, dsn, _ = closed_loop
    identity = create(closed_loop)["podcast_id"]
    claim = podcasts.claim_step(dsn=dsn)
    saved = script(claim)
    sent = []

    def answer(request):
        sent.append(request)
        return {"title": "有依據的回答"}

    title = podcast_titles.generate({**claim["episode"], "script": saved}, answer)
    assert title == "有依據的回答"
    assert set(sent[0]) == {"segments", "concepts"} and sent[0]["concepts"]
    assert podcasts.finish_step(claim, script=saved, title=title, dsn=dsn)
    audio = podcasts.claim_step(dsn=dsn)
    assert audio["episode"]["title"] == "有依據的回答"
    assert podcasts.finish_step(audio, audio=wav(), audio_provider="synthetic-test", dsn=dsn)
    view = podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)
    assert view["episodes"][0]["title"] == "有依據的回答"

    def broken(_):
        raise RuntimeError("provider down")

    assert podcast_titles.generate({**claim["episode"], "script": saved}, broken) is None
    assert podcast_titles.generate({**claim["episode"], "script": None}, answer) is None


def test_backfill_names_only_untitled_episodes_once(closed_loop):
    owner, _, _, _, dsn, _ = closed_loop
    identity = create(closed_loop)["podcast_id"]
    claim = podcasts.claim_step(dsn=dsn)
    podcasts.finish_step(claim, script=script(claim), dsn=dsn)
    podcasts.finish_step(podcasts.claim_step(dsn=dsn), audio=wav(), audio_provider="synthetic-test", dsn=dsn)
    calls = []

    def answer(request):
        calls.append(request)
        return {"title": "補上的集名"}

    assert podcast_titles.fill_missing(dsn=dsn, call=answer) == (1, 0)
    assert podcast_titles.fill_missing(dsn=dsn, call=answer) == (0, 0)
    assert len(calls) == 1
    with database_session(dsn) as db:
        episode = db.get(Podcast, identity).episodes[0]
    assert episode["title"] == "補上的集名" and episode["audio"] and episode["script"]


def test_title_request_uses_the_luna_text_channel(closed_loop, monkeypatch):
    import json
    from pathlib import Path

    from runtime import knowledge_chapters, provider_http
    from runtime.luna_test import test_lock

    _, _, _, _, dsn, _ = closed_loop
    create(closed_loop)
    claim = podcasts.claim_step(dsn=dsn)
    lock = json.loads((Path(__file__).parents[4] / "local_ai/runtime-lock.json").read_text())
    monkeypatch.setattr(knowledge_chapters, "configured_lock", lambda: test_lock(lock))
    sent = []

    def provider(path, body):
        sent.append((path, body))
        return {"title": "有依據的回答"}

    monkeypatch.setattr(provider_http, "_host", provider)
    assert podcast_titles.generate({**claim["episode"], "script": script(claim)}) == "有依據的回答"
    path, body = sent[0]
    assert path == "/semantics" and body["task"] == "material_review" and body["prompt"] == podcast_titles.PROMPT
    assert set(body["request"]) == {"segments", "concepts"}
