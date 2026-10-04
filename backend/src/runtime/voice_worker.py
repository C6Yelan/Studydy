"""短語音工作獨立執行；關頁不遺失已送出的問答。"""
import logging
from threading import Event, Thread
from . import voice


class VoiceWorker:
    def __init__(self, dsn):
        self.dsn=dsn
        self.stop_event=Event()
        self.thread=Thread(target=self.loop,name='studydy-voice',daemon=True)
    def start(self):
        self.thread.start();return self
    def stop(self):
        self.stop_event.set();self.thread.join(timeout=2)
    def loop(self):
        while not self.stop_event.wait(1):
            try: voice.step(dsn=self.dsn)
            except Exception: logging.getLogger(__name__).warning('VOICE_WORKER_FAILED')
