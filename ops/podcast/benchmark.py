"""無模型的 1080p30/60 paired benchmark；只生成可重建合成素材。"""
import argparse
from array import array
from hashlib import sha256
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
import wave

PROJECT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(PROJECT/'backend/src'))
from runtime.podcast_video_plan import timeline_for_video
import unicodedata

def normalized(text):
    return "".join(c for c in unicodedata.normalize("NFKC",text).casefold() if c.isalnum())
from runtime.podcast_video_render import draw_frame, layouts, emphasis_layouts


def fixture(duration, animated):
    texts=['先確認來源條件，再比較兩種結果。','相同條件保留，差異才有明確意義。','最後回到問題，判斷適用的情境。']
    claims=[{'claim_id':f'claim-{i}','concept_id':f'concept-{i}','evidence':[{'evidence_id':f'e-{i}'}]} for i in range(3)]
    episode={'delivery':'solo','claims':claims,'script':{'provider':'synthetic-benchmark','segments':[
        {'claim_id':c['claim_id'],'turns':[{'speaker':'host','text':t}]} for c,t in zip(claims,texts)]},
        'audio':{'sha256':'','duration_seconds':duration}}
    cues=[{'title':f'焦點 {i+1}','parts':[{'turn_index':i,'text':t}]} for i,t in enumerate(texts)]
    starts=[i*duration/3 for i in range(3)]
    anchors=[{'script_offset':0,'text':normalized(t)[:8],'audio_start':starts[i],
              'boundary_script_offset':0,'boundary_text':normalized(t)[:8],'boundary_audio_start':starts[i]} for i,t in enumerate(texts)]
    alignment={'starts':starts,'anchors':anchors,'duration':duration,'method':'synthetic-only','producer':'benchmark'}
    elements=[{'kind':'box','cue_index':0,'text':'必要條件','x':140,'y':300,'w':520,'h':210,'size':52,'color':'teal','filled':True},
              {'kind':'box','cue_index':1,'text':'比較結果','x':1140,'y':300,'w':520,'h':210,'size':52,'color':'blue','filled':True},
              {'kind':'arrow','cue_index':1,'text':'','x':660,'y':405,'w':480,'h':0,'size':28,'color':'ink','filled':False}]
    page={'title':'相同條件下比較結果','start_cue':0,'end_cue':2,'elements':elements,
          'reveal':[{'start_cue':1,'elements':[1,2]}] if animated else [],
          'emphasis':[{'element_index':2,'start_cue':1,'end_cue':1,'kind':'trace','quote':''}] if animated else []}
    return {'podcast_id':'synthetic','episode_index':0,'episode':episode,'source_resolver':'/synthetic/evidence',
            'cues':cues,'alignment':alignment,'plan':{'schema':'podcast-storyboard/v2','pages':[page]}}


def main():
    import imageio_ffmpeg
    from PIL import Image, ImageChops, ImageStat
    parser=argparse.ArgumentParser();parser.add_argument('output',type=Path);parser.add_argument('--repeats',type=int,default=3)
    args=parser.parse_args();root=args.output;root.mkdir(parents=True,exist_ok=True)
    ffmpeg=imageio_ffmpeg.get_ffmpeg_exe();report={'synthetic':True,'production_fps':60,'cases':[]}
    for name,duration,animated in [('static',6,False),('reveal-trace',9,True),('longer',24,True)]:
        request=fixture(duration,animated);wav=root/f'{name}.wav'
        with wave.open(str(wav),'wb') as w:
            w.setparams((1,2,24000,0,'NONE','not compressed'))
            w.writeframes(array('h',(round(2000*math.sin(i*math.tau*330/24000)) for i in range(duration*24000))).tobytes())
        request['episode']['audio']['sha256']=sha256(wav.read_bytes()).hexdigest()
        samples=[];outputs={}
        for repeat in range(args.repeats):
            for fps in ([60,30] if repeat%2==0 else [30,60]):
                stem=root/f'{name}-{fps}-{repeat}';input_path=stem.with_suffix('.request.json');output=stem.with_suffix('.json')
                input_path.write_text(json.dumps({**request,'benchmark_fps':fps},ensure_ascii=False))
                timing=stem.with_suffix('.time')
                subprocess.run(['/usr/bin/time','-f','%U %S %M','-o',str(timing),sys.executable,
                    '-m','runtime.podcast_video_render',str(input_path),str(wav),str(output)],check=True,
                    env={**__import__('os').environ,'PYTHONPATH':str(PROJECT/'backend/src')})
                value=json.loads(output.read_text());cpu_user,cpu_system,rss=map(float,timing.read_text().split())
                video=stem.with_suffix('.mp4');outputs[fps]=video
                decoded=subprocess.run([ffmpeg,'-v','error','-i',str(video),'-f','null','-'],capture_output=True,timeout=120)
                if decoded.returncode or decoded.stderr:raise RuntimeError('benchmark decode failed')
                samples.append({'fps':fps,'repeat':repeat,'wall_seconds':value['render_seconds'],
                    'cpu_seconds':cpu_user+cpu_system,'peak_rss_kib':rss,'bytes':video.stat().st_size,'full_decode':True})
        timeline=timeline_for_video('synthetic',0,request['episode'],request['cues'],request['alignment'],'/synthetic/evidence')
        text_layouts=layouts(request['plan']);marks=emphasis_layouts(request['plan'],text_layouts)
        times=[.5,duration/3,duration/3+.1,duration/3+.4,duration/3+.8,2*duration/3,duration-.1]
        comparisons=[];thumbnails=[]
        for t in times:
            decoded=[]
            for fps in (60,30):
                result=subprocess.run([ffmpeg,'-v','error','-ss',str(t),'-i',str(outputs[fps]),'-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','pipe:1'],capture_output=True,check=True,timeout=30)
                decoded.append(Image.frombytes('RGB',(1920,1080),result.stdout))
            expected=draw_frame(t,timeline,request['plan'],text_layouts,marks)
            error=[sum(ImageStat.Stat(ImageChops.difference(expected,frame)).mean)/3 for frame in decoded]
            comparisons.append({'time':t,'mean_pixel_error_60':error[0],'mean_pixel_error_30':error[1]})
            for frame in decoded:thumbnails.append(frame.resize((640,360)))
        sheet=Image.new('RGB',(1280,360*len(times)),'white')
        for i,frame in enumerate(thumbnails):sheet.paste(frame,((i%2)*640,(i//2)*360))
        sheet.save(root/f'{name}-comparison.png')
        medians={str(fps):statistics.median(s['wall_seconds'] for s in samples if s['fps']==fps) for fps in (60,30)}
        report['cases'].append({'name':name,'duration':duration,'samples':samples,'wall_medians':medians,
            'speedup':medians['60']/medians['30'],'frame_comparisons':comparisons})
        (root/'summary.json').write_text(json.dumps(report,indent=2))
        print(name,medians,flush=True)
    report['decision']='Keep 60fps until visual motion and browser synchronization review also pass; speed alone is insufficient.'
    (root/'summary.json').write_text(json.dumps(report,indent=2))


if __name__=='__main__':main()
