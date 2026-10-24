"""媒體來源的純邏輯：YouTube 網址與限制、字幕選擇、WebVTT、時間段頁面與沙箱腳本。不連網。"""

import importlib.util
import io
import json
from pathlib import Path
import zipfile

import av
import pymupdf
import pytest

from document_normalization import converter
from runtime.errors import SourceError
from runtime.media_sources import (
    check_youtube_info,
    parse_vtt,
    pick_subtitle,
    youtube_video_id,
)

MEDIA = Path(converter.__file__).with_name("media.py")


def _media_module():
    spec = importlib.util.spec_from_file_location("sandbox_media", MEDIA)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=iG9CE55wbtY",
        "https://www.youtube.com/watch?v=iG9CE55wbtY&list=PL0123456789&index=3",
        "youtu.be/iG9CE55wbtY?t=30",
        "https://m.youtube.com/shorts/iG9CE55wbtY",
        "https://www.youtube.com/live/iG9CE55wbtY",
    ],
)
def test_single_video_urls_including_shorts_resolve_to_one_id(url):
    assert youtube_video_id(url) == "iG9CE55wbtY"


@pytest.mark.parametrize(
    "url, reason",
    [
        ("https://www.youtube.com/playlist?list=PL0123456789", "YOUTUBE_PLAYLIST_REJECTED"),
        ("https://example.com/watch?v=iG9CE55wbtY", "YOUTUBE_URL_INVALID"),
        ("https://www.youtube.com/watch?v=short", "YOUTUBE_URL_INVALID"),
        ("https://www.youtube.com/@channel", "YOUTUBE_URL_INVALID"),
        ("javascript:alert(1)", "YOUTUBE_URL_INVALID"),
    ],
)
def test_playlists_and_other_urls_are_rejected(url, reason):
    with pytest.raises(SourceError, match=reason):
        youtube_video_id(url)


def _info(**changes):
    return {"id": "iG9CE55wbtY", "duration": 600, "live_status": "not_live", "availability": "public", "age_limit": 0, **changes}


@pytest.mark.parametrize(
    "changes, reason",
    [
        ({"live_status": "is_live"}, "YOUTUBE_LIVE"),
        ({"live_status": "is_upcoming"}, "YOUTUBE_LIVE"),
        ({"availability": "subscriber_only"}, "YOUTUBE_MEMBERS_ONLY"),
        ({"availability": "private"}, "YOUTUBE_PRIVATE"),
        ({"age_limit": 18}, "YOUTUBE_AGE_RESTRICTED"),
        ({"duration": 7201}, "MEDIA_TOO_LONG"),
        ({"duration": None}, "YOUTUBE_UNAVAILABLE"),
        ({"_type": "playlist"}, "YOUTUBE_PLAYLIST_REJECTED"),
    ],
)
def test_restricted_youtube_videos_are_rejected_with_reason(changes, reason):
    with pytest.raises(SourceError, match=reason):
        check_youtube_info(_info(**changes))


def test_accepted_youtube_video_includes_finished_streams_and_two_hours():
    check_youtube_info(_info(live_status="was_live", duration=7200))


def _tracks(*languages):
    return {language: [{"ext": "vtt", "url": f"https://example.invalid/{language}"}] for language in languages}


def test_uploader_subtitles_prefer_traditional_then_simplified_then_original_language():
    assert pick_subtitle(_info(language="en", subtitles=_tracks("en", "zh-CN", "zh-TW"))) == "zh-TW"
    assert pick_subtitle(_info(language="en", subtitles=_tracks("en", "zh-Hans"))) == "zh-Hans"
    assert pick_subtitle(_info(language="ja", subtitles=_tracks("ja", "en"))) == "ja"
    assert pick_subtitle(_info(language="en", subtitles=_tracks("en-US"))) == "en-US"
    assert pick_subtitle(_info(language="en-US", subtitles=_tracks("en", "fr"))) == "en"
    # 只有其他語言、或只有自動字幕時改用本機辨識。
    assert pick_subtitle(_info(language="en", subtitles=_tracks("fr"))) is None
    assert pick_subtitle(_info(language="en", subtitles={}, automatic_captions=_tracks("en", "zh-TW"))) is None
    assert pick_subtitle(_info(language="en", subtitles={"en": [{"ext": "json3", "url": "x"}]})) is None


def test_vtt_cues_strip_markup_and_merge_repeated_lines():
    text = (
        "WEBVTT\nKind: captions\n\n"
        "00:00:01.000 --> 00:00:03.500\n<c.colorE5E5E5>堆疊</c> &amp; 佇列\n\n"
        "00:00:03.500 --> 00:00:05.000\n堆疊 &amp; 佇列\n\n"
        "01:02:03.250 --> 01:02:04.000 align:start\n第二行\n第三行\n\n"
        "00:10.000 --> 00:12.000\n\n"
    )
    assert parse_vtt(text) == [
        {"start": 1.0, "end": 5.0, "text": "堆疊 & 佇列"},
        {"start": 3723.25, "end": 3724.0, "text": "第二行 第三行"},
    ]


def test_transcript_pages_follow_two_minute_windows_without_empty_pages():
    media = _media_module()
    pages = media.transcript_pages(
        [
            {"start": 5, "end": 10, "text": "a"},
            {"start": 118, "end": 125, "text": "b"},
            {"start": 361, "end": 370, "text": "c"},
        ],
        400,
    )
    assert [(page["start_seconds"], page["end_seconds"]) for page in pages] == [(0, 125), (360, 400)]
    assert [len(page["segments"]) for page in pages] == [2, 1]


def _wave(seconds, path):
    with av.open(str(path), "w", format="wav") as output:
        stream = output.add_stream("pcm_s16le", rate=8000)
        stream.layout = "mono"
        frame = av.AudioFrame(format="s16", layout="mono", samples=8000)
        frame.sample_rate = 8000
        for plane in frame.planes:
            plane.update(bytes(plane.buffer_size))
        for second in range(seconds):
            frame.pts = second * 8000
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode(None):
            output.mux(packet)


def test_probe_rejects_media_longer_than_the_limit(tmp_path, monkeypatch):
    media = _media_module()
    source = tmp_path / "speech.wav"
    _wave(3, source)
    media.probe(source, tmp_path)
    assert round(json.loads((tmp_path / "probe.json").read_text())["duration_seconds"]) == 3
    monkeypatch.setattr(media, "MAX_MEDIA_SECONDS", 1)
    with pytest.raises(ValueError, match="MEDIA_TOO_LONG"):
        media.probe(source, tmp_path)


def _png(text):
    document = pymupdf.open()
    page = document.new_page(width=200, height=100)
    page.insert_text((10, 50), text)
    return page.get_pixmap().tobytes("png")


def _zip(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return buffer.getvalue()


def test_image_set_becomes_one_page_per_image_in_order(tmp_path):
    media = _media_module()
    source = tmp_path / "images.zip"
    source.write_bytes(_zip([("0001.png", _png("first")), ("0002.png", _png("second"))]))
    output = tmp_path / "output"
    output.mkdir()
    media.image_set(source, output)
    mapping = json.loads((output / "mapping.json").read_text())
    assert mapping["format"] == "images" and mapping["page_count"] == 2
    assert [record["origin_locator"] for record in mapping["records"]] == [
        {"image_number": 1},
        {"image_number": 2},
    ]
    with pymupdf.open(output / "normalized.pdf") as document:
        assert max(document[0].rect.width, document[0].rect.height) == pytest.approx(842)


@pytest.mark.parametrize(
    "entries",
    [
        [("0002.png", b"\x89PNG\r\n\x1a\n")],
        [("0001.gif", b"GIF89a")],
        [("0001.png", b"\x89PNG\r\n\x1a\n"), ("../0002.png", b"\x89PNG\r\n\x1a\n")],
        [(f"{number:04d}.png", b"\x89PNG\r\n\x1a\n") for number in range(1, 52)],
    ],
)
def test_image_set_rejects_unordered_unknown_or_too_many_entries(tmp_path, entries):
    media = _media_module()
    source = tmp_path / "images.zip"
    source.write_bytes(_zip(entries))
    with pytest.raises(ValueError, match="IMAGE_SET_INVALID"):
        media.image_set(source, tmp_path)


def test_image_type_must_match_its_extension(tmp_path):
    media = _media_module()
    source = tmp_path / "images.zip"
    source.write_bytes(_zip([("0001.jpg", _png("not a jpeg"))]))
    with pytest.raises(ValueError, match="IMAGE_UNREADABLE"):
        media.image_set(source, tmp_path)


@pytest.mark.parametrize(
    ("orientation", "rotation"),
    # PyMuPDF 的 rotate 為逆時針：6（順時針 90 度）是 270；5、7 只補旋轉、不處理鏡像。
    [(1, 0), (2, 0), (3, 180), (4, 0), (5, 90), (6, 270), (7, 270), (8, 90)],
)
def test_jpeg_orientation_is_read_from_exif(orientation, rotation):
    media = _media_module()
    entry = b"\x01\x12\x00\x03\x00\x00\x00\x01" + bytes([0, orientation]) + b"\x00\x00"
    tiff = b"MM\x00\x2a\x00\x00\x00\x08" + b"\x00\x01" + entry + b"\x00\x00\x00\x00"
    app1 = b"Exif\x00\x00" + tiff
    data = b"\xff\xd8" + b"\xff\xe1" + (len(app1) + 2).to_bytes(2, "big") + app1 + b"\xff\xda"
    assert media._jpeg_rotation(data) == rotation
    assert media._jpeg_rotation(b"\xff\xd8\xff\xda") == 0


def test_transcript_pdf_keeps_one_page_per_window_and_time_mapping(tmp_path):
    source = tmp_path / "transcript.json"
    source.write_text(
        json.dumps(
            {
                "schema": "media-transcript/v1",
                "format": "youtube",
                "duration_seconds": 300,
                "original_sha256": "a" * 64,
                "media": {"video_id": "iG9CE55wbtY", "transcript_source": "uploader_subtitles"},
                "segments": [
                    {"start": 1, "end": 4, "text": "堆疊遵守後進先出。" * 40},
                    {"start": 130, "end": 140, "text": "佇列遵守先進先出。"},
                    {"start": 400, "end": 401, "text": "片尾之後的字幕不成頁"},
                ],
            },
            ensure_ascii=False,
        )
    )
    output = tmp_path / "output"
    output.mkdir()
    _media_module().transcript(source, output)
    mapping = json.loads((output / "mapping.json").read_text())
    assert mapping["original_sha256"] == "a" * 64 and mapping["format"] == "youtube"
    assert mapping["media"]["video_id"] == "iG9CE55wbtY"
    assert [record["origin_locator"] for record in mapping["records"]] == [
        {"start_seconds": 0, "end_seconds": 120},
        {"start_seconds": 120, "end_seconds": 240},
    ]
    with pymupdf.open(output / "normalized.pdf") as document:
        assert len(document) == 2
        assert "後進先出" in document[0].get_text() and "先進先出" in document[1].get_text()
        assert "片尾" not in "".join(page.get_text() for page in document)


def test_phone_photo_with_exif_orientation_is_upright_and_stays_compressed(tmp_path):
    import numpy

    upright = numpy.full((300, 400, 3), 255, numpy.uint8)
    upright[:60, :80] = 0  # 正向時左上角的黑塊
    stored = numpy.ascontiguousarray(numpy.rot90(upright, 1))  # 手機直拍：像素逆時針轉，EXIF 6
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, stored.shape[1], stored.shape[0], stored.tobytes(), False)
    tiff = b"MM\x00\x2a\x00\x00\x00\x08\x00\x01\x01\x12\x00\x03\x00\x00\x00\x01\x00\x06\x00\x00\x00\x00\x00\x00"
    app1 = b"Exif\x00\x00" + tiff
    jpeg = pixmap.tobytes("jpg", jpg_quality=90)
    jpeg = jpeg[:2] + b"\xff\xe1" + (len(app1) + 2).to_bytes(2, "big") + app1 + jpeg[2:]
    media = _media_module()
    for name, data, limit in (("small", jpeg, None), ("reencoded", jpeg, 1)):
        if limit:
            media.KEEP_ORIGINAL_BYTES = limit  # 強制走重新壓縮的路徑
        source = tmp_path / f"{name}.zip"
        source.write_bytes(_zip([("0001.jpg", data)]))
        output = tmp_path / name
        output.mkdir()
        media.image_set(source, output)
        with pymupdf.open(output / "normalized.pdf") as document:
            page = document[0]
            assert page.rect.width > page.rect.height
            render = page.get_pixmap(dpi=36)
            assert render.pixel(3, 3)[0] < 60 and render.pixel(render.width - 4, render.height - 4)[0] > 200
            # 嵌入的是壓縮影像，不是未壓縮像素。
            assert (output / "normalized.pdf").stat().st_size < 400 * 300 * 3 / 2


def test_video_audio_is_transcoded_when_the_track_cannot_be_copied(tmp_path, monkeypatch):
    import numpy

    video = tmp_path / "lecture.mp4"
    with av.open(str(video), "w") as output:
        picture = output.add_stream("mpeg4", rate=5)
        picture.width, picture.height, picture.pix_fmt = 64, 48, "yuv420p"
        sound = output.add_stream("aac", rate=48000)
        frame = av.AudioFrame(format="fltp", layout="stereo", samples=1024)
        frame.sample_rate = 48000
        for plane in frame.planes:
            plane.update(bytes(plane.buffer_size))
        for index in range(2 * 48000 // 1024):
            frame.pts = index * 1024
            for packet in sound.encode(frame):
                output.mux(packet)
        for index in range(10):
            image = av.VideoFrame.from_ndarray(numpy.zeros((48, 64, 3), numpy.uint8), format="rgb24")
            for packet in picture.encode(image):
                output.mux(packet)
        for stream in (picture, sound):
            for packet in stream.encode(None):
                output.mux(packet)
    media = _media_module()

    def refuse(self, *args, **kwargs):
        raise ValueError("codec not supported in container")

    monkeypatch.setattr(av.container.OutputContainer, "add_stream_from_template", refuse)
    media.extract(video, tmp_path)
    with av.open(str(tmp_path / "audio.m4a")) as stored:
        assert [stream.type for stream in stored.streams] == ["audio"]
        assert stored.streams.audio[0].codec_context.name == "aac"
        assert stored.streams.audio[0].codec_context.sample_rate == 24000
