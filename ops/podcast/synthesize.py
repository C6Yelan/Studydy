"""教材問答與 Podcast 共用原參考聲線、整句混讀及音訊修正。"""

import json
import os
from pathlib import Path
import sys

SPEECH_INSTRUCTION = (
    "You are a helpful assistant. 请用自然的台湾口音普通话说话。<|endofprompt|>"
)


def render(body, output):
    segments = body.get("script", {}).get("segments")
    if not isinstance(segments, list) or not 1 <= len(segments) <= 12:
        raise ValueError("REQUEST_INVALID")
    turns = []
    for segment_index, segment in enumerate(segments):
        group = segment.get("turns")
        if not isinstance(group, list) or not 1 <= len(group) <= 12:
            raise ValueError("REQUEST_INVALID")
        for turn in group:
            if (
                not isinstance(turn, dict)
                or turn.get("speaker") not in {"host", "guest"}
                or not isinstance(turn.get("text"), str)
                or not 1 <= len(turn["text"].strip()) <= 1600
            ):
                raise ValueError("REQUEST_INVALID")
            turns.append({**turn, "segment_index": segment_index})

    import logging
    import numpy as np
    import soundfile as sf
    import torch
    import wetext
    from tn.chinese.normalizer import Normalizer as ZhNormalizer
    from tn.english.normalizer import Normalizer as EnNormalizer
    from spoken_input import mixed_sentences

    cache = Path(os.environ["STUDYDY_COSYVOICE_NORMALIZER_CACHE"])
    zh = ZhNormalizer(
        cache_dir=str(cache / "fst-simplified"),
        traditional_to_simple=True,
        remove_interjections=False,
        remove_erhua=False,
    )
    en = EnNormalizer(cache_dir=str(cache / "fst-en"))
    wetext.Normalizer = lambda **kw: zh if "remove_erhua" in kw else en
    source = Path(os.environ["STUDYDY_COSYVOICE_SOURCE_DIR"])
    sys.path[:0] = [str(source), str(source / "third_party/Matcha-TTS")]
    from cosyvoice.cli.cosyvoice import AutoModel
    from cosyvoice.utils.common import set_all_random_seed

    # 模型的逐句 debug log 可能含教材，語音程序不輸出內容。
    logging.disable(logging.CRITICAL)
    torch.set_num_threads(4)
    model_dir = Path(os.environ["STUDYDY_COSYVOICE_MODEL_DIR"])
    model = AutoModel(model_dir=str(model_dir), fp16=True)
    weights = torch.load(model_dir / "llm.rl.pt", map_location="cpu", weights_only=True)
    model.model.llm.load_state_dict(weights, strict=True)
    del weights
    voices = Path(os.environ["STUDYDY_COSYVOICE_VOICES_DIR"])
    total, audio_chunks = 0, []
    from audio_mastering import trim_edges, master

    for turn in turns:
        speaker = turn["speaker"]
        set_all_random_seed(42 if speaker == "host" else 43)
        reference = str(voices / f"{speaker}.wav")
        plan = mixed_sentences(turn["text"], zh.normalize, en.normalize)
        if not plan:
            raise RuntimeError("PODCAST_AUDIO_INVALID")
        for item in plan:
            generated = model.inference_instruct2(
                item["text"],
                SPEECH_INSTRUCTION,
                reference,
                stream=False,
                text_frontend=False,
            )
            with torch.inference_mode():
                chunks = [
                    c["tts_speech"].detach().cpu().numpy().reshape(-1)
                    for c in generated
                ]
            if not chunks:
                raise RuntimeError("PODCAST_AUDIO_INVALID")
            data = np.concatenate(chunks)
            total += data.size
            if (
                model.sample_rate != 24000
                or data.size < 2400
                or not np.isfinite(data).all()
                or total > 24000 * 1800
            ):
                raise RuntimeError("PODCAST_AUDIO_INVALID")
            audio_chunks.append(
                {
                    "audio": trim_edges(data),
                    "text": item["text"],
                    "source_text": item["source_text"],
                    "ending": item["ending"],
                    "speaker": speaker,
                    "beat": turn["segment_index"],
                }
            )
    from dialogue_audio_repair import assemble

    combined = assemble(audio_chunks, 24000)
    if combined.size > 24000 * 1800:
        raise RuntimeError("PODCAST_AUDIO_INVALID")
    raw = Path(output).with_suffix(".float.wav")
    sf.write(raw, combined, 24000, format="WAV", subtype="FLOAT")
    metadata = master(raw, output)
    Path(output).with_suffix(".json").write_text(json.dumps(metadata))
    raw.unlink()


if __name__ == "__main__":
    render(json.load(sys.stdin), sys.argv[1])
