"""每集的音訊對齊獨立於文字研究；不按影格建立工作。"""
import logging
from threading import Event,Thread
from . import podcast_scenes

class SceneWorker:
    def __init__(self,dsn):
        self.dsn=dsn;self.stop_event=Event();self.thread=Thread(target=self.loop,name='studydy-scenes',daemon=True)
    def start(self):self.thread.start();return self
    def stop(self):self.stop_event.set();self.thread.join(timeout=2)
    def loop(self):
        while not self.stop_event.wait(1):
            try:podcast_scenes.step(dsn=self.dsn)
            except Exception:logging.getLogger(__name__).warning('SCENE_WORKER_FAILED')
