"""固定畫布、字型與圖形白名單的 CPU 影片繪製器；原音訊是唯一時間基準。"""
from functools import lru_cache
from hashlib import sha256
import json
import math
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import time

from .podcast_video_plan import validate_plan, timeline_for_video, validate_visual_timing, visual_window

PALETTE = {'ink':'#26354a','teal':'#168c86','blue':'#2973be','orange':'#bf651f','muted':'#74818c'}
BACKGROUND = '#fafbf9'
MAX_VIDEO_BYTES = 100*1024*1024


def ensure_space(directory):
    floor = int(os.environ.get('STUDYDY_VIDEO_MIN_FREE_BYTES', str(2*1024**3)))
    paths = [Path(directory)]
    if os.environ.get('STUDYDY_VIDEO_HOST_FREE_PATH'):
        paths.append(Path(os.environ['STUDYDY_VIDEO_HOST_FREE_PATH']))
    if any(not p.exists() or shutil.disk_usage(p).free < floor for p in paths):
        raise ValueError('VIDEO_DISK_SPACE_LOW')


@lru_cache
def font(size):
    from PIL import ImageFont
    return ImageFont.truetype(os.environ.get('STUDYDY_VIDEO_FONT',
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'), size)


@lru_cache(maxsize=512)
def lines(text, size, width):
    result=[]; line=''
    # 縮寫、單位與識別符能放入一行時整詞移動，不在 TCP／MB/s 中間換行。
    for token in re.findall(r'[A-Za-z0-9][A-Za-z0-9_./+-]*|\n|.', text):
        if token == '\n':
            result.append(line); line=''; continue
        if line and font(size).getlength(line+token)>width:
            result.append(line); line=''
        for char in token:
            if line and font(size).getlength(line+char)>width:
                result.append(line); line=''
            line+=char
    result.append(line)
    return result


def layouts(plan):
    """超出文字區或互相覆蓋就拒絕，不以縮小字體掩蓋分鏡問題。"""
    result=[];errors=[]
    for page_index,page in enumerate(plan['pages']):
        items=[];bounds=[]
        for index,e in enumerate(page['elements']):
            if not e['text']:items.append([]);continue
            padding=16 if e['kind'] in ('box','circle') else 0
            width=e['w']-2*padding
            if width<=0:
                errors.append(f'page={page_index},element={index},width must exceed {2*padding}')
                items.append([]);continue
            text_lines=lines(e['text'],e['size'],width)
            height=len(text_lines)*(e['size']+10)
            if height>e['h']-2*padding:
                errors.append(f'page={page_index},element={index},height={e["h"]}; {len(text_lines)} wrapped lines at size={e["size"]} require height>={height+2*padding}; move or shorten text if canvas space is insufficient')
                items.append([]);continue
            y=e['y']+(e['h']-height)/2 if e['kind'] in ('box','circle') else e['y']
            rendered=[]
            for text in text_lines:
                length=font(e['size']).getlength(text)
                x=e['x']+(e['w']-length)/2 if e['kind'] in ('box','circle') else e['x']
                rendered.append((x,y,text))
                bounds.append((index,x,y,x+length,y+e['size']+5))
                y+=e['size']+10
            items.append(rendered)
        for i,a in enumerate(bounds):
            for b in bounds[i+1:]:
                if a[0]!=b[0] and max(a[1],b[1])<min(a[3],b[3]) and max(a[2],b[2])<min(a[4],b[4]):
                    errors.append(f'page={page_index},elements={a[0]},{b[0]},text overlap; rendered bounds={tuple(round(v,1) for v in a[1:])} and {tuple(round(v,1) for v in b[1:])}; separate or shorten these labels; revealed elements remain visible until the page ends')
        result.append(items)
    if errors:raise ValueError('VIDEO_LAYOUT_INVALID:'+'; '.join(dict.fromkeys(errors)))
    return result


def emphasis_layouts(plan,text_layouts):
    result=[];errors=[]
    for page_index,page in enumerate(plan['pages']):
        bounds=[]
        for mark_index,mark in enumerate(page.get('emphasis',[])):
            e=page['elements'][mark['element_index']]
            if mark['kind']=='trace':bounds.append((e['x'],e['y'],e['x']+e['w'],e['y']+e['h']));continue
            if not mark['quote'] and e['kind'] in ('box','circle'):
                bounds.append((e['x'],e['y'],e['x']+e['w'],e['y']+e['h']));continue
            boxes=[]
            for x,y,text in text_layouts[page_index][mark['element_index']]:
                quote=mark['quote']
                if quote and quote not in text:continue
                if quote:x+=font(e['size']).getlength(text[:text.index(quote)])
                box=font(e['size']).getbbox(quote or text)
                boxes.append((x+box[0],y+box[1],x+box[2],y+box[3]))
            if not boxes:
                errors.append(f'page={page_index},emphasis={mark_index}, quote must fit within one rendered line; choose a shorter exact phrase')
                bounds.append(None);continue
            bounds.append((min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[2] for b in boxes),max(b[3] for b in boxes)))
        result.append(bounds)
    if errors:raise ValueError('VIDEO_LAYOUT_INVALID:'+'; '.join(errors))
    return result


def emphasis_opacity(t,start,end):
    """只以實測 cue 時間控制出現／退場；seek 後也不留下前一個標記。"""
    if not start<=t<end:return 0
    fade=min(.25,(end-start)/3)
    value=min(1,(t-start)/fade,(end-t)/fade)
    return value*value*(3-2*value)


def trace_path(draw,points,progress,color,width):
    lengths=[math.dist(a,b) for a,b in zip(points,points[1:])]
    remaining=sum(lengths)*progress;visible=[points[0]]
    for a,b,length in zip(points,points[1:],lengths):
        if remaining>=length:
            visible.append(b);remaining-=length
        else:
            fraction=remaining/length if length else 0
            visible.append((a[0]+(b[0]-a[0])*fraction,a[1]+(b[1]-a[1])*fraction));break
    if len(visible)>1:draw.line(visible,fill=color,width=width,joint='curve')


def draw_emphasis(draw,t,timeline,page,bounds,element_states=None):
    for mark,box in zip(page.get('emphasis',[]),bounds):
        start,end=visual_window(timeline,mark['start_cue'],mark['end_cue'],mark.get('caption_index'))
        opacity=emphasis_opacity(t,start,end)
        if element_states is not None:
            state=element_states[mark['element_index']]
            # v3 的 relation 已由同一時鐘 draw-on／推進，不能再疊第二顆 trace 指示。
            if mark['kind']=='trace':continue
            opacity*=state['entrance']*state['focus']
        if not opacity:continue
        elapsed=t-start;progress=min(1,elapsed/min(.45,(end-start)/2))
        color=(218,115,31,round(255*opacity))
        x0,y0,x1,y1=box
        if mark['kind']=='underline':
            draw.line((x0,y1+6,x0+(x1-x0)*progress,y1+6),fill=color,width=5)
        elif mark['kind']=='outline':
            x0-=7;y0-=7;x1+=7;y1+=7
            target=page['elements'][mark['element_index']]
            if target['kind']=='circle' and not mark['quote']:
                cx=(x0+x1)/2;cy=(y0+y1)/2
                points=[(cx+(x1-x0)/2*math.cos(i*math.tau/48),cy+(y1-y0)/2*math.sin(i*math.tau/48)) for i in range(49)]
            else:points=[(x0,y0),(x1,y0),(x1,y1),(x0,y1),(x0,y0)]
            trace_path(draw,points,progress,color,5)
        else:
            progress=min(1,elapsed/min(.9,(end-start)/2))
            x=x0+(x1-x0)*progress;y=y0+(y1-y0)*progress
            draw.line((x0,y0,x,y),fill=color,width=5)
            if progress<1:draw.ellipse((x-7,y-7,x+7,y+7),fill=color,outline=(255,255,255,round(255*opacity)),width=2)


def draw_scene(t, timeline, plan, page_index, text_layouts, mark_layouts):
    from PIL import Image, ImageDraw, ImageColor
    page=plan['pages'][page_index]
    states=None
    if 'motion' in page:
        from .podcast_video_motion import scene_state
        groups=scene_state(t,timeline,page)
        states={j:state for group,state in zip(page['motion']['groups'],groups) for j in group['elements']}
    image=Image.new('RGB',(1920,1080),BACKGROUND);d=ImageDraw.Draw(image,'RGBA')
    d.text((100,35),'PODCAST  /  圖解講解',font=font(26),fill=PALETTE['teal'])
    d.text((100,88),page['title'],font=font(54),fill=PALETTE['ink'])
    d.text((1690,106),f'{page_index+1:02} / {len(plan["pages"]):02}',font=font(30),fill=PALETTE['muted'])
    d.line((100,185,1820,185),fill='#d7e0e4',width=2)
    for index,e in enumerate(page['elements']):
        # Reveal 完全由時間推導；往回 seek 不留下未到時機的元素。
        reveal=next((g for g in page.get('reveal',[]) if index in g['elements']),None)
        if reveal and t < visual_window(timeline,reveal['start_cue'],reveal['start_cue'],reveal.get('caption_index'))[0]:continue
        state=states[index] if states is not None else None
        opacity=state['opacity'] if state else 1
        if opacity<=0:continue
        rgb=ImageColor.getrgb(PALETTE[e['color']]);rgba=(*rgb,round(255*opacity))
        area=(e['x'],e['y'],e['x']+e['w'],e['y']+e['h'])
        if state and e['kind']=='text':
            rows=text_layouts[page_index][index]
            if rows:
                area_text=(e['x'],rows[0][1],max(x+font(e['size']).getlength(text) for x,y,text in rows),rows[-1][1]+e['size']+10)
                d.rectangle(area_text,fill=(*rgb,round(18*state['focus']*state['entrance'])))
        if e['kind'] in ('box','circle'):
            draw=d.rectangle if e['kind']=='box' else d.ellipse
            fill=(*rgb,round(opacity*(32 if e['filled'] else 18*state['focus']))) if state else ((*rgb,32) if e['filled'] else None)
            draw(area,outline=rgba,fill=fill,width=2)
        elif e['kind'] in ('line','arrow'):
            draw_progress=state['draw'] if state else 1
            if draw_progress<=0:continue
            d.line((e['x'],e['y'],e['x']+e['w']*draw_progress,e['y']+e['h']*draw_progress),fill=rgba,width=3)
            if e['kind']=='arrow' and draw_progress==1:
                angle=math.atan2(e['h'],e['w']);x,y=area[2:]
                d.polygon([(x,y),(x-18*math.cos(angle-.5),y-18*math.sin(angle-.5)),
                           (x-18*math.cos(angle+.5),y-18*math.sin(angle+.5))],fill=rgba)
            if state and e['kind']=='arrow' and state['token'] is not None:
                phase=state['token'];x=e['x']+e['w']*phase;y=e['y']+e['h']*phase
                token_alpha=round(255*state['entrance']*min(1,phase*8,(1-phase)*8))
                d.ellipse((x-7,y-7,x+7,y+7),fill=(*ImageColor.getrgb(PALETTE['teal']),token_alpha))
        rows=text_layouts[page_index][index]
        if state and rows and opacity<1:
            # Pillow 的 RGB draw.text 不混合 fill alpha；在文字區明確合成，字體不縮放。
            base=image.crop(area);paint=base.copy();text_draw=ImageDraw.Draw(paint)
            for x,y,text in rows:text_draw.text((x-e['x'],y-e['y']),text,font=font(e['size']),fill=rgb)
            image.paste(Image.blend(base,paint,opacity),(e['x'],e['y']))
        else:
            for x,y,text in rows:d.text((x,y),text,font=font(e['size']),fill=rgba)
    if page.get('emphasis'):
        if mark_layouts is None:mark_layouts=emphasis_layouts(plan,text_layouts)
        draw_emphasis(d,t,timeline,page,mark_layouts[page_index],states)
    return image


def draw_frame(t, timeline, plan, text_layouts, mark_layouts=None):
    from PIL import Image, ImageDraw
    cue_index=next((i for i,s in enumerate(timeline['segments']) if s['start']<=t<s['end']),len(timeline['segments'])-1)
    page_index,page=next((i,p) for i,p in enumerate(plan['pages']) if p['start_cue']<=cue_index<=p['end_cue'])
    image=draw_scene(t,timeline,plan,page_index,text_layouts,mark_layouts)
    if plan.get('schema')=='podcast-storyboard/v3' and page_index:
        from .podcast_video_motion import transition
        blend=transition(t,timeline,page)
        if blend<1:
            boundary=timeline['segments'][page['start_cue']]['start']
            previous=draw_scene(math.nextafter(boundary,-math.inf),timeline,plan,page_index-1,text_layouts,mark_layouts)
            image=Image.blend(previous,image,blend)
    d=ImageDraw.Draw(image,'RGBA')
    d.rectangle((0,852,1920,1080),fill='#edf1f3')
    d.text((100,867),'正在講解',font=font(25),fill=PALETTE['teal'])
    caption=(timeline['segments'][cue_index]['title'] if plan.get('schema') in ('podcast-storyboard/v2','podcast-storyboard/v3') else timeline['segments'][cue_index]['text']).replace('\n',' ')
    for i,line in enumerate(lines(caption,32,1720)):
        d.text((100,900+i*42),line,font=font(32),fill=PALETTE['ink'])
    d.rectangle((0,1068,round(1920*min(t/timeline['duration'],1)),1079),fill=PALETTE['teal'])
    return image


def measure_encoded_audio(path):
    """AAC 解碼後再量測，避免 WAV 達標但壓縮成品過峰。"""
    import imageio_ffmpeg
    result=subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-nostdin','-i',str(path),
        '-vn','-af','loudnorm=I=-19:TP=-2:LRA=11:print_format=json','-f','null','-'],
        stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,timeout=120)
    try:
        if result.returncode:raise ValueError()
        value=json.JSONDecoder().raw_decode(result.stderr[result.stderr.rindex('{'):])[0]
        loudness,peak=float(value['input_i']),float(value['input_tp'])
        if not math.isfinite(loudness) or not math.isfinite(peak) or abs(loudness+19)>1 or peak>-1:raise ValueError()
        return {'integrated_lufs':loudness,'true_peak_dbtp':peak}
    except (KeyError,ValueError):raise ValueError('VIDEO_AUDIO_INVALID') from None


def render(request, audio_path, destination):
    import imageio_ffmpeg
    episode=request['episode']
    if sha256(audio_path.read_bytes()).hexdigest()!=episode['audio']['sha256']:
        raise ValueError('VIDEO_SOURCE_CHANGED')
    plan=validate_plan(request['plan'],request['cues'])
    timeline=timeline_for_video(request['podcast_id'],request['episode_index'],episode,
                               request['cues'],request['alignment'],request['source_resolver'])
    validate_visual_timing(plan,timeline)
    text_layouts=layouts(plan)
    mark_layouts=emphasis_layouts(plan,text_layouts)
    if any(len(lines((s['title'] if plan.get('schema') in ('podcast-storyboard/v2','podcast-storyboard/v3') else s['text']).replace('\n',' '),32,1720))>4 for s in timeline['segments']):
        raise ValueError('VIDEO_LAYOUT_INVALID:caption')
    ensure_space(destination.parent)
    fps=request.get('benchmark_fps',60)
    if type(fps) is not int or fps not in (30,60):raise ValueError('VIDEO_RESULT_INVALID')
    started=time.monotonic()
    command=[imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-loglevel','error','-n','-f','rawvideo',
             '-pix_fmt','rgb24','-s','1920x1080','-r',str(fps),'-i','pipe:0','-i',str(audio_path),
             '-map','0:v:0','-map','1:a:0','-t',str(timeline['duration']),'-c:v','libx264','-preset','fast',
             '-crf','18','-threads','4','-pix_fmt','yuv420p','-c:a','aac','-b:a','192k','-movflags','+faststart',str(destination)]
    with subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL) as process:
        try:
            for n in range(math.ceil(timeline['duration']*fps)):
                if n%600==0:
                    ensure_space(destination.parent)
                    if destination.exists() and destination.stat().st_size>MAX_VIDEO_BYTES:
                        raise ValueError('VIDEO_TOO_LARGE')
                process.stdin.write(draw_frame(n/fps,timeline,plan,text_layouts,mark_layouts).tobytes())
            process.stdin.close()
            if process.wait()!=0:raise ValueError('VIDEO_RENDER_FAILED')
        except BaseException:
            process.kill();process.wait();raise
    if destination.stat().st_size>MAX_VIDEO_BYTES:raise ValueError('VIDEO_TOO_LARGE')
    encoded=measure_encoded_audio(destination) if episode['audio'].get('mastering') else None
    return {**({'encoded_audio':encoded} if encoded else {}),'width':1920,'height':1080,'fps':fps,'duration':timeline['duration'],
            'sha256':sha256(destination.read_bytes()).hexdigest(),'render_seconds':time.monotonic()-started}


if __name__=='__main__':
    request_path,audio_path,result_path=map(Path,sys.argv[1:4])
    try:
        request=json.loads(request_path.read_text())
        if '--validate' in sys.argv[4:]:
            if 'semantic_plan' in request:
                from .podcast_video_layout import compile_layout
                timeline=timeline_for_video(request['podcast_id'],request['episode_index'],request['episode'],
                                           request['cues'],request['alignment'],request['source_resolver'])
                request['plan'] = compile_layout(request['semantic_plan'], request['cues'], timeline, motion='--motion' in sys.argv[4:])
            validate_plan(request['plan'],request['cues']);text_layouts=layouts(request['plan']);emphasis_layouts(request['plan'],text_layouts)
            result={'valid':True,'plan':request['plan']}
        else:result=render(request,audio_path,result_path.with_suffix('.mp4'))
        result_path.write_text(json.dumps(result))
    except Exception as error:
        code=str(error) if isinstance(error,ValueError) and str(error).startswith('VIDEO_') else 'VIDEO_RENDER_FAILED'
        result_path.write_text(json.dumps({'error':code}));raise SystemExit(1)
