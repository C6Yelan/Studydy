"""在隔離子程序中處理圖片組、音訊與逐字稿；不連網、不接 DB 或模型。

用法：media.py <command> <input> <output_dir>
- probe：輸出音訊長度
- extract：影片只保留第一條音軌（mp4 容器），原影片不輸出
- stt：轉成 16 kHz 單聲道 Opus，供本機語音辨識使用
- images：依 0001.png… 順序把圖片組排成一頁一張的 PDF
- transcript：把逐字稿依時間段排成一段一頁的 PDF
"""

from fractions import Fraction
import hashlib
import html
import io
import json
from pathlib import Path
import sys
import zipfile

import av
import pymupdf


MAX_IMAGES = 50
MAX_IMAGE_SET_BYTES = 100 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
# OCR 以 200 dpi 繪製長邊 842 pt 的頁面（約 2,340 px）；更高的解析度只會讓 PDF 變大。
MAX_EMBED_SIDE = 2500
# 小於此大小、解析度也夠低的 JPEG 原樣嵌入；其餘重新壓縮，讓 50 張照片仍在 100 MiB 內。
KEEP_ORIGINAL_BYTES = 1536 * 1024
MAX_MEDIA_SECONDS = 2 * 60 * 60
# 長度以容器 metadata 判斷；容許少量編碼尾端誤差，不容許實際更長的內容。
DURATION_TOLERANCE_SECONDS = 1
SEGMENT_SECONDS = 120
PAGE = pymupdf.Rect(0, 0, 595, 842)
_IMAGE_TYPES = {
    ".png": b"\x89PNG\r\n\x1a\n",
    ".jpg": b"\xff\xd8\xff",
    ".jpeg": b"\xff\xd8\xff",
    ".webp": b"RIFF",
}
_SAFE_ERRORS = {
    "MEDIA_UNREADABLE",
    "MEDIA_NO_AUDIO",
    "MEDIA_TOO_LONG",
    "MATERIAL_TOO_LARGE",
    "IMAGE_SET_INVALID",
    "IMAGE_UNREADABLE",
    "IMAGE_TOO_LARGE",
    "TRANSCRIPT_EMPTY",
    "TRANSCRIPT_INVALID",
    "NORMALIZER_VERSION_MISMATCH",
}


def _audio_stream(container):
    if not container.streams.audio:
        raise ValueError("MEDIA_NO_AUDIO")
    return container.streams.audio[0]


def media_duration(path):
    """先讀 metadata；沒有長度時實際解碼計算，不以檔案大小猜測。"""
    try:
        with av.open(str(path)) as container:
            stream = _audio_stream(container)
            if container.duration:
                return container.duration / av.time_base
            if stream.duration and stream.time_base:
                return float(stream.duration * stream.time_base)
            samples = 0
            rate = stream.codec_context.sample_rate or 1
            for frame in container.decode(stream):
                samples += frame.samples
                if samples / rate > MAX_MEDIA_SECONDS + DURATION_TOLERANCE_SECONDS:
                    raise ValueError("MEDIA_TOO_LONG")
            return samples / rate
    except av.error.FFmpegError:
        raise ValueError("MEDIA_UNREADABLE") from None


def probe(source, output):
    duration = media_duration(source)
    if not duration or duration <= 0:
        raise ValueError("MEDIA_UNREADABLE")
    if duration > MAX_MEDIA_SECONDS + DURATION_TOLERANCE_SECONDS:
        raise ValueError("MEDIA_TOO_LONG")
    (output / "probe.json").write_text(json.dumps({"duration_seconds": duration}))


def extract(source, output):
    """原音軌直接搬進 mp4 容器；容器不接受的編碼才轉成 AAC。"""
    probe(source, output)
    target = output / "audio.m4a"
    try:
        with av.open(str(source)) as container:
            stream = _audio_stream(container)
            try:
                with av.open(str(target), "w", format="mp4") as muxed:
                    copied = muxed.add_stream_from_template(stream)
                    for packet in container.demux(stream):
                        if packet.dts is None:
                            continue
                        packet.stream = copied
                        muxed.mux(packet)
                return
            except (av.error.FFmpegError, ValueError):
                target.unlink(missing_ok=True)
        with av.open(str(source)) as container, av.open(
            str(target), "w", format="mp4"
        ) as muxed:
            stream = _audio_stream(container)
            # 少見的音軌編碼才走這裡；單聲道 24 kHz 讓 2 小時影片約 40 秒轉完，
            # 最後一段上傳請求不會超過 Cloudflare 約 100 秒的回應期限。
            encoded = muxed.add_stream("aac", rate=24000)
            encoded.bit_rate = 64000
            encoded.layout = "mono"
            resampler = av.AudioResampler(format="fltp", layout="mono", rate=24000)
            for frame in container.decode(stream):
                for converted in resampler.resample(frame):
                    muxed.mux(encoded.encode(converted))
            for converted in resampler.resample(None):
                muxed.mux(encoded.encode(converted))
            muxed.mux(encoded.encode(None))
    except av.error.FFmpegError:
        raise ValueError("MEDIA_UNREADABLE") from None


def stt_audio(source, output):
    """語音辨識只需 16 kHz 單聲道；壓成 Opus 讓兩小時內容仍在 provider 請求上限內。"""
    probe(source, output)
    target = output / "speech.ogg"
    rate = 16000
    try:
        with av.open(str(source)) as container, av.open(
            str(target), "w", format="ogg"
        ) as muxed:
            stream = _audio_stream(container)
            encoded = muxed.add_stream("libopus", rate=rate)
            encoded.bit_rate = 32000
            encoded.layout = "mono"
            resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)
            samples = 0
            for frame in container.decode(stream):
                for converted in resampler.resample(frame):
                    samples += converted.samples
                    if samples / rate > MAX_MEDIA_SECONDS + DURATION_TOLERANCE_SECONDS:
                        raise ValueError("MEDIA_TOO_LONG")
                    converted.pts = None
                    muxed.mux(encoded.encode(converted))
            for converted in resampler.resample(None):
                converted.pts = None
                muxed.mux(encoded.encode(converted))
            muxed.mux(encoded.encode(None))
    except av.error.FFmpegError:
        raise ValueError("MEDIA_UNREADABLE") from None
    if samples < rate:
        raise ValueError("MEDIA_UNREADABLE")


def _jpeg_rotation(data):
    """讀 EXIF Orientation；手機直拍的投影片要轉正才能 OCR。鏡像方向不處理。"""
    index = 2
    while index + 4 <= len(data) and data[index] == 0xFF:
        marker = data[index + 1]
        size = int.from_bytes(data[index + 2 : index + 4], "big")
        if marker == 0xE1 and data[index + 4 : index + 10] == b"Exif\0\0":
            tiff = data[index + 10 : index + 2 + size]
            order = {b"II": "little", b"MM": "big"}.get(tiff[:2])
            if order is None or len(tiff) < 8:
                return 0
            offset = int.from_bytes(tiff[4:8], order)
            if offset + 2 > len(tiff):
                return 0
            count = int.from_bytes(tiff[offset : offset + 2], order)
            for entry in range(count):
                start = offset + 2 + entry * 12
                if start + 12 > len(tiff):
                    return 0
                if int.from_bytes(tiff[start : start + 2], order) == 0x0112:
                    value = int.from_bytes(tiff[start + 8 : start + 10], order)
                    # PyMuPDF 的 rotate 為逆時針；EXIF 6 需順時針 90 度，即逆時針 270 度。
                    # 5、7 是鏡像加旋轉：只補旋轉（與 8、6 同向），鏡像不處理。
                    return {3: 180, 5: 90, 6: 270, 7: 270, 8: 90}.get(value, 0)
            return 0
        if marker in (0xDA, 0xD9):
            return 0
        index += 2 + size
    return 0


def _image_pixmap(name, data):
    extension = Path(name).suffix.lower()
    if not data.startswith(_IMAGE_TYPES[extension]) or (
        extension == ".webp" and data[8:12] != b"WEBP"
    ):
        raise ValueError("IMAGE_UNREADABLE")
    try:
        if extension == ".webp":
            # MuPDF 不讀 WebP；以同一份 FFmpeg 解出第一格再交給 MuPDF。
            with av.open(io.BytesIO(data), format="webp_pipe") as container:
                stream = container.streams.video[0]
                if (stream.codec_context.width or 0) * (
                    stream.codec_context.height or 0
                ) > MAX_IMAGE_PIXELS:
                    raise ValueError("IMAGE_TOO_LARGE")
                frame = next(container.decode(stream)).reformat(format="rgb24")
            # 不依賴 numpy：逐列去掉 FFmpeg 的行距補齊位元組。
            plane, width, height = frame.planes[0], frame.width, frame.height
            raw = bytes(plane)
            pixels = b"".join(
                raw[row * plane.line_size : row * plane.line_size + width * 3]
                for row in range(height)
            )
            return pymupdf.Pixmap(pymupdf.csRGB, width, height, pixels, False), 0
        pixmap = pymupdf.Pixmap(data)
    except (av.error.FFmpegError, StopIteration, RuntimeError, IndexError):
        raise ValueError("IMAGE_UNREADABLE") from None
    if pixmap.width * pixmap.height > MAX_IMAGE_PIXELS:
        raise ValueError("IMAGE_TOO_LARGE")
    if pixmap.alpha:
        pixmap = pymupdf.Pixmap(pixmap, 0)
    return pixmap, _jpeg_rotation(data) if extension in (".jpg", ".jpeg") else 0


def _embedded_image(name, data):
    """回傳要嵌入 PDF 的壓縮影像 bytes、原始寬高與旋轉角度，不以未壓縮像素嵌入。"""
    pixmap, rotation = _image_pixmap(name, data)
    width, height = pixmap.width, pixmap.height
    if (
        Path(name).suffix.lower() in (".jpg", ".jpeg")
        and len(data) <= KEEP_ORIGINAL_BYTES
        and max(width, height) <= MAX_EMBED_SIDE
    ):
        return data, width, height, rotation
    if max(width, height) > MAX_EMBED_SIDE:
        scale = MAX_EMBED_SIDE / max(width, height)
        pixmap = pymupdf.Pixmap(
            pixmap, max(1, round(width * scale)), max(1, round(height * scale)), None
        )
    # 投影片截圖用 PNG 保留銳利文字；照片類 PNG 過大時改用 JPEG。
    encoded = pixmap.tobytes("png")
    if len(encoded) > KEEP_ORIGINAL_BYTES:
        encoded = pixmap.tobytes("jpg", jpg_quality=88)
    return encoded, width, height, rotation


def image_set(source, output):
    """壓縮檔只能有 0001.<格式> 起連號的圖片；第幾張就是第幾頁。"""
    if source.stat().st_size > MAX_IMAGE_SET_BYTES:
        raise ValueError("MATERIAL_TOO_LARGE")
    try:
        archive = zipfile.ZipFile(source)
    except zipfile.BadZipFile:
        raise ValueError("IMAGE_SET_INVALID") from None
    with archive:
        entries = archive.infolist()
        if not 1 <= len(entries) <= MAX_IMAGES or sum(
            entry.file_size for entry in entries
        ) > MAX_IMAGE_SET_BYTES:
            raise ValueError("IMAGE_SET_INVALID")
        for number, entry in enumerate(entries, 1):
            stem, _, extension = entry.filename.partition(".")
            if (
                stem != f"{number:04d}"
                or "." + extension not in _IMAGE_TYPES
                or entry.flag_bits & 1
                or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
            ):
                raise ValueError("IMAGE_SET_INVALID")
        target = output / "normalized.pdf"
        records = []
        with pymupdf.open() as document:
            for number, entry in enumerate(entries, 1):
                image, width, height, rotation = _embedded_image(
                    entry.filename, archive.read(entry)
                )
                if rotation in (90, 270):
                    width, height = height, width
                scale = PAGE.height / max(width, height)
                page = document.new_page(width=width * scale, height=height * scale)
                page.insert_image(page.rect, stream=image, rotate=rotation)
                records.append(
                    {
                        "normalized_page": number,
                        "origin_locator": {"image_number": number},
                        "accuracy": "exact",
                    }
                )
            document.save(target, garbage=3, deflate=True)
    _write_mapping(source, target, output, "images", records, None)


def transcript_pages(segments, duration):
    """約 2 分鐘一頁：依每句開始時間歸入時間段，句子不拆開；沒有語音的時間段不成頁。"""
    windows = {}
    for segment in segments:
        windows.setdefault(int(segment["start"] // SEGMENT_SECONDS), []).append(segment)
    pages = []
    for window in sorted(windows):
        items = windows[window]
        start = window * SEGMENT_SECONDS
        end = max(
            min(start + SEGMENT_SECONDS, duration),
            max(item["end"] for item in items),
        )
        pages.append(
            {"start_seconds": start, "end_seconds": round(end, 3), "segments": items}
        )
    return pages


def _paragraphs(segments, size=180):
    """辨識片段多半只有半句；併成約 180 字的段落，避免 Evidence 碎成單句。原文不改寫。"""
    paragraphs, current = [], []
    for item in segments:
        current.append(item["text"])
        if sum(len(text) for text in current) >= size:
            paragraphs.append(" ".join(current))
            current = []
    if current:
        paragraphs.append(" ".join(current))
    return paragraphs


BOX = pymupdf.Rect(40, 40, 555, 802)


def _fitting_css(markup):
    """單頁放不下時縮小字級，維持「一頁＝一個時間段」的對應。"""
    for size in (11, 10, 9, 8, 7, 6):
        css = f"body {{font-family:sans-serif;font-size:{size}pt;}} p {{margin:0 0 4pt;}}"
        more, _ = pymupdf.Story(markup, user_css=css).place(BOX)
        if not more:
            return css
    raise ValueError("TRANSCRIPT_INVALID")


def transcript(source, output):
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
        segments = payload["segments"]
        duration = float(payload["duration_seconds"])
        kind = payload["format"]
        media = payload["media"]
        original_sha256 = payload["original_sha256"]
        if (
            payload.get("schema") != "media-transcript/v1"
            or kind not in ("audio", "video", "youtube")
            or not isinstance(media, dict)
            or not isinstance(original_sha256, str)
            or len(original_sha256) != 64
            or not 0 < duration <= MAX_MEDIA_SECONDS + DURATION_TOLERANCE_SECONDS
            or not isinstance(segments, list)
        ):
            raise ValueError
        cleaned = []
        for item in segments:
            text = " ".join(str(item["text"]).split())
            start, end = float(item["start"]), float(item["end"])
            if not 0 <= start <= end:
                raise ValueError
            # 字幕結尾常多出片尾空白；超出影片長度的片段不成頁。
            if text and start <= duration:
                cleaned.append({"start": start, "end": end, "text": text})
    except (KeyError, TypeError, ValueError, UnicodeError):
        raise ValueError("TRANSCRIPT_INVALID") from None
    if not cleaned:
        raise ValueError("TRANSCRIPT_EMPTY")
    pages = transcript_pages(sorted(cleaned, key=lambda item: item["start"]), duration)
    target = output / "normalized.pdf"
    buffer = io.BytesIO()
    # 同一個 writer 只嵌入一次字型，存檔前再取子集，避免逐字稿 PDF 過大。
    writer = pymupdf.DocumentWriter(buffer)
    for page in pages:
        markup = "".join(
            f"<p>{html.escape(text)}</p>" for text in _paragraphs(page["segments"])
        )
        story = pymupdf.Story(markup, user_css=_fitting_css(markup))
        device = writer.begin_page(PAGE)
        story.place(BOX)
        story.draw(device)
        writer.end_page()
    writer.close()
    with pymupdf.open(stream=buffer.getvalue(), filetype="pdf") as document:
        document.subset_fonts()
        document.save(target, garbage=3, deflate=True)
    records = [
        {
            "normalized_page": number,
            "origin_locator": {
                "start_seconds": page["start_seconds"],
                "end_seconds": page["end_seconds"],
            },
            "accuracy": "exact",
        }
        for number, page in enumerate(pages, 1)
    ]
    _write_mapping(
        None,
        target,
        output,
        kind,
        records,
        {**media, "duration_seconds": round(duration, 3)},
        original_sha256=original_sha256,
    )


def _write_mapping(source, target, output, kind, records, media, *, original_sha256=None):
    with pymupdf.open(target) as document:
        if len(document) != len(records):
            raise ValueError("TRANSCRIPT_INVALID")
        mapping = {
            "schema": "source-mapping/v1",
            "format": kind,
            "original_sha256": original_sha256
            or hashlib.sha256(source.read_bytes()).hexdigest(),
            "normalized_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "page_count": len(document),
            "records": records,
            "quality_notice": "圖片與語音內容由系統辨識，辨識品質不保證。",
        }
    if media is not None:
        mapping["media"] = media
    (output / "mapping.json").write_text(
        json.dumps(mapping, ensure_ascii=False, separators=(",", ":"))
    )


COMMANDS = {
    "probe": probe,
    "extract": extract,
    "stt": stt_audio,
    "images": image_set,
    "transcript": transcript,
}


if __name__ == "__main__":
    try:
        if pymupdf.VersionBind != "1.28.0" or av.__version__ != "19.0.1":
            raise ValueError("NORMALIZER_VERSION_MISMATCH")
        COMMANDS[sys.argv[1]](Path(sys.argv[2]), Path(sys.argv[3]))
    except Exception as error:
        print(str(error) if str(error) in _SAFE_ERRORS else "MEDIA_PROCESSING_FAILED")
        raise SystemExit(1)
