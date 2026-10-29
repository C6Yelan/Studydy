import { useEffect, useRef, useState } from 'react';
import type { createVoiceVad } from './voice-vad';

declare const __VOICE_ASSET_BASE__: string;

type VoiceVad = Awaited<ReturnType<typeof createVoiceVad>>;

/**
 * 預先下載語音模型等公共資產（不取得麥克風）。只在使用者移到或聚焦「語音模式」開關時呼叫，
 * 只用文字問答時不下載數 MB 的模型；之後的 fetch 重用瀏覽器快取。
 */
export function prefetchVoiceAssets() {
  for (const file of ['silero_vad_v6.onnx', 'ort-wasm-simd-threaded.wasm', 'ort-wasm-simd-threaded.mjs']) {
    const href = __VOICE_ASSET_BASE__ + file;
    if (document.querySelector(`link[href="${href}"]`)) continue;
    const link = document.createElement('link');
    link.rel = file.endsWith('.mjs') ? 'modulepreload' : 'preload';
    if (link.rel === 'preload') link.as = 'fetch';
    link.crossOrigin = 'anonymous'; link.href = href;
    document.head.append(link);
  }
  // 預載失敗仍可用文字；實際開啟語音時由原本的錯誤處理回報。
  void import('./voice-vad').catch(() => {});
}

export function useContinuousVoice({enabled, paused, onRecording, onSpeech, onError}: {
  enabled: boolean; paused: boolean; onRecording: (blob: Blob) => void;
  onSpeech: () => void; onError: (message: string) => void;
}) {
  const latest = useRef({paused, onRecording, onSpeech, onError});
  latest.current = {paused, onRecording, onSpeech, onError};
  const syncPaused = useRef<(() => void) | undefined>(undefined);
  const [state, setState] = useState<'off'|'permission'|'loading'|'listening'|'recording'>('off');
  useEffect(() => {
    if (!enabled) { setState('off'); return; }
    let cancelled = false, submitted = false;
    let media: MediaStream | undefined, context: AudioContext | undefined, vad: VoiceVad | undefined;
    let operations = Promise.resolve();
    const allowed = () => !cancelled && !submitted && !latest.current.paused;
    const fail = (failure: unknown) => {
      if (!cancelled) latest.current.onError(failure instanceof DOMException && failure.name === 'NotFoundError' ? '找不到麥克風，請確認已連接麥克風，或改用文字提問。' : failure instanceof Error ? failure.message : '無法啟動語音辨識，請重新開啟語音模式或使用文字提問。');
    };
    // 暫停、恢復與銷毀依序執行，避免快速切換模式時留下麥克風或送出舊錄音。
    const synchronize = () => {
      operations = operations.then(async () => {
        if (!vad || cancelled) return;
        if (latest.current.paused) {
          await vad.pause();
          if (!cancelled) setState('listening');
        } else {
          submitted = false;
          await vad.start();
        }
      }).catch(fail);
    };
    syncPaused.current = synchronize;
    setState('permission');
    const initialize = (async () => {
      try {
        if (!navigator.mediaDevices?.getUserMedia || !window.AudioContext) throw new Error('此瀏覽器無法持續錄音，請使用文字提問。');
        media = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true}});
        if (cancelled) { media.getTracks().forEach(track => track.stop()); return; }
        setState('loading');
        context = new AudioContext();
        await context.resume();
        const {createVoiceVad} = await import('./voice-vad');
        if (cancelled) return;
        vad = await createVoiceVad(media, context, {
          onStart: () => { if (allowed()) { setState('recording'); latest.current.onSpeech(); } },
          onMisfire: () => { if (allowed()) setState('listening'); },
          onLimit: () => {
            operations = operations.then(async () => {
              if (!vad || cancelled) return;
              await vad.pause();
              // 長片段若仍不足有效人聲，捨棄後繼續聆聽；有效錄音則等問答完成。
              if (allowed()) await vad.start();
            }).catch(fail);
          },
          onEnd: blob => {
            if (!allowed()) return;
            submitted = true;
            setState('listening');
            latest.current.onRecording(blob);
          },
        });
        if (!cancelled) { setState('listening'); synchronize(); }
      } catch (failure) {
        media?.getTracks().forEach(track => track.stop());
        if (context && context.state !== 'closed') await context.close();
        fail(failure);
      }
    })();
    return () => {
      cancelled = true;
      syncPaused.current = undefined;
      media?.getTracks().forEach(track => track.stop());
      void initialize.then(() => operations).then(async () => {
        try { await vad?.destroy(); }
        finally { if (context && context.state !== 'closed') await context.close(); }
      }).catch(() => {});
    };
  }, [enabled]);
  useEffect(() => { syncPaused.current?.(); }, [paused]);
  return state;
}
