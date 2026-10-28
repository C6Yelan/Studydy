// 關鍵回饋音效「島嶼木琴」：全部用 Web Audio 即時合成，沒有音檔；木琴、水滴、浪聲與鋼鼓。
// 升級、成就與完成關卡共用「Stu-dy-dy」三音招牌旋律（G5、C6、E6，短短長）。
// 瀏覽器要求使用者操作後才能發聲，所以第一次點擊或按鍵之前一律靜音。
export type Cue = "click" | "correct" | "incorrect" | "levelUp" | "badge" | "stage" | "cardKnown";

const STORAGE_KEY = "studydy.sound";
// partials：[頻率倍數, 相對音量, 衰減時間占比]；noise 為帶通濾波的白噪音（浪聲），f→to 是濾波中心頻率。
type Note = { at: number; freq: number; length: number; gain: number; to?: number; partials?: [number, number, number][]; noise?: boolean; attack?: number; echo?: boolean };

const SEMITONES: Record<string, number> = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };
const hz = (name: string) => 440 * 2 ** ((SEMITONES[name[0]] + (Number(name[1]) - 4) * 12 - 9) / 12);

const marimba = (at: number, note: string, length = 0.5, gain = 0.2): Note =>
  ({ at, freq: hz(note), length, gain, partials: [[3.99, 0.45, 0.2], [9.9, 0.12, 0.08]] });
const drop = (at: number, freq: number, to: number, gain = 0.12): Note => ({ at, freq, to, length: 0.13, gain });
const wave = (at: number, freq: number, to: number, length: number, gain: number): Note =>
  ({ at, freq, to, length, gain, noise: true, attack: length * 0.45 });
const steelDrum = (at: number, note: string, length: number, gain = 0.17): Note =>
  ({ at, freq: hz(note), length, gain, partials: [[2.01, 0.45, 0.55], [3.03, 0.22, 0.35]], echo: true });

const cues: Record<Cue, Note[]> = {
  click: [marimba(0, "G6", 0.07, 0.07)],
  correct: [drop(0, 520, 1500, 0.1), marimba(0.04, "G5", 0.4), marimba(0.13, "D6", 0.7, 0.22)],
  // 答錯：兩聲低木琴緩緩往下，不用不協和音。
  incorrect: [marimba(0, "E4", 0.45, 0.15), marimba(0.17, "C4", 0.7, 0.13)],
  levelUp: [
    ...["C5", "D5", "E5", "G5", "A5", "C6"].map((note, i) => marimba(i * 0.055, note, 0.35, 0.14)),
    wave(0.2, 500, 2400, 0.55, 0.1),
    marimba(0.42, "G5", 0.25), marimba(0.52, "C6", 0.25), marimba(0.62, "E6", 1, 0.22), marimba(0.62, "C5", 1, 0.1),
  ],
  badge: [steelDrum(0, "G5", 0.5), steelDrum(0.12, "C6", 0.5), steelDrum(0.24, "E6", 1.1, 0.2), drop(0.5, 900, 2600, 0.07), drop(0.62, 1100, 3000, 0.06)],
  stage: [
    wave(0, 300, 1800, 0.7, 0.13),
    marimba(0.28, "G5", 0.3), marimba(0.38, "C6", 0.3), marimba(0.48, "E6", 0.9, 0.21), marimba(0.48, "G4", 0.9, 0.1), marimba(0.48, "C5", 0.9, 0.1),
  ],
  cardKnown: [drop(0, 650, 1900, 0.13), marimba(0.05, "A6", 0.35, 0.1)],
};

export function soundEnabled(): boolean {
  try { return localStorage.getItem(STORAGE_KEY) !== "off"; } catch { return true; }
}

export function setSoundEnabled(enabled: boolean) {
  try { localStorage.setItem(STORAGE_KEY, enabled ? "on" : "off"); } catch { /* 無法保存時，本次仍依畫面上的開關運作。 */ }
}

type Output = { ctx: AudioContext; master: AudioNode; echo: AudioNode; noise: AudioBuffer };
let output: Output | null = null;
let unlocked = false;

function audio(): Output | null {
  if (output) return output;
  const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!Ctor) return null;
  try {
    const ctx = new Ctor();
    const master = ctx.createDynamicsCompressor();
    master.threshold.value = -14;
    master.ratio.value = 4;
    master.connect(ctx.destination);
    // 鋼鼓的回音：0.16 秒延遲，低通後少量回授。
    const echo = ctx.createGain();
    echo.gain.value = 0.3;
    const delay = ctx.createDelay(1);
    delay.delayTime.value = 0.16;
    const lowpass = ctx.createBiquadFilter();
    lowpass.frequency.value = 3200;
    const feedback = ctx.createGain();
    feedback.gain.value = 0.32;
    echo.connect(delay).connect(lowpass).connect(feedback).connect(delay);
    lowpass.connect(master);
    const noise = ctx.createBuffer(1, ctx.sampleRate, ctx.sampleRate);
    const samples = noise.getChannelData(0);
    for (let i = 0; i < samples.length; i++) samples[i] = Math.random() * 2 - 1;
    output = { ctx, master, echo, noise };
  } catch { return null; }
  return output;
}

function voice({ ctx, master, echo, noise }: Output, note: Note, start: number) {
  const from = start + note.at;
  const end = from + note.length + 0.05;
  const volume = ctx.createGain();
  volume.gain.setValueAtTime(0.0001, from);
  volume.gain.exponentialRampToValueAtTime(note.gain, from + (note.attack ?? 0.004));
  volume.gain.exponentialRampToValueAtTime(0.0001, from + note.length);
  volume.connect(master);
  if (note.echo) volume.connect(echo);
  if (note.noise) {
    const source = ctx.createBufferSource();
    source.buffer = noise;
    source.loop = true;
    const band = ctx.createBiquadFilter();
    band.type = "bandpass";
    band.Q.value = 0.9;
    band.frequency.setValueAtTime(note.freq, from);
    band.frequency.exponentialRampToValueAtTime(note.to ?? note.freq, from + note.length);
    source.connect(band).connect(volume);
    source.start(from);
    source.stop(end);
    return;
  }
  const osc = ctx.createOscillator();
  osc.frequency.setValueAtTime(note.freq, from);
  if (note.to) osc.frequency.exponentialRampToValueAtTime(note.to, from + note.length);
  osc.connect(volume);
  osc.start(from);
  osc.stop(end);
  for (const [multiple, ratio, decay] of note.partials ?? []) {
    const partial = ctx.createOscillator();
    const partialVolume = ctx.createGain();
    partial.frequency.setValueAtTime(note.freq * multiple, from);
    partialVolume.gain.setValueAtTime(0.0001, from);
    partialVolume.gain.exponentialRampToValueAtTime(note.gain * ratio, from + 0.003);
    partialVolume.gain.exponentialRampToValueAtTime(0.0001, from + note.length * decay);
    partial.connect(partialVolume).connect(master);
    if (note.echo) partialVolume.connect(echo);
    partial.start(from);
    partial.stop(end);
  }
}

export function playCue(cue: Cue) {
  if (!unlocked || !soundEnabled()) return;
  const out = audio();
  if (!out) return;
  if (out.ctx.state === "suspended") void out.ctx.resume().catch(() => undefined);
  const start = out.ctx.currentTime + 0.01;
  for (const note of cues[cue]) voice(out, note, start);
}

// 解除靜音需要使用者操作；同時為主要按鈕加上輕微的點擊音。已有專屬提示音的按鈕標 data-silent-click。
export function installSound() {
  const unlock = () => { unlocked = true; };
  document.addEventListener("pointerdown", unlock, { capture: true, once: true });
  document.addEventListener("keydown", unlock, { capture: true, once: true });
  document.addEventListener("click", (event) => {
    unlocked = true;
    const button = event.target instanceof Element ? event.target.closest<HTMLElement>(".primary-button, .secondary-button") : null;
    if (button && !button.matches(":disabled, [aria-disabled='true']") && !button.closest("[data-silent-click]")) playCue("click");
  }, { capture: true });
}
