"""Podcast 音訊組裝政策；不改文字、不移動已發布媒體的時間軸。"""
import json
import math
import os
import shutil
import subprocess

POLICY = 'podcast-mastering/v1'
RATE = 24000


def trim_edges(samples, rate=RATE):
    # 20ms RMS 視窗、40ms 保護區，只去除兩端安靜區，內部停頓原樣保留。
    if not len(samples) or any(not math.isfinite(float(x)) for x in samples):
        raise ValueError('PODCAST_AUDIO_INVALID')
    window = max(1, round(rate * .02)); margin = round(rate * .04)
    active = [i for i in range(0, len(samples), window)
              if sum(float(x)**2 for x in samples[i:i+window]) / len(samples[i:i+window]) > 10**(-50/10)]
    if not active: raise ValueError('PODCAST_AUDIO_INVALID')
    return samples[max(0, active[0]-margin):min(len(samples), active[-1]+window+margin)]


def edge_silence(samples):
    threshold=10**(-50/20)
    counts=[]
    for values in (samples,samples[::-1]):
        count=0
        for sample in values:
            if abs(float(sample))>threshold:break
            count+=1
        counts.append(count)
    return tuple(counts)


def pause_seconds(text, *, next_speaker=False, next_beat=False, language_join=False):
    ending = text.rstrip()
    base = .22 if ending.endswith(('。','！','？','.','!','?')) else .12 if ending.endswith(('，',',','、','；',';',':','：')) else .08 if language_join else .12
    # 同一邊界只取一個停頓，不把句號、換人與換 beat 相加；語言切換不等於句尾。
    return max(base, .32 if next_beat else .22 if next_speaker else 0)


def ffmpeg():
    executable = shutil.which('ffmpeg')
    if executable:return executable
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        # 沿用已部署的 CPU renderer FFmpeg，不在 GPU 環境另外裝一套。
        python = os.environ.get('STUDYDY_VIDEO_PYTHON')
        if not python:raise ValueError('PODCAST_AUDIO_INVALID') from None
        result=subprocess.run([python,'-c','import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())'],
            capture_output=True,text=True,timeout=15,check=True)
        return result.stdout.strip()


def _run(source, filters, output=None):
    args=[ffmpeg(),'-hide_banner','-nostdin','-y','-i',str(source),'-af',filters]
    args += ['-ar',str(RATE),'-ac','1','-c:a','pcm_s16le',str(output)] if output else ['-f','null','-']
    result=subprocess.run(args,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,timeout=120,check=False)
    if result.returncode:raise ValueError('PODCAST_AUDIO_INVALID')
    try:
        value=json.JSONDecoder().raw_decode(result.stderr[result.stderr.rindex('{'):])[0]
        if any(not math.isfinite(float(value[k])) for k in ('input_i','input_tp','input_lra','input_thresh')):raise ValueError()
        return value
    except (ValueError,KeyError):raise ValueError('PODCAST_AUDIO_INVALID') from None


def master(source, output):
    measured=_run(source,'loudnorm=I=-19:TP=-2:LRA=11:print_format=json')
    second='loudnorm=I=-19:TP=-2:LRA=11:linear=true:print_format=json'
    for key,param in [('input_i','measured_I'),('input_tp','measured_TP'),('input_lra','measured_LRA'),('input_thresh','measured_thresh')]:
        second+=f':{param}={float(measured[key])}'
    second+=f":offset={float(measured['target_offset'])}"
    _run(source,second,output)
    final=_run(output,'loudnorm=I=-19:TP=-2:LRA=11:print_format=json')
    # 量測最後 24kHz PCM；不可用第一輪估計冒充成品結果。
    if abs(float(final['input_i'])+19)>1 or float(final['input_tp'])>-1.9:
        raise ValueError('PODCAST_AUDIO_INVALID')
    return {'policy':POLICY,'integrated_lufs':float(final['input_i']),
            'true_peak_dbtp':float(final['input_tp']),'loudness_range_lu':float(final['input_lra'])}
