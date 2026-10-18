"""Voice 與 Podcast 共用的已發布 PCM WAV 契約；保留既有音訊錯誤碼。"""

import io
import wave
from .errors import PodcastError


def validate_audio(data):
    try:
        with wave.open(io.BytesIO(data), "rb") as audio:
            duration = audio.getnframes() / audio.getframerate()
            if (
                audio.getnchannels() != 1
                or audio.getsampwidth() != 2
                or audio.getframerate() != 24000
                or not 0.5 <= duration <= 1800
                or len(audio.readframes(audio.getnframes())) != audio.getnframes() * 2
            ):
                raise ValueError()
        return duration
    except Exception:
        raise PodcastError("PODCAST_AUDIO_INVALID") from None
