"""Provider 的 HTTP 傳輸；不擁有對話、研究或媒體工作的生命週期。"""

from contextlib import contextmanager
from http.client import HTTPConnection, HTTPSConnection
from io import BytesIO
import json
import os
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .errors import SourceError


@contextmanager
def generation_response(request):
    """連線仍有期限；連上後等待生成完成，不以固定秒數截斷生成。"""
    target = urlsplit(request.full_url)
    if target.scheme not in ("http", "https"):
        raise ValueError("GENERATION_PROVIDER_UNAVAILABLE")
    connection = (HTTPSConnection if target.scheme == "https" else HTTPConnection)(
        target.hostname, target.port, timeout=10
    )
    try:
        connection.connect()
        connection.sock.settimeout(None)
        connection.request(
            request.get_method(),
            request.selector,
            body=request.data,
            headers=dict(request.header_items()),
        )
        with connection.getresponse() as response:
            if response.status >= 400:
                raise HTTPError(
                    request.full_url,
                    response.status,
                    response.reason,
                    response.headers,
                    BytesIO(response.read(4096)),
                )
            yield response
    finally:
        connection.close()


def provider_request(path, body, *, unavailable):
    """統一既有 provider 的位址、認證與 JSON；錯誤語意由呼叫功能決定。"""
    base = os.environ.get("STUDYDY_PODCAST_PROVIDER_URL", "").rstrip("/")
    token = os.environ.get("STUDYDY_PODCAST_PROVIDER_TOKEN", "")
    if not base or not token:
        raise unavailable
    return Request(
        base + path,
        data=json.dumps(body, ensure_ascii=False).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + token,
        },
    )


def provider_error_code(error):
    """錯誤回應也有讀取上限；各功能仍各自限制可公開的錯誤碼。"""
    try:
        return json.loads(error.read(4096)).get("error_code")
    except Exception:
        return None


def provider(path, body):
    request = provider_request(
        path, body, unavailable=SourceError("VOICE_PROVIDER_UNAVAILABLE")
    )
    try:
        with (
            urlopen(request, timeout=5)
            if path == "/luna-health"
            else generation_response(request)
        ) as response:
            data = response.read(20 * 1024 * 1024 + 1)
    except HTTPError as error:
        code = provider_error_code(error)
        if code in {
            "VOICE_TRANSCRIPT_INVALID",
            "PODCAST_AUDIO_INVALID",
            "LUNA_GENERATION_FAILED",
            "LUNA_GENERATION_TIMEOUT",
            "SCENE_ALIGNMENT_FAILED",
        }:
            raise SourceError(code) from None
        raise SourceError(
            "VOICE_PROVIDER_UNAVAILABLE"
            if error.code == 503
            else "VOICE_PROVIDER_FAILED"
        ) from None
    if len(data) > 20 * 1024 * 1024:
        raise SourceError("VOICE_PROVIDER_FAILED")
    return data if path == "/audio" else json.loads(data)
