"""Podcast 影片 provider：先對齊切頁交後端生成分鏡，再串行渲染；工作檔離開即清除。"""

import base64
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "backend/src"))
from runtime import podcast_process
from runtime.podcast_video_plan import validate_cues, merge_cue_groups, POLICY
from runtime.podcast_video_render import ensure_space
from runtime.podcast_script import script_input


def _check(cancelled):
    podcast_process.check(cancelled, "VIDEO_CANCELLED")


def _process(args, env, timeout, cancelled):
    try:
        return podcast_process.run(
            args,
            env=env,
            timeout=timeout,
            cancelled=cancelled,
            cancel_code="VIDEO_CANCELLED",
        ).returncode
    except subprocess.TimeoutExpired:
        raise ValueError("VIDEO_RENDER_FAILED") from None


def _audio(body):
    raw = base64.b64decode(body["audio"], validate=True)
    if (
        not 0 < len(raw) <= 100 * 1024 * 1024
        or sha256(raw).hexdigest() != body["episode"]["audio"]["sha256"]
    ):
        raise ValueError("VIDEO_SOURCE_CHANGED")
    return raw


def prepare(body, *, align, asr_lock, cancelled):
    """對齊音訊、依原稿段落切頁，回傳後端生成分鏡所需的輸入；不呼叫文字模型。"""
    _check(cancelled)
    episode = body["episode"]
    _audio(body)
    from runtime.podcast_video_plan import source_turns
    from runtime.podcast_cues import teaching_cues

    turns = source_turns(episode)
    split = {"cues": teaching_cues(episode)}
    texts = validate_cues(episode, split["cues"])
    with podcast_process.locked(asr_lock, cancelled, timeout=None):
        _check(cancelled)
        alignment = align(
            {
                "audio": body["audio"],
                "texts": texts,
                "turn_indices": [
                    [p["turn_index"] for p in c["parts"]] for c in split["cues"]
                ],
                "caption_turns": [t["text"] for t in turns],
            },
            max_texts=80,
            cancelled=cancelled,
        )
    if "groups" in alignment:
        split["cues"] = merge_cue_groups(
            episode, split["cues"], alignment.pop("groups")
        )
        texts = validate_cues(episode, split["cues"])
    # 以原稿段落歸屬分頁；ASR 合併跨段 cue 時沿用該 cue 的首個段落，不猜詞內時間。
    turn_beats = [
        i
        for i, beat in enumerate(episode["script"]["segments"])
        for _ in beat["turns"]
    ]
    page_ranges = []
    for i, cue in enumerate(split["cues"]):
        beat = turn_beats[cue["parts"][0]["turn_index"]]
        if not page_ranges or beat != page_ranges[-1][0]:
            page_ranges.append([beat, i, i])
        else:
            page_ranges[-1][2] = i
    source = {
        "sections": [
            {
                "title": split["cues"][start]["title"],
                "narration": "".join(texts[start : end + 1]),
            }
            for _, start, end in page_ranges
        ],
        **script_input(episode["claims"], body["source_context"]),
    }
    return {
        "cues": split["cues"],
        "alignment": alignment,
        "page_ranges": [[start, end] for _, start, end in page_ranges],
        "source": source,
    }


def render(body, *, cancelled):
    """以後端生成的分鏡驗證版面並渲染；分鏡已含各段 start_cue／end_cue。"""
    _check(cancelled)
    raw = _audio(body)
    python = os.environ.get("STUDYDY_VIDEO_PYTHON", "")
    work = Path(
        os.environ.get(
            "STUDYDY_VIDEO_WORK_DIR", str(PROJECT / "data/podcast/video-work")
        )
    )
    if not python or not Path(python).is_file():
        raise ValueError("VIDEO_PROVIDER_UNAVAILABLE")
    work.mkdir(parents=True, exist_ok=True)
    ensure_space(work)
    render_source = {
        key: body[key]
        for key in ("podcast_id", "episode_index", "episode", "source_resolver")
    }
    cues, alignment = body["cues"], body["alignment"]
    with tempfile.TemporaryDirectory(prefix="episode-", dir=work) as directory:
        directory = Path(directory)
        audio = directory / "audio.wav"
        request = directory / "request.json"
        result = directory / "result.json"
        audio.write_bytes(raw)
        env = {
            k: v for k, v in os.environ.items() if k != "STUDYDY_PODCAST_PROVIDER_TOKEN"
        }
        env["PYTHONPATH"] = str(PROJECT / "backend/src")
        args = [
            python,
            "-m",
            "runtime.podcast_video_render",
            str(request),
            str(audio),
            str(result),
        ]
        # 換頁、節點 anchor 與 key_terms 由 compile_layout 對講稿逐字核對；對不到只退回，不整份失敗。
        request.write_text(
            json.dumps(
                {
                    **render_source,
                    "cues": cues,
                    "alignment": alignment,
                    "semantic_plan": body["semantic_plan"],
                },
                ensure_ascii=False,
            )
        )
        checked = _process(args + ["--validate"], env, None, cancelled)
        if checked:
            code = (
                json.loads(result.read_text()).get("error", "VIDEO_LAYOUT_INVALID")
                if result.is_file()
                else "VIDEO_LAYOUT_INVALID"
            )
            raise ValueError(code.split(":", 1)[0])
        plan = json.loads(result.read_text())["plan"]
        request.write_text(
            json.dumps(
                {
                    **render_source,
                    "cues": cues,
                    "alignment": alignment,
                    "plan": plan,
                },
                ensure_ascii=False,
            )
        )
        rendered = _process(args, env, None, cancelled)
        if rendered:
            code = (
                json.loads(result.read_text()).get("error", "VIDEO_RENDER_FAILED")
                if result.is_file()
                else "VIDEO_RENDER_FAILED"
            )
            raise ValueError(code.split(":", 1)[0])
        metadata = json.loads(result.read_text())
        data = result.with_suffix(".mp4").read_bytes()
        if len(data) > 100 * 1024 * 1024:
            raise ValueError("VIDEO_TOO_LARGE")
        return {
            "policy": POLICY,
            "cues": cues,
            "alignment": alignment,
            "plan": plan,
            "render": metadata,
            "video": base64.b64encode(data).decode(),
        }
