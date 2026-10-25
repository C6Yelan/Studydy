"""從既存音訊與對齊講稿匯出，不重新生成或猜測時間。"""
from io import BytesIO
import av
from .export_documents import md_text, source_text


def mp3(source):
    output = BytesIO()
    with av.open(source, mode='r') as input, av.open(output, mode='w', format='mp3') as target:
        stream = target.add_stream('libmp3lame', rate=44100)
        stream.bit_rate = 192000
        resampler = av.AudioResampler(format='s16p', layout='stereo', rate=44100)
        for frame in input.decode(audio=0):
            for converted in resampler.resample(frame):
                for packet in stream.encode(converted):
                    target.mux(packet)
        for converted in resampler.resample(None):
            for packet in stream.encode(converted):
                target.mux(packet)
        for packet in stream.encode(None):
            target.mux(packet)
    return output.getvalue()


def timestamp(seconds):
    seconds = int(seconds)
    return f'{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}'


def transcript(view, index, timeline):
    episode = view['episodes'][index]
    hosts = episode.get('hosts') or {}
    lines = [f'# {md_text(view["name"])}：第 {index + 1} 集', '',
             md_text(episode.get('title') or ''), '', '## 本集重點', '']
    for claim in episode['claims']:
        lines.extend([f'### {md_text(claim["label"])}', '', md_text(claim['text']), ''])
        lines.extend(f'- 來源：{md_text(source_text(e))}' for e in claim['evidence'])
        lines.append('')
    lines += ['## 逐字稿', '']
    for segment in timeline['segments']:
        lines.extend([f'### {timestamp(segment["start"])}–{timestamp(segment["end"])}', ''])
        for turn in segment['turns']:
            name = hosts.get(turn['speaker']) or ('主持人' if turn['speaker'] == 'host' else '來賓')
            lines.extend([f'**{md_text(name)}**：{md_text(turn["text"])}', ''])
    return '\n'.join(lines)
