"""Voice 與 Podcast 共用的已發布 PCM WAV 契約；保留既有音訊錯誤碼。

Podcast 音訊以無損 FLAC 保存。WAV 的 PCM 以外的位元組（RIFF 標頭、fmt、LIST 等區塊）
依 flac 工具 `--keep-foreign-metadata` 的排法存成 `riff` APPLICATION metadata，
解碼時可還原與原檔逐位元相同的 WAV；分集的 `audio.sha256` 一律是這份 WAV 的雜湊，
因此影片、分鏡對齊與時間軸綁定的音訊身分不因保存格式改變。
"""

from hashlib import sha256
import io
import wave
from .errors import PodcastError

WAV_MEDIA_TYPE = "audio/wav"
FLAC_MEDIA_TYPE = "audio/flac"
_RATE = 24000
_FRAME_SAMPLES = 4608


def validate_audio(data):
    try:
        with wave.open(io.BytesIO(data), "rb") as audio:
            duration = audio.getnframes() / audio.getframerate()
            if (
                audio.getnchannels() != 1
                or audio.getsampwidth() != 2
                or audio.getframerate() != _RATE
                or not 0.5 <= duration <= 1800
                or len(audio.readframes(audio.getnframes())) != audio.getnframes() * 2
            ):
                raise ValueError()
        return duration
    except Exception:
        raise PodcastError("PODCAST_AUDIO_INVALID") from None


def _wav_parts(data):
    """拆成 foreign metadata 區塊與 PCM；data 區塊只存 8 位元組標頭。"""
    if (
        len(data) < 12
        or data[:4] != b"RIFF"
        or data[8:12] != b"WAVE"
        or int.from_bytes(data[4:8], "little") + 8 != len(data)
    ):
        raise ValueError("WAV_LAYOUT_UNSUPPORTED")
    blocks, pcm, position = [data[:12]], None, 12
    while position < len(data):
        if position + 8 > len(data):
            raise ValueError("WAV_LAYOUT_UNSUPPORTED")
        size = int.from_bytes(data[position + 4 : position + 8], "little")
        end = position + 8 + size + (size & 1)
        if end > len(data):
            raise ValueError("WAV_LAYOUT_UNSUPPORTED")
        if data[position : position + 4] == b"data":
            if pcm is not None or size & 1:
                raise ValueError("WAV_LAYOUT_UNSUPPORTED")
            blocks.append(data[position : position + 8])
            pcm = data[position + 8 : position + 8 + size]
        else:
            blocks.append(data[position:end])
        position = end
    if pcm is None:
        raise ValueError("WAV_LAYOUT_UNSUPPORTED")
    return blocks, pcm


def _metadata(flac):
    """回傳 (metadata 區塊清單, 音框起點)；每個區塊為 (type, body)。"""
    if flac[:4] != b"fLaC":
        raise ValueError("FLAC_INVALID")
    blocks, position, last = [], 4, False
    while not last:
        if position + 4 > len(flac):
            raise ValueError("FLAC_INVALID")
        header = flac[position]
        length = int.from_bytes(flac[position + 1 : position + 4], "big")
        body = flac[position + 4 : position + 4 + length]
        if len(body) != length:
            raise ValueError("FLAC_INVALID")
        blocks.append((header & 0x7F, body))
        last = bool(header & 0x80)
        position += 4 + length
    if blocks[0][0] != 0:
        raise ValueError("FLAC_INVALID")
    return blocks, position


def _decode_pcm(flac):
    import av

    chunks = []
    with av.open(io.BytesIO(flac), mode="r", format="flac") as source:
        stream = source.streams.audio[0]
        for frame in source.decode(stream):
            if (
                frame.format.name != "s16"
                or frame.layout.nb_channels != 1
                or frame.sample_rate != _RATE
            ):
                raise ValueError("FLAC_INVALID")
            chunks.append(bytes(frame.planes[0])[: frame.samples * 2])
    return b"".join(chunks)


def flac_to_wav(flac):
    """還原保存時的 WAV 位元組。"""
    try:
        blocks, _ = _metadata(flac)
        riff = [body[4:] for kind, body in blocks if kind == 2 and body[:4] == b"riff"]
        if not riff or riff[0][:4] != b"RIFF":
            raise ValueError("FLAC_INVALID")
        pcm = _decode_pcm(flac)
        output = bytearray()
        for block in riff:
            output += block
            if block[:4] == b"data" and len(block) == 8:
                if int.from_bytes(block[4:8], "little") != len(pcm):
                    raise ValueError("FLAC_INVALID")
                output += pcm
        return bytes(output)
    except Exception:
        raise PodcastError("PODCAST_AUDIO_INVALID") from None


def wav_to_flac(wav):
    """把已驗證的 24 kHz 單聲道 16-bit WAV 無損壓縮；解碼回原 WAV 不一致即拒絕。"""
    validate_audio(wav)
    try:
        import av

        blocks, pcm = _wav_parts(wav)
        output = io.BytesIO()
        with av.open(output, mode="w", format="flac") as target:
            stream = target.add_stream("flac", rate=_RATE, layout="mono")
            stream.format = "s16"
            step = _FRAME_SAMPLES * 2
            for offset in range(0, len(pcm), step):
                piece = pcm[offset : offset + step]
                frame = av.AudioFrame(
                    format="s16", layout="mono", samples=len(piece) // 2, align=1
                )
                frame.planes[0].update(piece)
                frame.sample_rate = _RATE
                frame.pts = offset // 2
                for packet in stream.encode(frame):
                    target.mux(packet)
            for packet in stream.encode(None):
                target.mux(packet)
        encoded = output.getvalue()
        metadata, audio_start = _metadata(encoded)
        metadata[1:1] = [(2, b"riff" + block) for block in blocks]
        flac = bytearray(b"fLaC")
        for index, (kind, body) in enumerate(metadata):
            flac.append(kind | (0x80 if index == len(metadata) - 1 else 0))
            flac += len(body).to_bytes(3, "big") + body
        flac += encoded[audio_start:]
        flac = bytes(flac)
    except Exception:
        raise PodcastError("PODCAST_AUDIO_INVALID") from None
    if flac_to_wav(flac) != wav:
        raise PodcastError("PODCAST_AUDIO_INVALID")
    return flac


def episode_wav(owner, episode, *, dsn=None):
    """讀取分集音訊並還原成發布時的 WAV；整檔核對 artifact 與分集雜湊後才交給 provider。"""
    from uuid import UUID
    from .storage.artifacts import open_verified_artifact

    with open_verified_artifact(
        owner, UUID(episode["audio"]["artifact_id"]), dsn=dsn, kind="podcast_audio"
    ) as source:
        data = source.file.read(100 * 1024 * 1024 + 1)
        media_type = source.media_type
    wav = flac_to_wav(data) if media_type == FLAC_MEDIA_TYPE else data
    if sha256(wav).hexdigest() != episode["audio"]["sha256"]:
        raise PodcastError("PODCAST_AUDIO_INVALID")
    validate_audio(wav)
    return wav
