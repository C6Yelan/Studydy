import { usePodcastVideo } from "./PodcastVideo";
import { BackLink } from "../../ui/BackLink";
import { PodcastTranscript } from "./PodcastTranscript";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { errorMessage, isNotFound, type StudydyApiClient } from "../../api/client";
import { NotFoundView } from "../../ui/NotFoundView";
import type { PodcastProgressUpdate, PodcastProgressView, PodcastView } from "../../api/contracts";
import { writeRoute } from "../../app/routes";
import { StateView } from "../../ui/StateView";
import { Icon } from "../../ui/Icon";
import { claimText } from "../../ui/claim-text";
import { PodcastProgress } from "./PodcastProgress";
import { podcastPreviewReady } from "./PodcastLibrary";

import { PodcastPlayer, savedPosition, formatTime as duration } from "./PodcastPlayer";

export function PodcastPage({ apiClient, podcastId, learnerId, materialId }: {
  apiClient: StudydyApiClient; podcastId: string; learnerId: string; materialId?: string;
}) {
  const storageKey = `studydy.podcast.position:${learnerId}:${podcastId}`;
  const [view, setView] = useState<PodcastView | null>(null);
  const viewRef = useRef<PodcastView | null>(null);
  viewRef.current = view;
  const [index, setIndex] = useState(() => savedPosition(storageKey).episode);
  // 雲端進度：undefined 讀取中、null 讀取失敗（改用此瀏覽器保存的位置）。
  const [progress, setProgress] = useState<PodcastProgressView | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [busy, setBusy] = useState(false);
  const [readingTab, setReadingTab] = useState<"transcript" | "highlights">("transcript");
  const media = useRef<HTMLMediaElement | null>(null);
  const workspace = useRef<HTMLDivElement>(null);
  const active = useRef(false), changing = useRef(false);
  const rememberPosition = useRef(true);
  const autoPlayNext = useRef(false);
  const episodeList = useRef<HTMLElement>(null);
  const videoState = usePodcastVideo(apiClient,podcastId,index,view && podcastPreviewReady(view) ? view.episodes[index]?.audio?.sha256 : undefined,view?.episodes[index]?.script_sha256??undefined);
  useLayoutEffect(() => {
    const layout = workspace.current;
    if (!layout) return;
    // 量測實際頁首／導覽佔用的位置；可用高度交由 CSS 的 dvh 計算。
    // 使用文件座標，避免捲動時播放器跟著放大或縮小。
    const resize = () => {
      const top = `${Math.ceil(layout.getBoundingClientRect().top + window.scrollY)}px`;
      if (layout.style.getPropertyValue('--watch-room-top') !== top) layout.style.setProperty('--watch-room-top', top);
      // 底部常駐播放列佔用的高度（含與內容的間距），觀看室不延伸到播放列下方。
      const dock = document.querySelector('.podcast-dock')?.getBoundingClientRect();
      const space = `${dock ? Math.ceil(window.innerHeight - dock.top) : 0}px`;
      if (layout.style.getPropertyValue('--podcast-dock-space') !== space) layout.style.setProperty('--podcast-dock-space', space);
      // 寬螢幕：播放列對齊影片欄（左緣與寬度同影片），不浮在整頁中央。
      const player = layout.querySelector('.media-player')?.getBoundingClientRect();
      const root = document.documentElement.style;
      if (player && window.matchMedia('(min-width: 1101px)').matches) {
        root.setProperty('--podcast-dock-left', `${Math.round(player.left)}px`);
        root.setProperty('--podcast-dock-width', `${Math.round(player.width)}px`);
      } else { root.removeProperty('--podcast-dock-left'); root.removeProperty('--podcast-dock-width'); }
    };
    const observer = new ResizeObserver(resize);
    observer.observe(layout);
    const dock = document.querySelector('.podcast-dock');
    if (dock) observer.observe(dock);
    document.querySelectorAll('.app-header, .material-learning-toolbar, .material-context, .podcast-page > .cards-page-header, .podcast-generation-summary').forEach(element => observer.observe(element));
    window.addEventListener('resize', resize);
    resize();
    return () => {
      observer.disconnect(); window.removeEventListener('resize', resize);
      document.documentElement.style.removeProperty('--podcast-dock-left'); document.documentElement.style.removeProperty('--podcast-dock-width');
    };
  }, [view?.podcast_id, view?.status, view?.generation?.active, videoState.state?.status, actionError, progress === undefined, index]);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const read = async () => {
      try {
        const result = await apiClient.getPodcast(podcastId);
        if (cancelled) return;
        if (!materialId) { writeRoute({ name: "podcast", podcastId, materialId: result.material_id }, true); return; }
        if (result.material_id !== materialId) { setNotFound(true); return; }
        setView(result); setError(null); setIndex((i) => Math.min(i, result.episodes.length - 1));
        if (result.status === "pending" || result.status === "running" || result.generation?.active) timer = setTimeout(() => void read(), 2000);
      } catch (e) { if (!cancelled) { if (isNotFound(e) && !viewRef.current) setNotFound(true); else setError(errorMessage(e)); } }
    };
    void read();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [apiClient, podcastId, materialId, reload]);
  const progressReady = view?.status === "ready";
  useEffect(() => {
    if (!progressReady) return;
    let cancelled = false;
    void (async () => {
      let value = await apiClient.getPodcastProgress(podcastId);
      // 舊版只存在此瀏覽器的位置：伺服器還沒有紀錄時上傳一次（不含收聽秒數）。
      const local = savedPosition(storageKey);
      if (!value.current && (local.episode > 0 || local.time > 0)) {
        value = await apiClient.savePodcastProgress(podcastId, {
          schema: "podcast-progress-update/v1", episode_index: local.episode, position_seconds: local.time, listened_seconds: 0, legacy: true,
        }).catch(() => value);
      }
      if (cancelled) return;
      setProgress(value);
      if (value.current) setIndex(value.current.episode_index);
    })().catch(() => { if (!cancelled) setProgress(null); });
    return () => { cancelled = true; };
    // 只在開啟節目時讀取一次；之後以每次回報的結果更新。
  }, [apiClient, podcastId, progressReady]);
  const syncProgress = useCallback((body: PodcastProgressUpdate, keepalive: boolean) =>
    apiClient.savePodcastProgress(podcastId, body, { keepalive }).then((value) => { if (active.current) setProgress(value); }),
  [apiClient, podcastId]);
  useEffect(() => {
    const focus = () => setReload((v) => v + 1);
    window.addEventListener("focus", focus);
    return () => window.removeEventListener("focus", focus);
  }, []);
  useEffect(() => {
    // 重開末集或自動接續時，讓清單中的目前集數保持可見。
    const list = episodeList.current;
    const current = list?.querySelector<HTMLElement>("button[aria-current=true]");
    if (!list || !current) return;
    const item = current.getBoundingClientRect(), box = list.getBoundingClientRect();
    const top = box.top + list.clientTop, bottom = top + list.clientHeight;
    if (item.top < top) list.scrollTop += item.top - top;
    else if (item.bottom > bottom) list.scrollTop += item.bottom - bottom;
  }, [index, view?.episode_count]);
  const act = async (action: "retry" | "cancel") => {
    if (!view || changing.current) return;
    changing.current = true; setBusy(true); setActionError(null);
    try {
      if (action === 'cancel') {
        await apiClient.deletePodcast(podcastId);
        rememberPosition.current = false;
        media.current?.pause();
        try { localStorage.removeItem(storageKey); } catch { /* 不影響伺服器刪除。 */ }
        if (active.current) writeRoute(materialId ? {name:'material-content',materialId,kind:'podcasts'} : {name:'podcasts'});
        return;
      }
      await apiClient.podcastAction(podcastId, { schema: "podcast-action/v1", action, expected_version: view.version });
      if (active.current) { setReload((v) => v + 1); }
    } catch (e) { if (active.current) { setActionError(errorMessage(e)); setReload((v) => v + 1); } }
    finally { changing.current = false; if (active.current) setBusy(false); }
  };
  const back = <BackLink label="返回 Podcast" route={materialId ? { name: "material-content", materialId, kind: "podcasts" } : { name: "podcasts" }} />;
  if (notFound) return <NotFoundView title="找不到這份 Podcast" description="這份 Podcast 可能已被刪除，或不屬於目前登入的帳號。"
    back={{ label: "回到 Podcast", route: materialId ? { name: "material-content", materialId, kind: "podcasts" } : { name: "podcasts" } }} />;
  if (error || !view) return <section className="cards-page podcast-page">{back}<StateView title={error ? "無法讀取 Podcast" : "正在讀取 Podcast"}
    description={error ?? "正在載入已保存的內容。"} tone={error ? "failure" : "loading"} live={!error} skeleton="media"
    action={error ? <button type="button" className="secondary-button" onClick={() => setReload((v) => v + 1)}>重新讀取</button> : undefined} /></section>;
  const episode = view.episodes[index];
  const previewReady = podcastPreviewReady(view);
  const groups = [...new Set(episode.claims.map(c => c.concept_id))].map(id => ({ label: episode.claims.find(c => c.concept_id === id)!.label, claims: episode.claims.filter(c => c.concept_id === id) }));
  return <section className="cards-page podcast-page">
    {back}
    <header className="cards-page-header"><div>
      <h1>{view.name}</h1>
      {!materialId && <p className="cards-material-name"><Icon name="book" size={16} />{view.material_name}</p>}
    </div></header>
    {actionError && <p className="form-error" role="alert">{actionError}</p>}
    {!view.is_current_revision && <p className="podcast-meta-note">依建立時的教材版本保存</p>}
    {!previewReady && <PodcastProgress view={view} busy={busy} onAction={action => void act(action)} />}
    {previewReady && videoState.state?.status !== "ready" && <StateView title={videoState.error ? "無法載入影片" : "正在載入影片"} description={videoState.error || "正在確認本集影片。"} tone={videoState.error ? "failure" : "loading"} action={<button type="button" className="secondary-button" onClick={videoState.reload}>重新讀取</button>} />}
    {previewReady && videoState.state?.status === "ready" && progress !== undefined && <div ref={workspace} className="podcast-listening-layout">
        <PodcastPlayer key={`${podcastId}/${index}`} view={view} index={index} episodeLabel={episode.title || episodeTitle(episode.claims.map(c => c.label))} videoState={videoState} mediaRef={media} storageKey={storageKey}
          resumeAt={progress?.episodes.find((item) => item.episode_index === index)?.position_seconds ?? null} onSync={syncProgress} settingsKey={`studydy.podcast.settings:${learnerId}`} onPrevious={() => { autoPlayNext.current = view.status === "ready"; setIndex(index - 1); }} onNext={() => { autoPlayNext.current = view.status === "ready"; setIndex(index + 1); }} rememberPosition={rememberPosition} autoPlay={autoPlayNext.current} onEnded={(autoAdvance) => {
          if (autoAdvance && index + 1 < view.episodes.length) { autoPlayNext.current = true; setIndex(index + 1); }
        }} />
      <div className="podcast-companion">
      <aside ref={episodeList} className="surface podcast-episodes" aria-label="分集清單"><h2>分集清單</h2>{view.episodes.map((e, i) => <button type="button" key={i} aria-current={index === i ? "true" : undefined} onClick={() => { autoPlayNext.current = view.status === "ready" && !!media.current && !media.current.paused; setIndex(i); }}>
      <span className="podcast-episode-number">{String(i + 1).padStart(2, "0")}</span><span className="podcast-episode-summary"><strong title={[...new Set(e.claims.map(c => c.label))].join(" · ")}>{e.title || episodeTitle(e.claims.map(c => c.label))}</strong><small>{e.audio ? duration(e.audio.duration_seconds) : ["failed", "cancelled"].includes(view.status) ? "尚未完成" : view.episodes.findIndex(episode => !episode.audio) === i && view.status === "running" ? (e.script ? "製作音訊中" : "整理內容中") : "等待處理"}</small></span></button>)}</aside>
        <section className="surface podcast-reading-area" aria-label="本集內容">
          <div className="podcast-content-tabs" role="tablist" aria-label="本集內容">
            {([['transcript', '逐字稿'], ['highlights', '本集重點']] as const).map(([tab, label]) => <button
              key={tab} id={`podcast-${tab}-tab`} type="button" role="tab" aria-selected={readingTab === tab}
              aria-controls={`podcast-${tab}-panel`} tabIndex={readingTab === tab ? 0 : -1}
              onClick={() => setReadingTab(tab)} onKeyDown={event => {
                if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
                event.preventDefault();
                const next = event.key === 'Home' ? 'transcript' : event.key === 'End' ? 'highlights' : tab === 'transcript' ? 'highlights' : 'transcript';
                setReadingTab(next);
                document.getElementById(`podcast-${next}-tab`)?.focus();
              }}>{label}</button>)}
          </div>
          <div id="podcast-transcript-panel" className="podcast-content-panel" role="tabpanel" aria-labelledby="podcast-transcript-tab" hidden={readingTab !== 'transcript'}>
            {!!videoState.state?.video?.pages.length && <nav className="podcast-chapters" aria-label="本集章節">{videoState.state.video.pages.map((page, i) => <button type="button" key={i} title={page.title} onClick={() => { if (media.current && media.current.readyState > 0) media.current.currentTime = page.start; }}><time>{duration(page.start)}</time><span>{page.title}</span></button>)}</nav>}
            <div className="podcast-transcript" tabIndex={0}>
              <PodcastTranscript api={apiClient} view={view} index={index} media={media} active={readingTab === 'transcript'} videoVersion={videoState.state?.version??0} />
            </div>
          </div>
          <div id="podcast-highlights-panel" className="podcast-content-panel" role="tabpanel" aria-labelledby="podcast-highlights-tab" hidden={readingTab !== 'highlights'}>
            <div className="podcast-highlights" key={index} tabIndex={0}><header><span>{episode.claims.length} 個重點</span></header>
              {groups.map(group => <div key={group.claims[0].concept_id}>{groups.length > 1 && <h3>{group.label}</h3>}<ul>{group.claims.map(claim => <li key={claim.claim_id}><p>{claimText(claim)}</p></li>)}</ul></div>)}
            </div>
          </div>
        </section>
      </div>
    </div>}
  </section>;
}

// 分集沒有另外的集名：用前兩個概念當標題，其餘以「等 N 個概念」帶過，完整清單放在提示文字。
function episodeTitle(labels: string[]) {
  const unique = [...new Set(labels)];
  return unique.length <= 2 ? unique.join("、") : `${unique.slice(0, 2).join("、")} 等 ${unique.length} 個概念`;
}
