import { useRef, useState } from 'react';
import { formatTime } from '../podcasts/PodcastPlayer';

export function AnswerAudio({ src, register, onPlay }: {
  src: string; register: (element: HTMLAudioElement) => void; onPlay: (element: HTMLAudioElement) => void;
}) {
  const audio = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false), [duration, setDuration] = useState(0), [time, setTime] = useState(0);
  const [error, setError] = useState('');
  return <div className="answer-audio" aria-live="off">
    <audio preload="metadata" src={src} ref={element => { audio.current = element; if (element) register(element); }}
      onLoadedMetadata={e => setDuration(e.currentTarget.duration)} onTimeUpdate={e => setTime(e.currentTarget.currentTime)}
      onPlay={e => { setPlaying(true); setError(''); onPlay(e.currentTarget); }} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)}
      onError={() => setError('音訊暫時無法讀取。')} />
    <button type="button" className="answer-audio-play" aria-label={playing ? '暫停回答語音' : '播放回答語音'} onClick={() => {
      const element = audio.current; if (!element) return;
      if (!element.paused) element.pause();
      else { if (element.error) element.load(); void element.play().catch(e => { if (e?.name !== 'AbortError') setError('暫時無法播放，請重試。'); }); }
    }}><svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">{playing ? <><rect x="6" y="4" width="4" height="16" rx="1" /><rect x="14" y="4" width="4" height="16" rx="1" /></> : <path d="m7 4 14 8-14 8Z" />}</svg></button>
    <span>聽回答</span>
    <input type="range" aria-label="回答語音進度" min={0} max={duration || 1} step={0.1} value={Math.min(time, duration || 1)} disabled={!duration} onChange={e => { if (audio.current) audio.current.currentTime = Number(e.target.value); }} />
    <small>{formatTime(time)}{duration > 0 && ` / ${formatTime(duration)}`}</small>
    {error && <p role="alert">{error}</p>}
  </div>;
}
