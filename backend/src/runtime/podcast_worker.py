"""獨立 Podcast runner，不佔用教材／題組 worker。"""

from runtime.errors import PodcastError
import json
import logging
from urllib.error import HTTPError

from . import podcasts
from .generation_jobs import cancellable_job
from .provider_http import generation_response, provider_request, provider_error_code
from .storage.tables import Podcast


def step(*, dsn=None):
    claim = None
    saving = False
    try:
        claim = podcasts.claim_step(dsn=dsn)
        if claim is None:
            return
        episode = claim["episode"]
        audio = bool(episode["script"])
        with cancellable_job(
            Podcast,
            claim["podcast_id"],
            claim["token"],
            dsn=dsn,
            statuses=("running",),
        ) as job:
            body = (
                {"script": episode["script"], "purpose": "podcast"}
                if audio
                else {
                    "claims": episode["claims"],
                    "delivery": episode["delivery"],
                    "source_context": claim["source_context"],
                }
            )
            body.update(job)
            body["episode_index"] = claim["index"]
            request = provider_request(
                "/audio" if audio else "/script",
                body,
                unavailable=PodcastError("PODCAST_PROVIDER_UNAVAILABLE"),
            )
            try:
                with generation_response(request) as response:
                    data = response.read(100 * 1024 * 1024 + 1)
                    audio_provider = (
                        response.headers.get("X-Studydy-Audio-Provider")
                        if audio
                        else None
                    )
                    audio_mastering = None
                    if audio:
                        raw_metadata = response.headers.get(
                            "X-Studydy-Audio-Mastering", ""
                        )
                        if len(raw_metadata) > 2048:
                            raise PodcastError("PODCAST_AUDIO_INVALID")
                        audio_mastering = (
                            json.loads(raw_metadata) if raw_metadata else None
                        )
            except HTTPError as failure:
                code = provider_error_code(failure)
                if code in {
                    "PODCAST_SCRIPT_INVALID",
                    "PODCAST_SCRIPT_NEEDS_REVIEW",
                    "PODCAST_AUDIO_INVALID",
                    "LUNA_GENERATION_FAILED",
                    "PODCAST_CANCELLED",
                }:
                    raise PodcastError(code) from None
                raise PodcastError("PODCAST_PROVIDER_FAILED") from None
            if len(data) > 100 * 1024 * 1024:
                raise PodcastError("PODCAST_AUDIO_INVALID")
            result = data if audio else json.loads(data)
            saving = True
            if audio:
                podcasts.finish_step(
                    claim,
                    audio=result,
                    audio_provider=audio_provider,
                    audio_mastering=audio_mastering,
                    dsn=dsn,
                )
            else:
                podcasts.finish_step(claim, script=result, dsn=dsn)
    except Exception as error:
        if claim is not None:
            code = (
                str(error)
                if isinstance(error, PodcastError)
                else (
                    "PODCAST_STORAGE_FAILED"
                    if saving
                    else "PODCAST_PROVIDER_FAILED"
                )
            )
            logging.getLogger(__name__).warning(
                json.dumps(
                    {
                        "event": "podcast_step_failed",
                        "podcast_id": str(claim["podcast_id"]),
                        "episode_index": claim["index"],
                        "error_code": code,
                        "error_type": type(error).__name__,
                        "sqlstate": getattr(
                            getattr(error, "orig", None), "sqlstate", None
                        ),
                    }
                )
            )
            try:
                podcasts.finish_step(claim, error=code, dsn=dsn)
            except Exception:
                logging.getLogger(__name__).warning(
                    "PODCAST_RESULT_SAVE_FAILED"
                )
        else:
            logging.getLogger(__name__).warning("PODCAST_WORKER_FAILED")
