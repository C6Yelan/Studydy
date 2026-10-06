import { usePodcastVideo } from "./PodcastVideo";
import { PodcastTranscript } from "./PodcastTranscript";
import { useEffect, useRef, useState } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { PodcastView } from "../../api/contracts";
import { writeRoute } from "../../app/routes";
import { StateView } from "../../ui/StateView";
import { Icon } from "../../ui/Icon";
import { claimText } from "../../ui/claim-text";
import { PodcastProgress } from "./PodcastProgress";

import { PodcastPlayer, savedPosition, formatTime as duration } from "./PodcastPlayer";

export function PodcastPage({ apiClient, podcastId, learnerId, materialId }: {
  apiClient: StudydyApiClient; podcastId: string; learnerId: string; materialId?: string;
}) {
  const storageKey = `studydy.podcast.position:${learnerId}:${podcastId}`;
  const [view, setView] = useState<PodcastView | null>(null);
  const [index, setIndex] = useState(() => savedPosition(storageKey).episode);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [busy, setBusy] = useState(false);
  const media = useRef<HTMLMediaElement | null>(null);
  const workspace = useRef<HTMLDivElement>(null);
  const active = useRef(false), changing = useRef(false);
  const rememberPosition = useRef(true);
  const autoPlayNext = useRef(false);
  const episodeList = useRef<HTMLElement>(null);
  const videoState = usePodcastVideo(apiClient,podcastId,index,view?.episodes[index]?.audio?.sha256,view?.episodes[index]?.script_sha256??undefined);
  useEffect(() => {
    const layout = workspace.current;
    if (!layout) return;
    // 依頁首實際高度保留底部空間；捲動不會重新改變左右欄尺寸。
    const resize = () => {
      const top = layout.getBoundingClientRect().top + window.scrollY;
      const height = `${Math.max(360, Math.floor(window.innerHeight - top - 24))}px`;
      if (layout.style.getPropertyValue('--watch-room-height') !== height) layout.style.setProperty('--watch-room-height', height);
    };
    const observer = new ResizeObserver(resize);
    observer.observe(layout);
    window.addEventListener('resize', resize);
    resize();
    return () => { observer.disconnect(); window.removeEventListener('resize', resize); };
  }, [view?.podcast_id, view?.status]);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const read = async () => {
      try {
        const result = await apiClient.getPodcast(podcastId);
        if (cancelled) return;
        if (!materialId) { writeRoute({ name: "podcast", podcastId, materialId: result.material_id }, true); return; }
        if (result.material_id !== materialId) throw new Error("這份 Podcast 不屬於此教材。");
        setView(result); setError(null); setIndex((i) => Math.min(i, result.episodes.length - 1));
        if (result.status === "pending" || result.status === "running") timer = setTimeout(() => void read(), 2000);
      } catch (e) { if (!cancelled) setError(errorMessage(e)); }
    };
    void read();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [apiClient, podcastId, materialId, reload]);
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
      await apiClient.podcastAction(podcastId, { schema: "podcast-action/v1", action, expected_version: view.version });
      if (active.current) { setReload((v) => v + 1); }
    } catch (e) { if (active.current) { setActionError(errorMessage(e)); setReload((v) => v + 1); } }
    finally { changing.current = false; if (active.current) setBusy(false); }
  };
  const back = <button type="button" className="cards-back text-button" onClick={() => writeRoute(materialId ? { name: "material-content", materialId, kind: "podcasts" } : { name: "podcasts" })}><Icon name="arrow-left" size={17} /> {materialId ? "此教材的 Podcast" : "我的 Podcast"}</button>;
  if (error || !view) return <section className="cards-page podcast-page">{back}<StateView title={error ? "無法讀取 Podcast" : "正在讀取 Podcast"}
    description={error ?? "正在載入已保存的內容。"} tone={error ? "failure" : "loading"} live={!error}
    action={error ? <button type="button" className="secondary-button" onClick={() => setReload((v) => v + 1)}>重新讀取</button> : undefined} /></section>;
  const episode = view.episodes[index];
  const groups = [...new Set(episode.claims.map(c => c.concept_id))].map(id => ({ label: episode.claims.find(c => c.concept_id === id)!.label, claims: episode.claims.filter(c => c.concept_id === id) }));
  return <section className="cards-page podcast-page">{back}
    <header className="cards-page-header"><div>
      <h1>{view.name}</h1>
      {!materialId && <p className="cards-material-name"><Icon name="book" size={16} />{view.material_name}</p>}
    </div></header>
    {actionError && <p className="form-error" role="alert">{actionError}</p>}
    {!view.is_current_revision && <p className="podcast-meta-note">依建立時的教材版本保存</p>}
    {view.status !== "ready" && <PodcastProgress view={view} busy={busy} onAction={action => void act(action)} />}
    {view.status === "ready" && <div ref={workspace} className="podcast-listening-layout">
        <PodcastPlayer key={`${podcastId}/${index}`} view={view} index={index} videoState={videoState} mediaRef={media} storageKey={storageKey} settingsKey={`studydy.podcast.settings:${learnerId}`} onPrevious={() => { autoPlayNext.current = view.status === "ready"; setIndex(index - 1); }} onNext={() => { autoPlayNext.current = view.status === "ready"; setIndex(index + 1); }} rememberPosition={rememberPosition} autoPlay={autoPlayNext.current} onEnded={(autoAdvance) => {
          if (autoAdvance && index + 1 < view.episodes.length) { autoPlayNext.current = true; setIndex(index + 1); }
        }} />
      <div className="podcast-companion">
        <section className="surface podcast-chapters" aria-label="章節列表"><h2>章節列表</h2>
          {videoState.state?.video?.pages.length ? <nav aria-label="本集章節">{videoState.state.video.pages.map((page, i) => <button type="button" key={i} onClick={() => { if (media.current && media.current.readyState > 0) media.current.currentTime = page.start; }}><time>{duration(page.start)}</time>{page.title}</button>)}</nav> : <p className="podcast-meta-note">影片完成後會顯示章節。</p>}
        </section>
      <aside ref={episodeList} className="surface podcast-episodes" aria-label="分集清單"><h2>分集清單</h2>{view.episodes.map((e, i) => <button type="button" key={i} aria-current={index === i ? "true" : undefined} onClick={() => { autoPlayNext.current = false; setIndex(i); }}>
      <span className="podcast-episode-number">{String(i + 1).padStart(2, "0")}</span><span className="podcast-episode-summary"><strong title={[...new Set(e.claims.map(c => c.label))].join(" · ")}>{[...new Set(e.claims.map(c => c.label))].join(" · ")}</strong><small>{e.audio ? duration(e.audio.duration_seconds) : ["failed", "cancelled"].includes(view.status) ? "尚未完成" : view.episodes.findIndex(episode => !episode.audio) === i && view.status === "running" ? (e.script ? "製作音訊中" : "整理內容中") : "等待處理"}</small></span></button>)}</aside>
        <section className="surface podcast-reading-area" aria-label="逐字稿">
          <h2>逐字稿</h2><div className="podcast-transcript">
            <PodcastTranscript api={apiClient} view={view} index={index} media={media} active videoVersion={videoState.state?.version??0} />
          </div>
        </section>
        <section className="surface podcast-highlights" aria-label="本集重點"><h2>本集重點</h2><header><span>{episode.claims.length} 個重點</span></header>
          {groups.map(group => <div key={group.claims[0].concept_id}>{groups.length > 1 && <h3>{group.label}</h3>}<ul>{group.claims.map(claim => <li key={claim.claim_id}><p>{claimText(claim)}</p></li>)}</ul></div>)}
        </section>
      </div>
    </div>}
  </section>;
}
