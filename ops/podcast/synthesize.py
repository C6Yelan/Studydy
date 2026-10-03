"""B 聲線：固定參考、通用正規化與英文片段。"""
import json
import os
from pathlib import Path
import sys


def render(body, output):
    segments = body.get("script", {}).get("segments")
    if not isinstance(segments, list) or not 1 <= len(segments) <= 6:
        raise ValueError("REQUEST_INVALID")
    turns = []
    for segment in segments:
        group = segment.get("turns")
        if not isinstance(group, list) or not 1 <= len(group) <= 8:
            raise ValueError("REQUEST_INVALID")
        for turn in group:
            if (not isinstance(turn, dict) or turn.get("speaker") not in {"host", "guest"}
                or not isinstance(turn.get("text"), str) or not 5 <= len(turn["text"].strip()) <= 1600):
                raise ValueError("REQUEST_INVALID")
            turns.append(turn)

    import logging
    import numpy as np
    import soundfile as sf
    import torch
    import wetext
    from tn.chinese.normalizer import Normalizer as ZhNormalizer
    from tn.english.normalizer import Normalizer as EnNormalizer
    from spoken_input import speech_spans

    cache = Path(os.environ["STUDYDY_COSYVOICE_NORMALIZER_CACHE"])
    zh = ZhNormalizer(cache_dir=str(cache / "fst-simplified"), traditional_to_simple=True,
                      remove_interjections=False, remove_erhua=False)
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
    reference_text = json.loads((voices / "reference-text.json").read_text())
    groups = []
    for turn in turns:
        if groups and groups[-1]["speaker"] == turn["speaker"]:
            groups[-1]["text"] += " " + turn["text"]
        else:
            groups.append(dict(turn))
    samples, total = [], 0
    for turn in groups:
        speaker = turn["speaker"]
        set_all_random_seed(42 if speaker == "host" else 43)
        reference = str(voices / f"{speaker}.wav")
        prompt = model.frontend.text_normalize(reference_text[speaker], split=False)
        plan = speech_spans(turn["text"], zh.normalize, en.normalize)
        for item in plan:
            if item["language"] == "en":
                generated = model.inference_cross_lingual(
                    "You are a helpful assistant.<|endofprompt|>" + item["text"] + ".",
                    reference, stream=False, text_frontend=False)
            else:
                generated = model.inference_zero_shot(item["text"],
                    "You are a helpful assistant.<|endofprompt|>" + prompt,
                    reference, stream=False, text_frontend=False)
            chunks = [c["tts_speech"].detach().cpu().numpy().reshape(-1) for c in generated]
            if not chunks:
                raise RuntimeError("PODCAST_AUDIO_INVALID")
            data = np.concatenate(chunks)
            total += data.size
            if model.sample_rate != 24000 or data.size < 2400 or not np.isfinite(data).all() or total > 24000 * 1800:
                raise RuntimeError("PODCAST_AUDIO_INVALID")
            samples.extend([data, np.zeros(1920, dtype=np.float32)])
        samples.append(np.zeros(5280, dtype=np.float32))
    if not samples:
        raise RuntimeError("PODCAST_AUDIO_INVALID")
    sf.write(output, np.concatenate(samples), 24000, format="WAV", subtype="PCM_16")


if __name__ == "__main__":
    render(json.load(sys.stdin), sys.argv[1])
