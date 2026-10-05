import { useCallback, useEffect, useRef, useState, type SyntheticEvent } from "react";
import type { PodcastView } from "../../api/contracts";
import { PodcastVideoStatus, type VideoView, type usePodcastVideo } from "./PodcastVideo";

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
function ControlIcon({ name }: { name: "play" | "pause" | "previous" | "next" | "volume" | "muted" | "settings" | "rewind" | "forward" | "fullscreen" }) {
  return <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {name === "rewind" || name === "forward" ? <><g transform={name === "forward" ? "translate(24 0) scale(-1 1)" : undefined}><path d="M4 7V3M4 7h4M4 7a9 9 0 1 1-1 9" /></g><text x="12" y="15" textAnchor="middle" fill="currentColor" stroke="none" fontSize="8" fontWeight="600">10</text></> : name === "play" ? <path d="m8 5 11 7-11 7Z" fill="currentColor" stroke="none" /> : name === "pause" ? <><path d="M8 5v14M16 5v14" strokeWidth="4" /></>
      : name === "previous" ? <><path d="M5 5v14M18 5 8 12l10 7Z" /></> : name === "next" ? <><path d="M19 5v14M6 5l10 7-10 7Z" /></>
      : name === "settings" ? <><path d="m9 3-1 3-3 1-2 3 2 3v4l3 1 2 3h4l2-3 3-1v-4l2-3-2-3-3-1-1-3Z" /><circle cx="12" cy="12" r="3" /></>
      : name === "fullscreen" ? <path d="M9 3H3v6M15 3h6v6M3 15v6h6M21 15v6h-6" />
      : <><path d="M3 9h4l5-4v14l-5-4H3Z" />{name === "muted" ? <path d="m17 9 5 6m0-6-5 6" /> : <><path d="M16 8a6 6 0 0 1 0 8M19 5a10 10 0 0 1 0 14" /></>}</>}
  </svg>;
}

export function PodcastPlayer({ view, index, storageKey, settingsKey, rememberPosition, autoPlay, onEnded, onPrevious, onNext, mediaRef, videoState }: {
  view: PodcastView; index: number; storageKey: string; settingsKey: string; rememberPosition: { current: boolean };
  mediaRef?: {current: HTMLMediaElement | null};
  videoState: ReturnType<typeof usePodcastVideo>;
  autoPlay: boolean; onEnded: (autoAdvance:boolean) => void; onPrevious: () => void; onNext: () => void;
}) {
  const internalMedia = useRef<HTMLMediaElement>(null);
  const media = mediaRef ?? internalMedia;
  const video: VideoView['video'] = videoState.state?.status === 'ready' ? videoState.state.video : null;
  const mediaSrc = view.status === 'ready' ? `/v1/podcasts/${view.podcast_id}/episodes/${index}/${video ? 'video/media' : 'audio'}` : undefined;
  const handoff = useRef<{time:number;playing:boolean}|null>(null);
  const menu = useRef<HTMLDivElement>(null), gear = useRef<HTMLButtonElement>(null);
  const viewport = useRef<HTMLDivElement>(null);
  const [fullscreen, setFullscreen] = useState(false);
  const [captions,setCaptions]=useState(false);
  const [settings, setSettings] = useState(() => readSettings(settingsKey));
  const [menuOpen, setMenuOpen] = useState(false), [playing, setPlaying] = useState(false), [ready, setReady] = useState(false);
  const [time, setTime] = useState(0), [length, setLength] = useState(view.episodes[index].audio?.duration_seconds ?? 0);
  const [error, setError] = useState<string | null>(null), [storageError, setStorageError] = useState(false);
  const lastSaved = useRef(0), lastVolume = useRef(settings.volume || 1);
  const save = useCallback((element: HTMLMediaElement) => {
    if (!rememberPosition.current || !Number.isFinite(element.currentTime) || element.readyState === 0) return;
    try { localStorage.setItem(storageKey, JSON.stringify({ episode: index, time: element.currentTime })); }
    catch { setStorageError(true); }
  }, [storageKey,index,rememberPosition]);
  const attachMedia = useCallback((element:HTMLMediaElement|null) => {
    const previous=media.current;
    if(previous && previous!==element){
      handoff.current={time:previous.currentTime,playing:!previous.paused};
      save(previous);previous.pause();previous.removeAttribute('src');previous.load();
    }
    media.current=element;
  },[media,save]);
  useEffect(()=>{
    const leaving=()=>{if(media.current)save(media.current)};
    window.addEventListener('pagehide',leaving);
    return()=>window.removeEventListener('pagehide',leaving);
  },[media,save]);
  useEffect(()=>{setReady(!!media.current && media.current.readyState>0)},[mediaSrc]);
  useEffect(() => {
    if (media.current) { media.current.playbackRate = settings.speed; media.current.volume = settings.volume; media.current.muted = settings.muted; }
    try { localStorage.setItem(settingsKey, JSON.stringify(settings)); } catch { setStorageError(true); }
  }, [settings, settingsKey, mediaSrc]);
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
  useEffect(()=>{
    const element=media.current;
    if(element instanceof HTMLVideoElement)for(const track of Array.from(element.textTracks))track.mode=captions?'showing':'disabled';
  },[captions,mediaSrc,ready]);
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
      setLength(element.duration);setReady(true);setError(null);
      if (pending && Number.isFinite(pending.time)) element.currentTime=Math.min(pending.time,Math.max(0,element.duration-.1));
      else if (!autoPlay && position.episode===index && position.time<element.duration-1) element.currentTime=position.time;
      handoff.current=null;setTime(element.currentTime);save(element);
      element.playbackRate=settings.speed;element.volume=settings.volume;element.muted=settings.muted;
      if (pending?.playing || autoPlay) void element.play().catch(e=>{if(e?.name!=="AbortError")setError("按播放即可繼續。");});
    },
    onPlay:(e:SyntheticEvent<HTMLMediaElement>)=>{if(e.currentTarget===media.current){setPlaying(true);setError(null)}},
    onPause:(e:SyntheticEvent<HTMLMediaElement>)=>{if(e.currentTarget===media.current){setPlaying(false);save(e.currentTarget)}},
    onTimeUpdate:(e:SyntheticEvent<HTMLMediaElement>)=>{if(e.currentTarget!==media.current)return;setTime(e.currentTarget.currentTime);if(Date.now()-lastSaved.current>2000){save(e.currentTarget);lastSaved.current=Date.now()}},
    onSeeked:(e:SyntheticEvent<HTMLMediaElement>)=>{if(e.currentTarget===media.current)save(e.currentTarget)},
    onEnded:(e:SyntheticEvent<HTMLMediaElement>)=>{if(e.currentTarget!==media.current)return;setPlaying(false);save(e.currentTarget);onEnded(settings.autoAdvance)},
    onError:(e:SyntheticEvent<HTMLMediaElement>)=>{if(e.currentTarget===media.current){setReady(false);setError("媒體讀取失敗，請重試。")}},
  };
  const title = [...new Set(view.episodes[index].claims.map(c => c.label))].join(" · ");
  return <section className="media-player" aria-label={`第 ${index + 1} 集播放器`} tabIndex={0} onKeyDown={e => { if (e.key === "Escape" && menuOpen) { e.stopPropagation(); setMenuOpen(false); gear.current?.focus(); } }}>
    <div className="media-player-heading"><div><p>第 {index + 1} 集 / 共 {view.episode_count} 集 · {view.delivery === "dialogue" ? "雙人對談" : "單人解說"}</p><h2 title={title}>{title}</h2></div></div>
    <div className={`media-viewport${video ? " has-video" : ""}`} ref={viewport}>
      <div className="media-display" role="region" aria-label="教學影片">
        {video ? <video ref={attachMedia} {...mediaEvents} playsInline className="podcast-video-element" aria-label={`第 ${index+1} 集影片`}>
          <track kind="captions" src={`/v1/podcasts/${view.podcast_id}/episodes/${index}/subtitles`} srcLang="zh-Hant" label="繁體中文講稿" />
        </video> : <audio ref={attachMedia} {...mediaEvents} aria-label={`第 ${index+1} 集音訊`} />}
        {!video && <><span className="media-display-label">2D 教學影片</span>
        <div className="media-video-empty">
          <svg className="media-video-icon" viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><rect x="5" y="10" width="38" height="28" rx="4"/><path d="m20 18 12 6-12 6Z"/></svg>
          <h3>{videoState.state?.status === "running" ? "正在製作教學影片" : videoState.state?.status === "pending" ? "影片排隊中" : "本集影片尚未生成"}</h3>
          <p>{view.status !== "ready" ? "音訊完成後即可收聽" : !ready ? "正在載入本集音訊…" : playing ? "正在收聽本集 Podcast" : "可以先收聽本集 Podcast"}</p>
          <PodcastVideoStatus {...videoState} />
        </div></>}
      </div>
      <div className="media-playback-controls" role="group" aria-label="播放控制">
    <div className="media-progress-track"><span aria-hidden="true" style={{width:`${length ? Math.max(0,Math.min(100,time / length * 100)) : 0}%`}}/>
    <input className="media-timeline" type="range" aria-label="播放進度" aria-valuetext={`${formatTime(time)} / ${formatTime(length)}`} min={0} max={length} step={0.1} value={time} disabled={!ready} onChange={e => seek(Number(e.target.value))} />
    </div>
    <div className="media-time" aria-live="off"><span>{formatTime(time)}</span><span>{formatTime(length)}</span></div>
    <div className="media-controls"><div className="media-transport">
      <button type="button" className="media-icon" aria-label="上一集" title="上一集" disabled={index === 0} onClick={onPrevious}><ControlIcon name="previous" /></button>
      <button type="button" className="media-icon" aria-label="後退 10 秒" title="後退 10 秒" disabled={!ready} onClick={() => seek(time - 10)}><ControlIcon name="rewind" /></button>
      <button type="button" className="media-icon media-play" aria-label={playing ? "暫停" : "播放"} title={playing ? "暫停" : "播放"} disabled={!ready} onClick={toggle}><ControlIcon name={playing ? "pause" : "play"} /></button>
      <button type="button" className="media-icon" aria-label="前進 10 秒" title="前進 10 秒" disabled={!ready} onClick={() => seek(time + 10)}><ControlIcon name="forward" /></button>
      <button type="button" className="media-icon" aria-label="下一集" title="下一集" disabled={index + 1 === view.episode_count} onClick={onNext}><ControlIcon name="next" /></button>
      </div>
      <div className="media-volume"><button type="button" className="media-icon" aria-label={settings.muted || settings.volume === 0 ? "取消靜音" : "靜音"} title="音量" onClick={mute}><ControlIcon name={settings.muted || settings.volume === 0 ? "muted" : "volume"} /></button>
        <input type="range" aria-label="音量" min={0} max={1} step={0.05} value={settings.muted ? 0 : settings.volume} onChange={e => changeVolume(Number(e.target.value))} /></div>
      <div className="media-settings" ref={menu}><label className="media-autoplay"><input type="checkbox" aria-label="自動播放下一集" checked={settings.autoAdvance} onChange={e => setSettings(s => ({ ...s, autoAdvance: e.target.checked }))} />自動接續</label><button ref={gear} type="button" className="media-icon media-settings-toggle" aria-label="播放設定" title="播放設定" aria-expanded={menuOpen} aria-controls="podcast-player-settings" onClick={() => setMenuOpen(v => !v)}><span>{settings.speed}×</span><ControlIcon name="settings" /></button>
        {menuOpen && <div id="podcast-player-settings" className="media-settings-panel" role="group" aria-label="播放設定選單"><strong>播放速度</strong><div className="media-speed-options">{speeds.map(speed => <button type="button" key={speed} aria-pressed={settings.speed === speed} onClick={() => {setSettings(s => ({ ...s, speed }));setMenuOpen(false)}}>{speed === 1 ? "正常" : `${speed}×`}</button>)}</div><label className="media-menu-volume">音量<input type="range" aria-label="設定音量" min={0} max={1} step={0.05} value={settings.muted ? 0 : settings.volume} onChange={e => changeVolume(Number(e.target.value))}/></label></div>}
        {video&&<button type="button" className="text-button" aria-pressed={captions} onClick={()=>setCaptions(v=>!v)}>字幕</button>}
        <button type="button" className="media-icon" aria-label={fullscreen ? "退出全螢幕" : "全螢幕"} title={fullscreen ? "退出全螢幕" : "全螢幕"} onClick={() => void toggleFullscreen()}><ControlIcon name="fullscreen" /></button>
      </div>
    </div>
      </div>
    </div>
    {video && <nav className="podcast-video-pages" aria-label="本集投影片">{video.pages.map((page,i)=><button key={i} type="button" disabled={!ready} aria-current={time>=page.start&&time<page.end?'true':undefined} onClick={()=>seek(page.start)}>{i+1}. {page.title}</button>)}</nav>}
    {error && <p className="form-error media-feedback" role="alert">{error}{!ready && <button type="button" className="text-button" onClick={() => { setError(null); media.current?.load(); }}>重新載入媒體</button>}</p>}
    {storageError && <p className="media-feedback">瀏覽器目前無法保存播放位置或設定。</p>}
  </section>;
}
