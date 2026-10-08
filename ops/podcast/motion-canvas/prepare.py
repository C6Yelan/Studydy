"""把 PoC 複製到隔離目錄，沿用既有本機 TTS 並保存精確旁白段落邊界。"""
import argparse
import json
import os
import shlex
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import wave

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'ops/podcast'))


def environment():
    for line in (ROOT/'data/podcast/provider.env').read_text().splitlines():
        if not line or line.startswith('#') or '=' not in line:continue
        key,value=line.split('=',1)
        if key.startswith('STUDYDY_') and 'TOKEN' not in key:
            values=shlex.split(value);os.environ[key]=values[0] if values else ''


def prepare(directory):
    directory=directory.resolve();directory.mkdir(parents=True,exist_ok=True)
    for name in ('scene.tsx','project.ts','runner.js','editor.html','style.css','vite.config.mjs','package.json','package-lock.json'):
        shutil.copyfile(Path(__file__).parent/name,directory/name)
    texts=['第一步，用戶端送出 SYN，提出連線要求。','第二步，伺服器回覆 SYN 加 ACK，同步並確認。','第三步，用戶端送出 ACK，伺服器收到後完成連線建立。']
    environment();output=directory/'narration.wav'
    durations=[];frames=[]
    with tempfile.TemporaryDirectory(prefix='studydy-motion-') as temporary:
        for index,text in enumerate(texts):
            clip=Path(temporary)/f'{index}.wav'
            body={'purpose':'podcast','script':{'segments':[{'turns':[{'speaker':'host','text':text}]}]}}
            subprocess.run([os.environ['STUDYDY_PODCAST_TTS_PYTHON'],str(ROOT/'ops/podcast/synthesize.py'),str(clip)],input=json.dumps(body,ensure_ascii=False),text=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=540)
            with wave.open(str(clip),'rb') as audio:
                if (audio.getnchannels(),audio.getsampwidth(),audio.getframerate())!=(1,2,24000):raise ValueError('INVALID_AUDIO')
                data=audio.readframes(audio.getnframes())
            if index+1<len(texts):data+=b'\0\0'*5760
            frames.append(data);durations.append(len(data)/2/24000)
    with wave.open(str(output),'wb') as audio:
        audio.setparams((1,2,24000,0,'NONE','not compressed'));audio.writeframes(b''.join(frames))
    (directory/'timing.json').write_text(json.dumps({'durations':durations,'basis':'actual mastered step sample boundaries at 24000Hz'}))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('directory',type=Path);args=parser.parse_args();prepare(args.directory)
