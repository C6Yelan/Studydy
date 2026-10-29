import { useCallback, useEffect, useRef, useState, type SyntheticEvent } from "react";
import type { PodcastProgressUpdate, PodcastView } from "../../api/contracts";
import { type VideoView, type usePodcastVideo } from "./PodcastVideo";

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
// 比照 YouTube：滑桿 0.25～2 倍、每格 0.05，下方是常用速度。
const speedRange = { min: 0.25, max: 2, step: 0.05 };
const speedPresets = [0.75, 1, 1.25, 1.5, 2];
// 以 0.05 為單位取整後再固定兩位小數，避免 0.7000000000000001 這類浮點誤差。
const clampSpeed = (value: number) => Number(Math.min(speedRange.max, Math.max(speedRange.min, Math.round(value / speedRange.step) * speedRange.step)).toFixed(2));
type Settings = { speed: number; volume: number; muted: boolean; autoAdvance: boolean };
function readSettings(key: string): Settings {
  try {
    const v = JSON.parse(localStorage.getItem(key) ?? "null");
    if (v && typeof v.speed === "number" && v.speed >= speedRange.min && v.speed <= speedRange.max && typeof v.volume === "number" && v.volume >= 0 && v.volume <= 1
      && typeof v.muted === "boolean" && typeof v.autoAdvance === "boolean") return { ...v, speed: clampSpeed(v.speed) };
  } catch { /* 使用預設播放設定。 */ }
  return { speed: 1, volume: 1, muted: false, autoAdvance: true };
}
function ControlIcon({ name }: { name: "play" | "pause" | "previous" | "next" | "volume" | "muted" | "settings" | "rewind" | "forward" | "fullscreen" | "exit-fullscreen" }) {
  return <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {name === "rewind" || name === "forward" ? <><g transform={name === "forward" ? "translate(24 0) scale(-1 1)" : undefined}><path d="M4 7V3M4 7h4M4 7a9 9 0 1 1-1 9" /></g><text x="12" y="15" textAnchor="middle" fill="currentColor" stroke="none" fontSize="8" fontWeight="600">10</text></> : name === "play" ? <path d="m8 5 11 7-11 7Z" fill="currentColor" stroke="none" /> : name === "pause" ? <><path d="M8 5v14M16 5v14" strokeWidth="4" /></>
      : name === "previous" ? <><path d="M5 5v14M18 5 8 12l10 7Z" /></> : name === "next" ? <><path d="M19 5v14M6 5l10 7-10 7Z" /></>
      : name === "settings" ? <><path d="m9 3-1 3-3 1-2 3 2 3v4l3 1 2 3h4l2-3 3-1v-4l2-3-2-3-3-1-1-3Z" /><circle cx="12" cy="12" r="3" /></>
      : name === "fullscreen" ? <path d="M9 3H3v6M15 3h6v6M3 15v6h6M21 15v6h-6" />
      : name === "exit-fullscreen" ? <path d="M3 9h6V3M21 9h-6V3M3 15h6v6M21 15h-6v6" />
      : <><path d="M3 9h4l5-4v14l-5-4H3Z" />{name === "muted" ? <path d="m17 9 5 6m0-6-5 6" /> : <><path d="M16 8a6 6 0 0 1 0 8M19 5a10 10 0 0 1 0 14" /></>}</>}
  </svg>;
}

// 播放中每 15 秒回報一次位置與實際收聽秒數。
const SYNC_INTERVAL_MS = 15_000;
export type ProgressSync = (body: PodcastProgressUpdate, keepalive: boolean) => Promise<unknown>;

export function PodcastPlayer({ view, index, storageKey, settingsKey, rememberPosition, autoPlay, onEnded, onPrevious, onNext, mediaRef, videoState, timelineReady, resumeAt, onSync, theater, onTheater }: {
  view: PodcastView; index: number; storageKey: string; settingsKey: string; rememberPosition: { current: boolean };
  mediaRef?: {current: HTMLMediaElement | null};
  videoState: ReturnType<typeof usePodcastVideo>;
  timelineReady: boolean; theater: boolean; onTheater: () => void;
  autoPlay: boolean; onEnded: (autoAdvance:boolean) => void; onPrevious: () => void; onNext: () => void;
  // 伺服器保存的本集位置（跨裝置接續）；沒有時改用此瀏覽器保存的位置。
  resumeAt?: number | null; onSync?: ProgressSync;
}) {
  const internalMedia = useRef<HTMLMediaElement>(null);
  const media = mediaRef ?? internalMedia;
  const video: VideoView['video'] = videoState.state?.status === 'ready' ? videoState.state.video : null;
  // 有影片播影片；沒勾影片或影片已刪除時，同一個播放器直接播音訊。
  const mediaSrc = view.status === 'ready' ? `/v1/podcasts/${view.podcast_id}/episodes/${index}/${video ? 'video/media' : 'audio'}` : undefined;
  const handoff = useRef<{time:number;playing:boolean}|null>(null);
  const menu = useRef<HTMLDivElement>(null), gear = useRef<HTMLButtonElement>(null);
  const viewport = useRef<HTMLDivElement>(null);
  const [fullscreen, setFullscreen] = useState(false);
  const [showSubtitles, setShowSubtitles] = useState(true);
  const [caption, setCaption] = useState("");
  const [captionBottom, setCaptionBottom] = useState(12);
  const [controlsVisible, setControlsVisible] = useState(true);
  const idle = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const keyboardFocus = useRef(false);
  const adjustingRange = useRef(false);
  const [settings, setSettings] = useState(() => readSettings(settingsKey));
  const [menuOpen, setMenuOpen] = useState(false), [playing, setPlaying] = useState(false), [ready, setReady] = useState(false);
  const [time, setTime] = useState(0), [length, setLength] = useState(view.episodes[index].audio?.duration_seconds ?? 0);
  const [error, setError] = useState<string | null>(null), [storageError, setStorageError] = useState(false);
  const lastSaved = useRef(0), lastVolume = useRef(settings.volume || 1);
  // 實際收聽以播放中的牆鐘時間計算（不受倍速影響）；不足 1 秒的部分留到下次回報。
  const listening = useRef<{ seconds: number; since: number | null }>({ seconds: 0, since: null });
  const lastSynced = useRef<number | null>(null);
  const measure = (playing: boolean) => {
    const value = listening.current, now = performance.now();
    if (value.since !== null) value.seconds += (now - value.since) / 1000;
    value.since = playing ? now : null;
  };
  const save = useCallback((element: HTMLMediaElement) => {
    if (!rememberPosition.current || !Number.isFinite(element.currentTime) || element.readyState === 0) return;
    try { localStorage.setItem(storageKey, JSON.stringify({ episode: index, time: element.currentTime })); }
    catch { setStorageError(true); }
  }, [storageKey,index,rememberPosition]);
  const sync = useCallback((element: HTMLMediaElement, options: { ended?: boolean; keepalive?: boolean } = {}) => {
    if (!onSync || !rememberPosition.current || !Number.isFinite(element.currentTime) || element.readyState === 0) return;
    measure(!element.paused && !element.ended);
    const seconds = Math.floor(listening.current.seconds);
    const position = Math.max(0, element.currentTime);
    // 位置沒變也沒有新的收聽時間時不送出。
    if (!seconds && !options.ended && lastSynced.current !== null && Math.abs(position - lastSynced.current) < 0.5) return;
    listening.current.seconds -= seconds;
    lastSynced.current = position;
    void onSync({ schema: "podcast-progress-update/v1", episode_index: index, position_seconds: position, listened_seconds: seconds, ...(options.ended ? { ended: true } : {}) }, !!options.keepalive)
      .catch(() => { listening.current.seconds += seconds; lastSynced.current = null; });
  }, [onSync, index, rememberPosition]);
  const attachMedia = useCallback((element:HTMLMediaElement|null) => {
    const previous=media.current;
    if(previous && previous!==element){
      // 尚未載入 metadata 的音訊沒有有效播放位置，不得覆蓋已保存的位置。
      handoff.current=previous.readyState>0?{time:previous.currentTime,playing:!previous.paused}:null;
      save(previous);sync(previous);previous.pause();previous.removeAttribute('src');previous.load();
    }
    media.current=element;
  },[media,save,sync]);
  useEffect(()=>{
    // 離開頁面或切到背景時以 keepalive 送出最後一次進度。
    const leaving=()=>{if(media.current){save(media.current);sync(media.current,{keepalive:true})}};
    const hidden=()=>{if(document.visibilityState==='hidden')leaving()};
    window.addEventListener('pagehide',leaving);
    document.addEventListener('visibilitychange',hidden);
    return()=>{window.removeEventListener('pagehide',leaving);document.removeEventListener('visibilitychange',hidden)};
  },[media,save,sync]);
  useEffect(()=>{
    if(!playing)return;
    const timer=window.setInterval(()=>{if(media.current)sync(media.current)},SYNC_INTERVAL_MS);
    return()=>window.clearInterval(timer);
  },[playing,media,sync]);
  useEffect(()=>{setReady(!!media.current && media.current.readyState>0)},[mediaSrc]);
  useEffect(() => {
    if (media.current) { media.current.playbackRate = settings.speed; media.current.volume = settings.volume; media.current.muted = settings.muted; }
    try { localStorage.setItem(settingsKey, JSON.stringify(settings)); } catch { setStorageError(true); }
  }, [settings, settingsKey, mediaSrc]);
  const syncSubtitles = useCallback(() => {
    const element = media.current;
    if (!element) return;
    // 瀏覽器沿用 VTT 的實測時序；顯示交給可換行的字幕區，避開自訂控制列。
    const tracks = Array.from(element.textTracks).filter(track => track.kind === "subtitles");
    for (const track of tracks) track.mode = "hidden";
    setCaption(showSubtitles ? tracks.flatMap(track => Array.from(track.activeCues ?? []))
      .map(cue => (cue as VTTCue).getCueAsHTML().textContent ?? "").join("\n") : "");
    // 有影片時控制列疊在畫面上（比照 YouTube）；顯示控制列時字幕須讓開。
    const overlay = fullscreen || video;
    const controlsHeight = controlsVisible && overlay ? viewport.current?.querySelector(".media-playback-controls")?.getBoundingClientRect().height ?? 0 : 0;
    setCaptionBottom(controlsHeight + 12);
  }, [media, showSubtitles, controlsVisible, fullscreen, video]);
  useEffect(() => {
    const element = media.current;
    const tracks = Array.from(element?.textTracks ?? []);
    for (const track of tracks) track.addEventListener("cuechange", syncSubtitles);
    element?.addEventListener("seeked", syncSubtitles);
    syncSubtitles();
    const observer = new ResizeObserver(syncSubtitles);
    if (viewport.current) observer.observe(viewport.current);
    const controls = viewport.current?.querySelector(".media-playback-controls");
    if (controls) observer.observe(controls);
    return () => {
      observer.disconnect();
      for (const track of tracks) track.removeEventListener("cuechange", syncSubtitles);
      element?.removeEventListener("seeked", syncSubtitles);
    };
  }, [syncSubtitles, mediaSrc, ready, timelineReady]);
  useEffect(() => {
    if (!menuOpen) return;
    const close = (e: PointerEvent) => { if (!menu.current?.contains(e.target as Node)) setMenuOpen(false); };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [menuOpen]);
  useEffect(() => {
    const changed = () => setFullscreen(document.fullscreenElement === viewport.current);
    document.addEventListener("fullscreenchange", changed);
    return () => document.removeEventListener("fullscreenchange", changed);
  }, []);
  const showControls = useCallback(() => {
    setControlsVisible(true);
    clearTimeout(idle.current);
    if (playing && !menuOpen && !keyboardFocus.current && !adjustingRange.current) idle.current = setTimeout(() => setControlsVisible(false), 2500);
  }, [playing, menuOpen]);
  useEffect(() => { showControls(); return () => clearTimeout(idle.current); }, [showControls]);
  useEffect(() => {
    // 拖曳滑桿時持續顯示；即使在播放器外放開，也要恢復閒置計時。
    const finishAdjustment = () => {
      if (!adjustingRange.current) return;
      adjustingRange.current = false;
      showControls();
    };
    document.addEventListener("pointerup", finishAdjustment);
    document.addEventListener("pointercancel", finishAdjustment);
    return () => {
      document.removeEventListener("pointerup", finishAdjustment);
      document.removeEventListener("pointercancel", finishAdjustment);
    };
  }, [showControls]);
  const toggleFullscreen = async () => {
    try {
      if (document.fullscreenElement === viewport.current) await document.exitFullscreen();
      else if (viewport.current?.requestFullscreen) await viewport.current.requestFullscreen();
      else setError("此瀏覽器尚不支援全螢幕播放。");
    } catch { setError("無法切換全螢幕，請再試一次。"); }
  };
  const toggle = () => {
    const element = media.current;
    if (!element || !ready) return;
    if (!element.paused) element.pause();
    else void element.play().then(() => setError(null), (e) => { if (e?.name !== "AbortError") setError("暫時無法播放，請按播放重試。"); });
  };
  const seek = (value: number) => { if (media.current && ready) { media.current.currentTime = Math.max(0, Math.min(length, value)); setTime(media.current.currentTime); } };
  const mute = () => setSettings(s => ({ ...s, muted: !(s.muted || s.volume === 0), volume: s.volume || lastVolume.current }));
  const changeVolume = (volume: number) => {
    if (volume > 0) lastVolume.current = volume;
    setSettings(s => ({ ...s, volume, muted: false }));
  };
  const mediaEvents = {
    preload: "metadata" as const, src: mediaSrc,
    onLoadedMetadata: (e:SyntheticEvent<HTMLMediaElement>) => {
      if(e.currentTarget!==media.current)return;
      const element=e.currentTarget, position=savedPosition(storageKey), pending=handoff.current;
      const resume=resumeAt ?? (position.episode===index ? position.time : null);
      setLength(element.duration);setReady(true);setError(null);
      if (pending && Number.isFinite(pending.time)) element.currentTime=Math.min(pending.time,Math.max(0,element.duration-.1));
      else if (!autoPlay && resume!==null && resume<element.duration-1) element.currentTime=resume;
      handoff.current=null;setTime(element.currentTime);save(element);lastSynced.current=element.currentTime;
      element.playbackRate=settings.speed;element.volume=settings.volume;element.muted=settings.muted;
      if (pending?.playing || autoPlay) void element.play().catch(e=>{if(e?.name!=="AbortError")setError("按播放即可繼續。");});
    },
    onPlay:(e:SyntheticEvent<HTMLMediaElement>)=>{if(e.currentTarget===media.current){setPlaying(true);setError(null);measure(true)}},
    onPause:(e:SyntheticEvent<HTMLMediaElement>)=>{if(e.currentTarget===media.current){setPlaying(false);save(e.currentTarget);if(!e.currentTarget.ended)sync(e.currentTarget)}},
    onTimeUpdate:(e:SyntheticEvent<HTMLMediaElement>)=>{if(e.currentTarget!==media.current)return;setTime(e.currentTarget.currentTime);if(Date.now()-lastSaved.current>2000){save(e.currentTarget);lastSaved.current=Date.now()}},
    onSeeked:(e:SyntheticEvent<HTMLMediaElement>)=>{if(e.currentTarget===media.current)save(e.currentTarget)},
    onEnded:(e:SyntheticEvent<HTMLMediaElement>)=>{if(e.currentTarget!==media.current)return;setPlaying(false);save(e.currentTarget);sync(e.currentTarget,{ended:true});onEnded(settings.autoAdvance)},
    onError:(e:SyntheticEvent<HTMLMediaElement>)=>{if(e.currentTarget===media.current){setReady(false);setError("媒體讀取失敗，請重試。")}},
  };
  // 一般模式與全螢幕共用同一組播放操作。
  const controls = <>
      <div className="media-playback-controls" role="group" aria-label="播放控制">
    <div className="media-progress-track"><span aria-hidden="true" style={{width:`${length ? Math.max(0,Math.min(100,time / length * 100)) : 0}%`}}/>
    <input className="media-timeline" type="range" aria-label="播放進度" aria-valuetext={`${formatTime(time)} / ${formatTime(length)}`} min={0} max={length} step={0.1} value={time} disabled={!ready} onChange={e => seek(Number(e.target.value))} />
    </div>
    <div className="media-controls"><div className="media-transport">
      <button type="button" className="media-icon media-play" aria-label={playing ? "暫停" : "播放"} title={playing ? "暫停" : "播放"} disabled={!ready} onClick={toggle}><ControlIcon name={playing ? "pause" : "play"} /></button>
      {index > 0 && <button type="button" className="media-icon" aria-label="上一集" title="上一集" onClick={onPrevious}><ControlIcon name="previous" /></button>}
      {index + 1 < view.episode_count && <button type="button" className="media-icon" aria-label="下一集" title="下一集" onClick={onNext}><ControlIcon name="next" /></button>}
      </div>
      <div className="media-seek-buttons">
        <button type="button" className="media-icon" aria-label="後退 10 秒" title="後退 10 秒" disabled={!ready} onClick={() => seek(time - 10)}><ControlIcon name="rewind" /></button>
        <button type="button" className="media-icon" aria-label="前進 10 秒" title="前進 10 秒" disabled={!ready} onClick={() => seek(time + 10)}><ControlIcon name="forward" /></button>
      </div>
      <div className="media-volume"><button type="button" className="media-icon" aria-label={settings.muted || settings.volume === 0 ? "取消靜音" : "靜音"} title="音量" onClick={mute}><ControlIcon name={settings.muted || settings.volume === 0 ? "muted" : "volume"} /></button>
        <input type="range" aria-label="音量" min={0} max={1} step={0.05} value={settings.muted ? 0 : settings.volume} onChange={e => changeVolume(Number(e.target.value))} /></div>
      <span className="media-time" aria-live="off">{formatTime(time)} / {formatTime(length)}</span>
      <div className="media-settings" ref={menu}><label className="media-autoplay"><input type="checkbox" aria-label="自動播放下一集" checked={settings.autoAdvance} onChange={e => setSettings(s => ({ ...s, autoAdvance: e.target.checked }))} />自動接續</label><button ref={gear} type="button" className="media-icon media-settings-toggle" aria-label="播放設定" title="播放設定" aria-expanded={menuOpen} aria-controls="podcast-player-settings" onClick={() => setMenuOpen(v => !v)}><span>{settings.speed}×</span><ControlIcon name="settings" /></button>
        {menuOpen && <div id="podcast-player-settings" className="media-settings-panel" role="group" aria-label="播放設定選單"><label className="media-menu-autoplay"><input type="checkbox" aria-label="自動播放下一集" checked={settings.autoAdvance} onChange={e => setSettings(s => ({ ...s, autoAdvance: e.target.checked }))} />自動接續</label><div className="media-menu-seek"><button type="button" className="text-button" disabled={!ready} onClick={() => seek(time - 10)}><ControlIcon name="rewind" />後退 10 秒</button><button type="button" className="text-button" disabled={!ready} onClick={() => seek(time + 10)}><ControlIcon name="forward" />前進 10 秒</button></div><label className="media-menu-row">字幕<button type="button" role="switch" className="media-switch" aria-label="字幕" aria-checked={showSubtitles} onClick={() => setShowSubtitles(value => !value)}><span /></button></label>
          <section className="media-speed" aria-label="播放速度"><strong>播放速度</strong><output aria-live="polite">{settings.speed.toFixed(2)}×</output>
            <div className="media-speed-slider"><button type="button" aria-label="減慢" disabled={settings.speed <= speedRange.min} onClick={() => setSettings(s => ({ ...s, speed: clampSpeed(s.speed - speedRange.step) }))}>−</button>
              <input type="range" aria-label="播放速度" min={speedRange.min} max={speedRange.max} step={speedRange.step} value={settings.speed} onChange={e => { const speed = clampSpeed(Number(e.target.value)); setSettings(s => ({ ...s, speed })); }} />
              <button type="button" aria-label="加快" disabled={settings.speed >= speedRange.max} onClick={() => setSettings(s => ({ ...s, speed: clampSpeed(s.speed + speedRange.step) }))}>+</button></div>
            <div className="media-speed-options">{speedPresets.map(speed => <button type="button" key={speed} aria-pressed={Math.abs(settings.speed - speed) < 0.001} onClick={() => setSettings(s => ({ ...s, speed }))}>{speed % 1 ? speed : speed.toFixed(1)}{speed === 1 && <small>正常</small>}</button>)}</div>
          </section><label className="media-menu-volume">音量<input type="range" aria-label="設定音量" min={0} max={1} step={0.05} value={settings.muted ? 0 : settings.volume} onChange={e => changeVolume(Number(e.target.value))}/></label></div>}
        {!fullscreen && <button type="button" className="media-icon media-theater-toggle" aria-label="劇院模式" aria-pressed={theater} title="劇院模式" onClick={onTheater}><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><rect x="2" y="5" width="20" height="14" rx="2"/></svg></button>}
        {video && <button type="button" className="media-icon" aria-label={fullscreen ? "退出全螢幕" : "全螢幕"} title={fullscreen ? "退出全螢幕" : "全螢幕"} onClick={() => void toggleFullscreen()}><ControlIcon name={fullscreen ? "exit-fullscreen" : "fullscreen"} /></button>}
      </div>
    </div>
      </div>
  </>;
  return <section className="media-player" aria-label={`第 ${index + 1} 集播放器`} onKeyDown={e => { if (e.key === "Escape" && menuOpen) { e.stopPropagation(); setMenuOpen(false); gear.current?.focus(); } }}>
    <div className={`media-viewport${video ? " has-video" : " audio-only"}${controlsVisible ? "" : " controls-idle"}`} ref={viewport}
      onPointerMove={showControls}
      // 滑鼠離開播放區就立即收起控制列；設定選單開著或拖曳進度時保留。
      onPointerLeave={() => { if (menuOpen || adjustingRange.current || keyboardFocus.current) return; clearTimeout(idle.current); setControlsVisible(false); }}
      onPointerDown={e => {
        keyboardFocus.current = false;
        adjustingRange.current = (e.target as HTMLElement).matches('input[type="range"]');
        showControls();
      }}
      onFocusCapture={e => { keyboardFocus.current = e.target.matches(':focus-visible'); showControls(); }}
      onBlurCapture={e => { if (!e.currentTarget.contains(e.relatedTarget)) { keyboardFocus.current = false; showControls(); } }}
      onKeyDown={e => {
        keyboardFocus.current = true; showControls();
        if (e.target !== e.currentTarget) return;
        if ([' ', 'Enter'].includes(e.key)) { e.preventDefault(); toggle(); }
        else if (e.key.toLowerCase() === 'f' && video) { e.preventDefault(); void toggleFullscreen(); }
        else if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { e.preventDefault(); seek(time + (e.key === 'ArrowLeft' ? -5 : 5)); }
      }}
      tabIndex={0} aria-label={video ? '影片播放區；按空白鍵播放或暫停' : '音訊播放區；按空白鍵播放或暫停'}>
      <div className="media-display" role="region" aria-label={video ? "教學影片" : "Podcast 音訊"} onClick={e => { if (!(e.target as HTMLElement).closest("button, a, input, select")) { toggle(); showControls(); } }}>
        <video ref={attachMedia} {...mediaEvents} playsInline className="podcast-video-element" aria-label={video ? `第 ${index+1} 集影片` : `第 ${index+1} 集音訊`}>
          {/* 對齊完成時重建失敗的字幕 track；保留同一播放器及播放位置。 */}
          <track key={timelineReady ? 'aligned' : 'pending'} kind="subtitles" src={`/v1/podcasts/${view.podcast_id}/episodes/${index}/subtitles`} srcLang="zh-TW" label="繁體中文"
            onLoad={syncSubtitles} />
        </video>
        {/* 尚未開始播放時顯示封面，避免整片深色空白。 */}
        {!playing && time === 0 && <div className="media-cover" aria-hidden="true">
          <span className="podcast-cover-rings" />
          <img src="/assets/studydy/brand-idle.png" alt="" />
          <b>第 {index + 1} 集</b>
        </div>}
      </div>
      {caption && <div className="media-subtitles" aria-hidden="true" style={{ bottom: captionBottom }}>{caption}</div>}
      {(fullscreen || video) && controls}
    </div>
    {!fullscreen && !video && <div className="podcast-controls-bar" role="region" aria-label="播放列">{controls}</div>}
    {error && <p className="form-error media-feedback" role="alert">{error}{!ready && <button type="button" className="text-button" onClick={() => { setError(null); media.current?.load(); }}>重新載入媒體</button>}</p>}
    {storageError && <p className="media-feedback">瀏覽器目前無法保存播放位置或設定。</p>}
  </section>;
}
