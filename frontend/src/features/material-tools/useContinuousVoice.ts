import { useEffect, useRef, useState } from 'react';

// 簡單音量 VAD：瀏覽器回音消除處理播放聲，不另建串流服務。
export function useContinuousVoice({enabled, paused, onRecording, onSpeech, onError}: {
  enabled: boolean; paused: boolean; onRecording: (blob: Blob) => void;
  onSpeech: () => void; onError: (message: string) => void;
}) {
  const latest = useRef({paused, onRecording, onSpeech, onError});
  latest.current = {paused, onRecording, onSpeech, onError};
  const [state, setState] = useState<'off'|'permission'|'listening'|'recording'>('off');
  useEffect(() => {
    if (!enabled) { setState('off'); return; }
    let cancelled = false, media: MediaStream | undefined, context: AudioContext | undefined;
    let recorder: MediaRecorder | undefined, interval: ReturnType<typeof setInterval> | undefined;
    let chunks: Blob[] = [], voiced = false, started = 0, lastVoice = 0, loudSince = 0;
    const stop = (send: boolean) => {
      const current = recorder;
      recorder = undefined;
      if (!current || current.state === 'inactive') return;
      const captured = chunks;
      current.ondataavailable = event => { if (event.data.size) captured.push(event.data); };
      current.onstop = () => {
        if (send && !cancelled && captured.length) latest.current.onRecording(new Blob(captured, {type: current.mimeType}));
      };
      current.stop();
    };
    setState('permission');
    void (async () => {
      try {
        if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder || !window.AudioContext) throw new Error('此瀏覽器無法持續錄音，請使用文字提問。');
        media = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true}});
        if (cancelled) { media.getTracks().forEach(track => track.stop()); return; }
        context = new AudioContext();
        await context.resume();
        if (cancelled) return;
        const source = context.createMediaStreamSource(media), analyser = context.createAnalyser();
        analyser.fftSize = 2048;
        source.connect(analyser);
        const samples = new Float32Array(analyser.fftSize);
        setState('listening');
        interval = setInterval(() => {
          if (latest.current.paused) { stop(false); voiced = false; loudSince = 0; setState('listening'); return; }
          const now = performance.now();
          if (!recorder) {
            try { recorder = new MediaRecorder(media!); } catch { latest.current.onError('此瀏覽器無法啟動錄音，請使用文字。'); return; }
            chunks = []; voiced = false; started = now; loudSince = 0;
            recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
            recorder.onerror = () => latest.current.onError('錄音中斷，請重新開啟語音模式或使用文字。');
            recorder.start();
          }
          analyser.getFloatTimeDomainData(samples);
          const rms = Math.sqrt(samples.reduce((sum, value) => sum + value * value, 0) / samples.length);
          if (rms > 0.025) {
            if (!loudSince) loudSince = now;
            if (now - loudSince >= 120) {
              lastVoice = now;
              if (!voiced) { voiced = true; setState('recording'); latest.current.onSpeech(); }
            }
          } else loudSince = 0;
          if ((voiced && now - lastVoice > 900) || now - started > (voiced ? 120000 : 15000)) {
            stop(voiced); voiced = false; setState('listening');
          }
        }, 40);
      } catch (failure) {
        if (!cancelled) latest.current.onError(failure instanceof Error ? failure.message : '無法取得麥克風，請使用文字提問。');
      }
    })();
    return () => {
      cancelled = true; clearInterval(interval); stop(false);
      media?.getTracks().forEach(track => track.stop());
      if (context && context.state !== 'closed') void context.close();
    };
  }, [enabled]);
  return state;
}
