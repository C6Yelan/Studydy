import { useEffect, useRef, useState } from 'react';
import { AnswerAudio } from './AnswerAudio';
import type { VoiceSentence } from './types';

type Props = {
  src: string | null; sentences?: VoiceSentence[]; status: string;
  activeAudio: HTMLMediaElement | null;
  register: (element: HTMLAudioElement) => void; onPlay: (element: HTMLAudioElement) => void;
  autoPlay: boolean; onAutoPlayAttempt: () => void;
};

export function VoiceAnswerAudio(props: Props) {
  const [started, setStarted] = useState(false), [finished, setFinished] = useState(false);
  const whole = props.src && (!props.sentences?.length || props.status === 'ready' && (!started || finished));
  // 串播進行中不切成整段，避免中途重播；完成後仍用原本的完整音訊播放軸。
  if (whole) return <AnswerAudio src={props.src!} register={props.register} onPlay={props.onPlay}
    autoPlay={props.autoPlay} onAutoPlayAttempt={props.onAutoPlayAttempt}/>;
  if (!props.sentences?.length) return null;
  return <SentenceAudio {...props} sentences={props.sentences} onStarted={() => setStarted(true)} onFinished={() => setFinished(true)}/>;
}

function SentenceAudio({ sentences, status, activeAudio, autoPlay, register, ...actions }: Props & {
  sentences: VoiceSentence[]; onStarted: () => void; onFinished: () => void;
}) {
  const audio = useRef<HTMLAudioElement>(null), alive = useRef(false);
  const [index, setIndex] = useState(0), [desired, setDesired] = useState(false), [playing, setPlaying] = useState(false);
  const [error, setError] = useState(''), [gesture, setGesture] = useState(false);
  const wants = useRef(false), started = useRef(false), switching = useRef(false), autoPending = useRef(false);
  const callbacks = useRef(actions); callbacks.current = actions;
  const current = sentences[index];
  const source = current?.status === 'ready' ? current.audio_url : null;
  const stop = () => {
    wants.current = false; setDesired(false); audio.current?.pause();
    if (autoPending.current) { autoPending.current = false; callbacks.current.onAutoPlayAttempt(); }
  };
  useEffect(() => {
    alive.current = true;
    const element = audio.current;
    return () => { alive.current = false; wants.current = false; if (element) { element.pause(); element.removeAttribute('src'); element.load(); } };
  }, []);
  useEffect(() => {
    if (autoPlay) { autoPending.current = true; wants.current = true; setDesired(true); }
    else if (autoPending.current) stop();
  }, [autoPlay]);
  useEffect(() => { if (started.current && activeAudio !== audio.current) stop(); }, [activeAudio]);
  useEffect(() => { if (status === 'failed' || status === 'cancelled') stop(); }, [status]);
  useEffect(() => { if (current?.status === 'failed' || current?.status === 'cancelled') stop(); }, [index, current?.status]);
  useEffect(() => {
    if (status === 'ready' && index >= sentences.length) { stop(); callbacks.current.onFinished(); }
  }, [status, index, sentences.length]);
  useEffect(() => {
    const element = audio.current;
    if (!desired || !source || !element) return;
    let cancelled = false;
    if (element.error) element.load();
    void element.play().then(() => {
      if (cancelled || !alive.current) { if (!wants.current || !alive.current) element.pause(); return; }
      if (!wants.current) { element.pause(); return; }
      if (autoPending.current) { autoPending.current = false; callbacks.current.onAutoPlayAttempt(); }
    }).catch(failure => {
      if (cancelled || !alive.current || failure?.name === 'AbortError') return;
      stop();
      if (failure?.name === 'NotAllowedError') setGesture(true);
      else setError('音訊暫時無法播放，請重試。');
    });
    return () => { cancelled = true; if (!wants.current) element.pause(); };
  }, [source, desired]);
  const unavailable = !sentences.some(sentence => sentence.status === 'ready');
  return <div className="answer-audio voice-sentence-audio" aria-live="off">
    <audio preload="metadata" src={source ?? undefined} ref={element => { audio.current = element; if (element) register(element); }}
      onPlay={event => {
        if (!alive.current || !wants.current) { event.currentTarget.pause(); return; }
        switching.current = false; started.current = true; setPlaying(true); setError(''); setGesture(false);
        callbacks.current.onStarted(); callbacks.current.onPlay(event.currentTarget);
      }}
      onPause={event => { setPlaying(false); if (!event.currentTarget.ended && !switching.current) stop(); }}
      onEnded={() => { switching.current = true; setPlaying(false); setIndex(value => value + 1); }}
      onError={() => { stop(); setError('音訊暫時無法讀取，請重試。'); }}/>
    <button type="button" className="answer-audio-play" disabled={unavailable && !desired}
      aria-label={desired ? '暫停回答語音' : '播放回答語音'} onClick={() => {
        if (desired) { stop(); return; }
        if (index >= sentences.length || current?.status === 'failed' || current?.status === 'cancelled') { switching.current = true; setIndex(0); }
        setError(''); setGesture(false); wants.current = true; setDesired(true);
      }}>
      <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">{desired ? <><rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/></> : <path d="m7 4 14 8-14 8Z"/>}</svg>
      <span>{desired ? '暫停' : unavailable ? '語音準備中' : '聽回答'}</span>
    </button>
    {playing && <span className="voice-playback-state" role="status">播放回答中</span>}
    {desired && !source && !['failed','cancelled'].includes(status) && <p className="voice-sentence-wait" role="status">正在準備後續語音…</p>}
    {gesture && <p className="audio-gesture-note" role="status">語音已可播放，點擊播放。</p>}
    {error && <p role="alert">{error}</p>}
  </div>;
}
