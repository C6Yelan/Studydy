import { MicVAD, utils } from '@ricky0123/vad-web';

declare const __VOICE_ASSET_BASE__: string;

export async function createVoiceVad(stream: MediaStream, context: AudioContext, callbacks: {
  onStart: () => void; onEnd: (blob: Blob) => void; onMisfire: () => void; onLimit: () => void;
}) {
  // 原生降噪／回音消除後，只濾除低頻震動；不以音量門檻裁掉小聲的人聲。
  const source = context.createMediaStreamSource(stream);
  const highpass = context.createBiquadFilter();
  highpass.type = 'highpass'; highpass.frequency.value = 80; highpass.Q.value = 0.707;
  const destination = context.createMediaStreamDestination();
  source.connect(highpass); highpass.connect(destination);
  let frames = 0, active = false;
  const vad = await MicVAD.new({
    model: 'v6', audioContext: context,
    baseAssetPath: __VOICE_ASSET_BASE__, onnxWASMBasePath: __VOICE_ASSET_BASE__,
    ortConfig: ort => { ort.env.wasm.numThreads = 1; ort.env.logLevel = 'error'; },
    getStream: async () => destination.stream,
    pauseStream: async () => {}, resumeStream: async () => destination.stream,
    positiveSpeechThreshold: 0.5, negativeSpeechThreshold: 0.35,
    preSpeechPadMs: 800, redemptionMs: 1400, minSpeechMs: 256,
    submitUserSpeechOnPause: true,
    onSpeechStart: () => { active = true; frames = 0; },
    onSpeechRealStart: callbacks.onStart,
    onVADMisfire: () => { active = false; callbacks.onMisfire(); },
    onSpeechEnd: audio => {
      active = false;
      callbacks.onEnd(new Blob([utils.encodeWAV(audio, 1, 16000, 1, 16)], {type: 'audio/wav'}));
    },
    // 以已處理的取樣數限制長度，包含前置緩衝仍低於伺服器的 121 秒上限。
    onFrameProcessed: () => {
      if (active && ++frames >= 118 * 16000 / 512) {
        active = false;
        callbacks.onLimit();
      }
    },
  });
  return vad;
}
