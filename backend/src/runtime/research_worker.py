"""研究 I/O 與教材分析分開排程；一次只取得一份來源。"""
import logging
from threading import Event,Thread
from . import research

class ResearchWorker:
    def __init__(self,dsn):
        self.dsn=dsn;self.stop_event=Event();self.thread=Thread(target=self.loop,name='studydy-research',daemon=True)
    def start(self):self.thread.start();return self
    def stop(self):self.stop_event.set();self.thread.join(timeout=2)
    def loop(self):
        while not self.stop_event.wait(2):
            try:research.step(dsn=self.dsn)
            except Exception:logging.getLogger(__name__).warning('RESEARCH_WORKER_FAILED')
