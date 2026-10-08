"""Podcast 音訊接點與語速處理；依聲音特徵及發音量，不依特定講稿。"""
import re
import numpy as np
from spoken_input import lexicon, letter_phones


def _islands(samples, rate):
    window = max(1, round(rate * .01))
    rms = np.array([np.sqrt(np.mean(samples[i:i+window] ** 2))
                    for i in range(0, len(samples), window)])
    threshold = max(10 ** (-55 / 20), float(rms.max(initial=0)) * .02)
    indices = np.flatnonzero(rms > threshold)
    groups = []
    for index in indices:
        # 20ms 以下的短空隙仍算同一個聲音，避免把子音拆成假雜音。
        if groups and index - groups[-1][-1] <= 3:
            groups[-1].append(int(index))
        else:
            groups.append([int(index)])
    return [(g[0] * window, min(len(samples), (g[-1] + 1) * window)) for g in groups]


def clean_onset(samples, rate):
    """僅辨識句首短促、非週期脈衝；有聲語氣詞或緊接母音的子音保留。"""
    groups = _islands(samples, rate)
    report = {'removed_samples': 0, 'decision': 'preserved'}
    if len(groups) < 2:
        return samples.copy(), report
    first, following = groups[:2]
    a, b = first
    c, d = following
    if not (a <= .15 * rate and b - a <= .05 * rate
            and c - b >= .12 * rate and d - c >= .08 * rate and c <= .4 * rate):
        return samples.copy(), report
    pulse = samples[a:b].astype('float64')
    pulse -= pulse.mean()
    energy = float(np.dot(pulse, pulse))
    lags = range(max(1, round(rate / 400)), min(round(rate / 80), len(pulse) // 2) + 1)
    voiced = max((float(np.dot(pulse[:-lag], pulse[lag:])) / energy
                  for lag in lags), default=1.0) if energy else 0.0
    report['voicing_score'] = voiced
    if voiced >= .35:
        report['decision'] = 'voiced_prefix_preserved'
        return samples.copy(), report
    # 與持續語音起點保持 100ms 距離；不從說話中的區域取樣刪除。
    cut = max(0, c - round(.10 * rate))
    report.update({'removed_samples': cut, 'decision': 'isolated_onset_transient',
                   'island_samples': [a, b], 'following_speech_samples': [c, d]})
    return samples[cut:].copy(), report


def soften_edges(samples, rate):
    result = samples.copy()
    lead = min(round(.010 * rate), len(result) // 4)
    tail = min(round(.020 * rate), len(result) // 4)
    if lead:
        result[:lead] *= np.sin(np.linspace(0, np.pi / 2, lead)) ** 2
    if tail:
        result[-tail:] *= np.cos(np.linspace(0, np.pi / 2, tail)) ** 2
    if len(result):
        result[0] = result[-1] = 0
    return result


def spoken_units(text):
    """中文以字、英文以 CMU 音節估計；未知發音不猜測，交回人工檢查。"""
    import wordninja
    units = len(re.findall(r'[\u4e00-\u9fff]', text))
    unknown = []
    for word in re.findall(r'[A-Za-z]+', text):
        if len(word) == 1 and word.isupper():
            phones = letter_phones(word)
        elif word.lower() in lexicon():
            phones = lexicon()[word.lower()][0]
        elif word.lower().endswith('s') and word.lower()[:-1] in lexicon() and lexicon()[word.lower()[:-1]][0][-1] not in ('S', 'Z', 'SH', 'ZH', 'CH', 'JH'):
            # 常規複數尾音不另成音節，避免把詞尾 s 算成字母 S。
            phones = lexicon()[word.lower()[:-1]][0]
        else:
            pieces = wordninja.split(word.lower())
            if len(pieces) < 2 or not all(len(p) > 1 and p in lexicon() for p in pieces):
                unknown.append(word)
                continue
            phones = [phone for piece in pieces for phone in lexicon()[piece][0]]
        units += sum(any(c.isdigit() for c in phone) for phone in phones)
    return units, unknown


def pace_plan(items, rate):
    """把長句的語速小幅拉向本段中位數；短語氣詞、未知發音維持原樣。"""
    estimates = []
    for item in items:
        units, unknown = spoken_units(item['text'])
        islands = _islands(item['audio'], rate)
        seconds = (islands[-1][1] - islands[0][0]) / rate if islands else 0
        eligible = units >= 12 and seconds >= 2 and not unknown
        estimates.append({'spoken_units': units, 'unknown_words': unknown,
                          'speech_span_seconds': seconds, 'eligible': eligible,
                          'estimated_rate': units / seconds if eligible else None})
    rates = [x['estimated_rate'] for x in estimates if x['eligible']]
    target = float(np.median(rates)) if len(rates) >= 3 else None
    for item in estimates:
        tempo = 1.0
        if target and item['eligible']:
            # 僅走向中位數的一半，並限制在 ±10%，保留正常的語氣差異。
            proposed = float(np.clip((1 + target / item['estimated_rate']) / 2, .90, 1.10))
            if abs(proposed - 1) >= .03:
                tempo = proposed
        item['tempo'] = tempo
    return {'target_rate': target, 'sentences': estimates}


def assemble(items, rate):
    """直接使用模型片段的已知邊界；不從已發布 WAV 推測句界。"""
    import subprocess
    import tempfile
    from pathlib import Path
    import soundfile as sf
    from audio_mastering import edge_silence, ffmpeg
    if not items:
        raise ValueError('PODCAST_AUDIO_INVALID')
    cleaned = [{**item, 'audio': clean_onset(item['audio'], rate)[0]} for item in items]
    plan = pace_plan(cleaned, rate)
    rendered = []
    with tempfile.TemporaryDirectory(prefix='studydy-podcast-pace-') as temporary:
        directory = Path(temporary)
        for item, estimate in zip(cleaned, plan['sentences']):
            audio = item['audio']
            if estimate['tempo'] != 1:
                sf.write(directory / 'input.wav', audio, rate, subtype='FLOAT')
                result = subprocess.run([ffmpeg(), '-v', 'error', '-y', '-i', str(directory / 'input.wav'),
                    '-af', f'atempo={estimate["tempo"]:.9f}', '-c:a', 'pcm_f32le', str(directory / 'output.wav')],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False)
                if result.returncode:
                    raise ValueError('PODCAST_AUDIO_INVALID')
                audio, actual_rate = sf.read(directory / 'output.wav', dtype='float32')
                if actual_rate != rate or not len(audio) or not np.isfinite(audio).all():
                    raise ValueError('PODCAST_AUDIO_INVALID')
            rendered.append(soften_edges(audio, rate))
    parts = []
    for index, audio in enumerate(rendered):
        parts.append(audio)
        if index + 1 < len(rendered):
            current, following = cleaned[index:index+2]
            pause = .32 if (current['speaker'] != following['speaker'] or current['beat'] != following['beat']) else .26 if current['ending'] in ('？', '?') else .24
            silent = max(0, round(pause * rate) - edge_silence(audio)[1] - edge_silence(rendered[index+1])[0])
            parts.append(np.zeros(silent, dtype='float32'))
    return np.concatenate(parts)
