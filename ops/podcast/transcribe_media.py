"""教材音訊辨識子程序：本機 faster-whisper（顯示卡、批次推論），不外送音訊。

輸入為後端沙箱轉好的 16 kHz 單聲道 Opus；語言自動偵測，輸出片段時間與文字。
實測 1 小時中文課程：CPU int8 約 12.7 分鐘，顯示卡批次約 1 分鐘，因此使用顯示卡，
並由 provider 以 CosyVoice 的同一把鎖排程，避免同時佔用 GPU 記憶體。
"""

import json
import os
import sys

import av
import numpy as np
from faster_whisper import BatchedInferencePipeline, WhisperModel

MAX_SECONDS = 2 * 60 * 60 + 1


def decode(path):
    chunks = []
    total = 0
    with av.open(path) as container:
        if not container.streams.audio:
            raise ValueError("MEDIA_NO_AUDIO")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
        for frame in container.decode(audio=0):
            for sample in resampler.resample(frame):
                value = sample.to_ndarray().reshape(-1)
                total += len(value)
                if total > 16000 * MAX_SECONDS:
                    raise ValueError("MEDIA_TOO_LONG")
                chunks.append(value)
        for sample in resampler.resample(None):
            chunks.append(sample.to_ndarray().reshape(-1))
    if total < 16000:
        raise ValueError("MEDIA_UNREADABLE")
    return np.concatenate(chunks).astype(np.float32) / 32768


def transcribe(path):
    wave = decode(path)
    model = WhisperModel(
        os.environ["STUDYDY_STT_MODEL_DIR"],
        device="cuda",
        compute_type="int8_float16",
        local_files_only=True,
    )
    # 中文語音不加提示時常輸出簡體字；先偵測語言，中文才以繁體提示辨識。
    language, _, _ = model.detect_language(wave)
    segments, info = BatchedInferencePipeline(model).transcribe(
        wave,
        batch_size=8,
        vad_filter=True,
        condition_on_previous_text=False,
        language=language,
        initial_prompt="以下是繁體中文的課程內容。" if language == "zh" else None,
    )
    parts = [
        {"start": round(s.start, 3), "end": round(s.end, 3), "text": s.text.strip()}
        for s in segments
        if s.text.strip()
    ]
    return {
        "provider": "faster-whisper:large-v3-turbo;cuda-int8_float16;batched-8;zh-hant-prompt",
        "device": "cuda",
        "language": info.language,
        "duration_seconds": len(wave) / 16000,
        "segments": parts,
    }


if __name__ == "__main__":
    try:
        result = transcribe(sys.argv[1])
        with open(sys.argv[2], "w") as output:
            json.dump(result, output, ensure_ascii=False)
    except Exception:
        raise SystemExit(1)
