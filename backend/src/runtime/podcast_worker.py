"""獨立 Podcast runner，不佔用教材／題組 worker。"""
import json
import logging
import os
from threading import Event, Thread
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from . import podcasts


class PodcastWorker:
    def __init__(self, dsn):
        self.dsn = dsn
        self.stop_event = Event()
        self.thread = Thread(target=self.loop, name="studydy-podcast-worker", daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=2)

    def loop(self):
        while not self.stop_event.wait(1):
            claim = None
            saving = False
            try:
                claim = podcasts.claim_step(dsn=self.dsn)
                if claim is None:
                    continue
                base = os.environ.get("STUDYDY_PODCAST_PROVIDER_URL", "").rstrip("/")
                token = os.environ.get("STUDYDY_PODCAST_PROVIDER_TOKEN", "")
                if not base or not token:
                    raise podcasts.PodcastError("PODCAST_PROVIDER_UNAVAILABLE")
                episode = claim["episode"]
                audio = bool(episode["script"])
                body = {"script": episode["script"], "purpose": "podcast"} if audio else {
                    "claims": episode["claims"], "delivery": episode["delivery"],
                    "source_context": claim["source_context"]}
                request = Request(base + ("/audio" if audio else "/script"),
                    data=json.dumps(body, ensure_ascii=False).encode(),
                    headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
                try:
                    with urlopen(request, timeout=600) as response:
                        data = response.read(100 * 1024 * 1024 + 1)
                        audio_provider = response.headers.get("X-Studydy-Audio-Provider") if audio else None
                        audio_mastering = None
                        if audio:
                            raw_metadata=response.headers.get('X-Studydy-Audio-Mastering','')
                            if len(raw_metadata)>2048:raise podcasts.PodcastError('PODCAST_AUDIO_INVALID')
                            audio_mastering=json.loads(raw_metadata) if raw_metadata else None
                except HTTPError as failure:
                    try:
                        code = json.loads(failure.read(4096)).get("error_code")
                    except Exception:
                        code = None
                    if code in {"PODCAST_SCRIPT_INVALID", "PODCAST_SCRIPT_NEEDS_REVIEW", "PODCAST_AUDIO_INVALID", "LUNA_GENERATION_FAILED"}:
                        raise podcasts.PodcastError(code) from None
                    raise podcasts.PodcastError("PODCAST_PROVIDER_FAILED") from None
                if len(data) > 100 * 1024 * 1024:
                    raise podcasts.PodcastError("PODCAST_AUDIO_INVALID")
                result = data if audio else json.loads(data)
                saving = True
                if audio:
                    podcasts.finish_step(claim, audio=result, audio_provider=audio_provider, audio_mastering=audio_mastering, dsn=self.dsn)
                else:
                    podcasts.finish_step(claim, script=result, dsn=self.dsn)
            except Exception as error:
                if claim is not None:
                    code = str(error) if isinstance(error, podcasts.PodcastError) else ("PODCAST_STORAGE_FAILED" if saving else "PODCAST_PROVIDER_FAILED")
                    try:
                        podcasts.finish_step(claim, error=code, dsn=self.dsn)
                    except Exception:
                        logging.getLogger(__name__).warning("PODCAST_RESULT_SAVE_FAILED")
                else:
                    logging.getLogger(__name__).warning("PODCAST_WORKER_FAILED")
