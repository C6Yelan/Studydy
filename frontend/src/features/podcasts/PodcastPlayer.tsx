import { useEffect, useRef, useState } from "react";
import type { PodcastView } from "../../api/contracts";

export function savedPosition(key: string): { episode: number; time: number } {
  try {
    const p = JSON.parse(localStorage.getItem(key) ?? "null");
    if (p && Number.isInteger(p.episode) && p.episode >= 0 && Number.isFinite(p.time) && p.time >= 0) return p;
  } catch { /* 儲存受限不妨礙播放。 */ }
  return { episode: 0, time: 0 };
}
export function formatTime(seconds: number): string {
  const value = Number.isFinite(seconds) ? Math.max(0, Math.floor(seconds)) : 0;
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, "0")}`;
}
const speeds = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];
type Settings = { speed: number; volume: number; muted: boolean; autoAdvance: boolean };
function readSettings(key: string): Settings {
  try {
    const v = JSON.parse(localStorage.getItem(key) ?? "null");
    if (v && speeds.includes(v.speed) && typeof v.volume === "number" && v.volume >= 0 && v.volume <= 1
      && typeof v.muted === "boolean" && typeof v.autoAdvance === "boolean") return v;
  } catch { /* 使用預設播放設定。 */ }
  return { speed: 1, volume: 1, muted: false, autoAdvance: true };
}
function ControlIcon({ name }: { name: "play" | "pause" | "previous" | "next" | "volume" | "muted" | "settings" | "rewind" | "forward" }) {
  return <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {name === "rewind" || name === "forward" ? <><g transform={name === "forward" ? "translate(24 0) scale(-1 1)" : undefined}><path d="M4 7V3M4 7h4M4 7a9 9 0 1 1-1 9" /></g><text x="12" y="15" textAnchor="middle" fill="currentColor" stroke="none" fontSize="8" fontWeight="600">10</text></> : name === "play" ? <path d="m8 5 11 7-11 7Z" fill="currentColor" stroke="none" /> : name === "pause" ? <><path d="M8 5v14M16 5v14" strokeWidth="4" /></>
      : name === "previous" ? <><path d="M5 5v14M18 5 8 12l10 7Z" /></> : name === "next" ? <><path d="M19 5v14M6 5l10 7-10 7Z" /></>
      : name === "settings" ? <><path d="m9 3-1 3-3 1-2 3 2 3v4l3 1 2 3h4l2-3 3-1v-4l2-3-2-3-3-1-1-3Z" /><circle cx="12" cy="12" r="3" /></>
      : <><path d="M3 9h4l5-4v14l-5-4H3Z" />{name === "muted" ? <path d="m17 9 5 6m0-6-5 6" /> : <><path d="M16 8a6 6 0 0 1 0 8M19 5a10 10 0 0 1 0 14" /></>}</>}
  </svg>;
}

export function PodcastPlayer({ view, index, storageKey, settingsKey, rememberPosition, autoPlay, onEnded, onPrevious, onNext, mediaRef }: {
  view: PodcastView; index: number; storageKey: string; settingsKey: string; rememberPosition: { current: boolean };
  mediaRef?: {current: HTMLAudioElement | null};
  autoPlay: boolean; onEnded: () => void; onPrevious: () => void; onNext: () => void;
}) {
  const internalMedia = useRef<HTMLAudioElement>(null);
  const media = mediaRef ?? internalMedia;
  const menu = useRef<HTMLDivElement>(null), gear = useRef<HTMLButtonElement>(null);
  const [settings, setSettings] = useState(() => readSettings(settingsKey));
  const [menuOpen, setMenuOpen] = useState(false), [playing, setPlaying] = useState(false), [ready, setReady] = useState(false);
  const [time, setTime] = useState(0), [length, setLength] = useState(view.episodes[index].audio!.duration_seconds);
  const [error, setError] = useState<string | null>(null), [storageError, setStorageError] = useState(false);
  const lastSaved = useRef(0), lastVolume = useRef(settings.volume || 1);
  const save = (element: HTMLAudioElement) => {
    if (!rememberPosition.current || !Number.isFinite(element.currentTime) || element.readyState === 0) return;
    try { localStorage.setItem(storageKey, JSON.stringify({ episode: index, time: element.currentTime })); }
    catch { setStorageError(true); }
  };
  useEffect(() => {
    const element = media.current;
    const leaving = () => { if (element) save(element); };
    window.addEventListener("pagehide", leaving);
    return () => {
      window.removeEventListener("pagehide", leaving);
      if (element) { save(element); element.pause(); element.removeAttribute("src"); element.load(); }
    };
  }, [storageKey, index]);
  useEffect(() => {
    if (media.current) { media.current.playbackRate = settings.speed; media.current.volume = settings.volume; media.current.muted = settings.muted; }
    try { localStorage.setItem(settingsKey, JSON.stringify(settings)); } catch { setStorageError(true); }
  }, [settings, settingsKey]);
  useEffect(() => {
    if (!menuOpen) return;
    const close = (e: PointerEvent) => { if (!menu.current?.contains(e.target as Node)) setMenuOpen(false); };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [menuOpen]);
  const toggle = () => {
    const element = media.current;
    if (!element || !ready) return;
    if (!element.paused) element.pause();
    else void element.play().then(() => setError(null), () => setError("暫時無法播放，請按播放重試。"));
  };
  const seek = (value: number) => { if (media.current && ready) { media.current.currentTime = Math.max(0, Math.min(length, value)); setTime(media.current.currentTime); } };
  const mute = () => setSettings(s => ({ ...s, muted: !(s.muted || s.volume === 0), volume: s.volume || lastVolume.current }));
  return <section className="media-player" aria-label={`第 ${index + 1} 集播放器`} tabIndex={0} onKeyDown={e => { if (e.key === "Escape" && menuOpen) { e.stopPropagation(); setMenuOpen(false); gear.current?.focus(); } }}>
    <div className="media-player-heading"><div><p>第 {index + 1} 集 / 共 {view.episode_count} 集 · {view.mode === "quick" ? "快速複習" : "完整講解"}{view.delivery === "dialogue" ? " · 雙人對談" : ""}</p><h2>{[...new Set(view.episodes[index].claims.map(c => c.label))].join(" · ")}</h2></div></div>
    <audio ref={media} preload="metadata" aria-label={`第 ${index + 1} 集音訊`} src={`/v1/podcasts/${view.podcast_id}/episodes/${index}/audio`}
      onLoadedMetadata={e => {
        const element = e.currentTarget, position = savedPosition(storageKey);
        setLength(element.duration); setReady(true);
        if (!autoPlay && position.episode === index && position.time < element.duration - 1) element.currentTime = position.time;
        setTime(element.currentTime); save(element);
        if (autoPlay) void element.play().catch(() => setError("按播放即可接續下一集。"));
      }} onPlay={() => { setPlaying(true); setError(null); }} onPause={e => { setPlaying(false); save(e.currentTarget); }}
      onTimeUpdate={e => { setTime(e.currentTarget.currentTime); if (Date.now() - lastSaved.current > 2000) { save(e.currentTarget); lastSaved.current = Date.now(); } }}
      onSeeked={e => save(e.currentTarget)} onEnded={e => { setPlaying(false); save(e.currentTarget); if (settings.autoAdvance) onEnded(); }}
      onError={() => { setReady(false); setError("音訊讀取失敗，請重試。"); }} />
    <input className="media-timeline" type="range" aria-label="播放進度" aria-valuetext={`${formatTime(time)} / ${formatTime(length)}`} min={0} max={length} step={0.1} value={time} disabled={!ready} onChange={e => seek(Number(e.target.value))} style={{ backgroundSize: `${length ? time / length * 100 : 0}% 100%` }} />
    <div className="media-time" aria-live="off"><span>{formatTime(time)}</span><span>{formatTime(length)}</span></div>
    <div className="media-controls"><div className="media-transport">
      <button type="button" className="media-icon" aria-label="上一集" title="上一集" disabled={index === 0} onClick={onPrevious}><ControlIcon name="previous" /></button>
      <button type="button" className="media-icon" aria-label="後退 10 秒" title="後退 10 秒" disabled={!ready} onClick={() => seek(time - 10)}><ControlIcon name="rewind" /></button>
      <button type="button" className="media-icon media-play" aria-label={playing ? "暫停" : "播放"} title={playing ? "暫停" : "播放"} disabled={!ready} onClick={toggle}><ControlIcon name={playing ? "pause" : "play"} /></button>
      <button type="button" className="media-icon" aria-label="前進 10 秒" title="前進 10 秒" disabled={!ready} onClick={() => seek(time + 10)}><ControlIcon name="forward" /></button>
      <button type="button" className="media-icon" aria-label="下一集" title="下一集" disabled={index + 1 === view.episode_count} onClick={onNext}><ControlIcon name="next" /></button>
      </div>
      <div className="media-volume"><button type="button" className="media-icon" aria-label={settings.muted || settings.volume === 0 ? "取消靜音" : "靜音"} title="音量" onClick={mute}><ControlIcon name={settings.muted || settings.volume === 0 ? "muted" : "volume"} /></button>
        <input type="range" aria-label="音量" min={0} max={1} step={0.05} value={settings.muted ? 0 : settings.volume} onChange={e => { const volume = Number(e.target.value); if (volume > 0) lastVolume.current = volume; setSettings(s => ({ ...s, volume, muted: false })); }} /></div>
      <div className="media-settings" ref={menu}><label className="media-autoplay"><input type="checkbox" aria-label="自動播放下一集" checked={settings.autoAdvance} onChange={e => setSettings(s => ({ ...s, autoAdvance: e.target.checked }))} />自動接續</label><button ref={gear} type="button" className="media-icon media-settings-toggle" aria-label="播放速度" title="播放速度" aria-expanded={menuOpen} aria-controls="podcast-player-speed" onClick={() => setMenuOpen(v => !v)}><span>{settings.speed}×</span><ControlIcon name="settings" /></button>
        {menuOpen && <div id="podcast-player-speed" className="media-settings-panel" role="group" aria-label="播放速度選單"><strong>播放速度</strong><div className="media-speed-options">{speeds.map(speed => <button type="button" key={speed} aria-pressed={settings.speed === speed} onClick={() => setSettings(s => ({ ...s, speed }))}>{speed === 1 ? "正常" : `${speed}×`}</button>)}</div></div>}
      </div>
    </div>
    {error && <p className="form-error media-feedback" role="alert">{error}{!ready && <button type="button" className="text-button" onClick={() => { setError(null); media.current?.load(); }}>重試音訊</button>}</p>}
    {storageError && <p className="media-feedback">瀏覽器目前無法保存播放位置或設定。</p>}
  </section>;
}
