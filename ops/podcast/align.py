"""由實際音訊的 ASR 詞時間點對齊既有講稿；不以字數分配音訊時間。"""

import json
import os
from pathlib import Path
import sys
import wave


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend/src"))
from runtime.scene_alignment import (
    align_texts,
    align_cue_groups,
    normalized,
    AlignmentError,
)


def render(audio_path, texts, turn_indices=None, caption_turns=None):
    from faster_whisper import WhisperModel

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from transcribe import decode_audio

    with wave.open(str(audio_path), "rb") as wav:
        duration = wav.getnframes() / wav.getframerate()
        if (
            wav.getnchannels() != 1
            or wav.getframerate() != 24000
            or not 0.5 <= duration <= 1800
        ):
            raise ValueError("SCENE_ALIGNMENT_FAILED")
    model = WhisperModel(
        os.environ["STUDYDY_STT_MODEL_DIR"],
        device="cpu",
        compute_type="int8",
        cpu_threads=6,
        local_files_only=True,
    )
    samples = decode_audio(audio_path.read_bytes(), 1801)
    segments, _ = model.transcribe(
        samples,
        language="zh",
        beam_size=3,
        word_timestamps=True,
        vad_filter=True,
        initial_prompt="繁體中文教材講解。保留英文專有名詞、數字與否定詞。",
        condition_on_previous_text=False,
    )
    words = [
        {"word": word.word, "start": float(word.start), "end": float(word.end)}
        for s in segments
        for word in (s.words or [])
    ]
    result = align_words(texts, words, duration, turn_indices, caption_turns)
    if caption_turns is not None and "caption_alignment" not in result:
        gaps = [
            (a["end"], b["start"])
            for a, b in zip(words, words[1:])
            if b["start"] - a["end"] >= 10
        ]
        retry_words = short_window_words(model, samples, gaps)
        if gaps:
            # 只補長段辨識漏掉的區間，保留其他已成立的詞時間與上下文。
            retry_words = sorted(
                words
                + [
                    word
                    for word in retry_words
                    if any(
                        begin <= (word["start"] + word["end"]) / 2 < end
                        for begin, end in gaps
                    )
                ],
                key=lambda word: word["start"],
            )
        try:
            retry = align_words(
                texts, retry_words, duration, turn_indices, caption_turns
            )
        except AlignmentError:
            retry = {}
        except ValueError as error:
            if str(error) != "SCENE_ALIGNMENT_FAILED":
                raise
            retry = {}
        if "caption_alignment" in retry:
            result["caption_alignment"] = retry["caption_alignment"]
            result["method"] = (
                result["method"].removesuffix(";paragraph-captions/v1")
                + ";short-window-captions/v1"
            )
    repair_caption_prefixes(model, samples, result)
    metadata = (
        Path(os.environ["STUDYDY_STT_MODEL_DIR"])
        / ".cache/huggingface/download/model.bin.metadata"
    )
    revision = (
        metadata.read_text().splitlines()[0]
        if metadata.is_file()
        else "local-checkpoint"
    )
    result["producer"] = "faster-whisper:large-v3-turbo@" + revision
    if "caption_alignment" in result:
        result["caption_alignment"]["alignment"]["producer"] = result["producer"]
    return result


def repair_caption_prefixes(model, samples, result):
    """漏辨句首會讓字幕晚切；只用局部音訊重新辨識到的句首時間修正，不按字數倒推。"""
    captions = result.get("caption_alignment")
    if not captions:
        return
    alignment = captions["alignment"]
    starts = alignment["starts"]
    anchors = alignment["anchors"]
    original_starts = list(starts)
    duration = result["duration"]
    changed = False
    for index, cue in enumerate(captions["cues"]):
        if index == 0 or anchors[index]["boundary_script_offset"] == 0:
            continue
        start = max(0, original_starts[index - 1] - 1)
        end = min(
            duration,
            (original_starts[index + 1] if index + 1 < len(starts) else duration) + 1,
        )
        segments, _ = model.transcribe(
            samples[round(start * 16000) : round(end * 16000)],
            language="zh",
            beam_size=3,
            word_timestamps=True,
            vad_filter=False,
            no_speech_threshold=None,
            initial_prompt="繁體中文教材講解。保留英文專有名詞、數字與否定詞。",
            condition_on_previous_text=False,
        )
        words = [
            {
                "word": word.word,
                "start": float(word.start) + start,
                "end": min(duration, float(word.end) + start),
            }
            for segment in segments
            for word in (segment.words or [])
        ]
        text = "\n".join(part["text"] for part in cue["parts"])
        try:
            anchor = align_texts([text], words, duration)["anchors"][0]
        except AlignmentError:
            continue
        # 必須真的辨識到原句開頭，而且時間仍在相鄰字幕之間；失敗就保留原來的可回查錨點。
        if (
            anchor["script_offset"] != 0
            or anchor["boundary_script_offset"] != 0
            or not starts[index - 1] < anchor["boundary_audio_start"] < starts[index]
        ):
            continue
        starts[index] = anchor["boundary_audio_start"]
        anchors[index] = anchor
        changed = True
    if changed:
        alignment["method"] += ";caption-prefix-repair/v1"
        result["method"] += ";caption-prefix-repair/v1"


def short_window_words(model, samples, gaps=()):
    """重疊音訊提供上下文；只採用核心區間的實測詞時間，避免長段解碼漏句。"""
    rate = 16000  # decode_audio 的輸出取樣率
    duration = len(samples) / rate
    words = []
    for core_start in range(0, len(samples), 10 * rate):
        core_end = min(core_start + 10 * rate, len(samples))
        if gaps and not any(
            core_start / rate < end and core_end / rate > begin for begin, end in gaps
        ):
            continue
        start = max(0, core_start - 2 * rate)
        end = min(len(samples), core_end + 2 * rate)
        segments, _ = model.transcribe(
            samples[start:end],
            language="zh",
            beam_size=3,
            word_timestamps=True,
            vad_filter=False,
            no_speech_threshold=None,
            initial_prompt="繁體中文教材講解。保留英文專有名詞、數字與否定詞。",
            condition_on_previous_text=False,
        )
        for segment in segments:
            for word in segment.words or []:
                begin = float(word.start) + start / rate
                finish = min(float(word.end) + start / rate, duration)
                if core_start / rate <= (begin + finish) / 2 < core_end / rate:
                    words.append({"word": word.word, "start": begin, "end": finish})
    return sorted(words, key=lambda word: word["start"])


def align_words(texts, words, duration, turn_indices=None, caption_turns=None):
    result = (
        align_cue_groups(texts, words, duration, turn_indices)
        if turn_indices is not None
        else align_texts(texts, words, duration)
    )
    if caption_turns is not None:
        from runtime.podcast_cues import align_captions, captions_for_episode

        if "".join(map(normalized, caption_turns)) != "".join(map(normalized, texts)):
            raise ValueError("SCENE_ALIGNMENT_FAILED")
        try:
            captions = align_captions(caption_turns, words, duration)
            # 使用消費端相同的文字／時間核對；字幕粒度失敗不推翻已成立的段落對齊。
            captions_for_episode(
                {
                    "script": {
                        "segments": [
                            {"turns": [{"text": text} for text in caption_turns]}
                        ]
                    },
                    "audio": {"duration_seconds": duration},
                },
                captions,
            )
        except AlignmentError:
            result["method"] += ";paragraph-captions/v1"
        except ValueError as error:
            if str(error) != "PODCAST_CAPTION_ALIGNMENT_INVALID":
                raise
            result["method"] += ";paragraph-captions/v1"
        else:
            result["caption_alignment"] = captions
    return result


if __name__ == "__main__":
    try:
        request = json.load(sys.stdin)
        result = render(
            Path(sys.argv[1]),
            request["texts"],
            request.get("turn_indices"),
            request.get("caption_turns"),
        )
        Path(sys.argv[2]).write_text(
            json.dumps(result, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        raise SystemExit(1)
