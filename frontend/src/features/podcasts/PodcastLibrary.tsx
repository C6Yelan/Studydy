import { useEffect, useRef, useState, type ReactNode } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { PodcastSummary, MaterialLibraryItem } from "../../api/contracts";
import { writeRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";
import { islandColors, islandPalette } from "../../ui/art/Island";
import type { CSSProperties } from "react";
import { PodcastCancelControl } from "./PodcastCancelControl";
import { PodcastManagement } from "./PodcastManagement";
import { StateView } from "../../ui/StateView";
import { CollectionHeader } from "../../ui/CollectionHeader";
import { EmptyState } from "../../ui/EmptyState";

export const podcastPreviewReady = (item: PodcastSummary) => item.status === 'ready' && item.generation?.preview_ready === true;

export const podcastStatus = { pending: "等待生成", running: "正在生成", ready: "可以播放", failed: "生成中斷", cancelled: "已取消" };

export function PodcastLibrary({ apiClient, learnerId, material, contentNavigation }: { apiClient: StudydyApiClient; learnerId: string; material?: MaterialLibraryItem; contentNavigation?: ReactNode }) {
  const [items, setItems] = useState<PodcastSummary[] | null>(null);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [deletingId, setDeletingId] = useState<string|null>(null);
  const heading = useRef<HTMLDivElement>(null);
  const mutationVersion = useRef(0);
  useEffect(() => {
    let cancelled = false;
    setError(null);
    let timer:ReturnType<typeof setTimeout>|undefined;
    const read = async () => {
      const version = mutationVersion.current;
      try {
        const list = await apiClient.listPodcasts();
        if (cancelled || version !== mutationVersion.current) return;
        const visible = material ? list.podcasts.filter(p => p.material_id === material.material_id) : list.podcasts;
        setItems(visible); setError(null);
        if (visible.some(p=>p.generation?.active || ['pending','running'].includes(p.status))) timer=setTimeout(read,2000);
      } catch(e) { if(!cancelled)setError(errorMessage(e)); }
    };
    void read();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [apiClient, reload, material?.material_id]);
  const cancelAndDelete = async (id:string) => {
    if (deletingId) return;
    setDeletingId(id);setError(null);
    try {
      await apiClient.deletePodcast(id);
      mutationVersion.current++;
      try { localStorage.removeItem(`studydy.podcast.position:${learnerId}:${id}`); } catch { /* 不影響伺服器刪除。 */ }
      setItems(previous=>previous?.filter(p=>p.podcast_id!==id)??null);setReload(value=>value+1);
      heading.current?.focus();
    } catch(e) { setError(errorMessage(e)); }
    finally { setDeletingId(null); }
  };
  const structure = material?.available_structures.find(s => s.knowledge_structure_revision === material.head_revision);
  const create = () => { if (material && structure) writeRoute({ name: "podcast-create", materialId: material.material_id, runId: structure.run_id, structureRevision: structure.knowledge_structure_revision }); else if (!material) writeRoute({ name: "podcast-new" }); };
  const filtered = items?.filter((item) => `${item.name} ${item.material_name}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  return <section className="material-library is-collection podcast-library" aria-label={material ? "教材 Podcast" : "我的 Podcast"}>
    {contentNavigation}
    {material ? <header className="library-header library-header-compact collection-toolbar material-search-row">
      <form className="library-search" role="search" onSubmit={e => e.preventDefault()}><input type="search" aria-label="搜尋 Podcast" placeholder="搜尋 Podcast…" value={query} onChange={e => setQuery(e.target.value)} onKeyDown={e => { if (e.key === "Escape") e.preventDefault(); }} /></form>
      <div className="collection-summary" ref={heading} tabIndex={-1}><p>{items === null ? "—" : `已保存 ${items.length} 份 Podcast`}</p></div>
      <button className="primary-button" type="button" disabled={!structure} onClick={create}><Icon name="headphones" size={18} /> 建立 Podcast</button>
    </header> : <CollectionHeader eyebrow="用聽的學習" title="我的 Podcast" subtitle="把教材重點變成可以隨時收聽的節目，收聽進度會自動保存。"
      actions={<button className="primary-button" type="button" onClick={create}><Icon name="headphones" size={18} /> 建立 Podcast</button>}
      search={{ label: "搜尋 Podcast 或教材", placeholder: "搜尋 Podcast 或教材…", value: query, onChange: setQuery }}
      count={items === null ? "—" : `已保存 ${items.length} 份 Podcast`} countRef={heading} />}
      <div role={material ? "tabpanel" : undefined} id={material ? "material-panel-podcasts" : undefined} aria-labelledby={material ? "material-tab-podcasts" : undefined}>
    {error && items !== null && <p className="form-error" role="alert">無法更新 Podcast 列表。{error}<button className="text-button" onClick={()=>setReload(value=>value+1)}>重新讀取</button></p>}
    {error && items === null ? <StateView title="無法讀取 Podcast" description={error} tone="failure" action={<button type="button" className="secondary-button" onClick={() => setReload((v) => v + 1)}>重新讀取</button>} />
      : items === null ? <StateView title="正在讀取 Podcast" description="正在找回你保存的內容。" tone="loading" live skeleton="library" />
      : items.length === 0 ? <EmptyState title="讓教材說給你聽" description="挑選概念，選擇快速或完整模式。生成後保存在你的帳號，隨時可以再聽。"
        action={<button className="primary-button" type="button" disabled={!!material && !structure} onClick={create}><Icon name="headphones" size={18} /> {material ? "建立 Podcast" : "選擇教材"}</button>} />
      : <>
        <div className="library-grid">{filtered?.map((item) => <article className="surface library-item podcast-tile" key={item.podcast_id}>
          <PodcastCover item={item} />
          <p className="library-metadata">{item.delivery === "dialogue" ? "雙人對談" : "單人解說"} · {item.episode_count} 集</p>
          <PodcastManagement item={item} apiClient={apiClient} onChanged={() => {mutationVersion.current++;setReload(v => v + 1);}} onDeleted={() => {
            mutationVersion.current++;
            try { localStorage.removeItem(`studydy.podcast.position:${learnerId}:${item.podcast_id}`); } catch { /* 不影響伺服器刪除。 */ }
            setItems(previous => previous?.filter(p => p.podcast_id !== item.podcast_id) ?? null);
            setReload(v=>v+1);heading.current?.focus();
          }} />
          {!material && <p className="podcast-material-name" title={item.material_name}>{item.material_name}</p>}
          {item.generation ? <p className="library-state">{item.generation.active ? (item.status === 'ready' ? '影片／字幕生成中' : podcastStatus[item.status]) : item.generation.failed_videos ? '部分影片生成失敗' : podcastStatus[item.status]} · 音訊 {item.completed_episodes} / {item.episode_count} 集 · 影片 {item.generation.completed_videos} / {item.episode_count} 集</p>
            : item.status !== "ready" && <p className="library-state">{podcastStatus[item.status]} · 音訊已完成 {item.completed_episodes} / {item.episode_count} 集</p>}
          {(item.generation?.active || ['pending','running'].includes(item.status)) && <PodcastCancelControl busy={deletingId!==null} onConfirm={()=>void cancelAndDelete(item.podcast_id)}/>}
          {!item.is_current_revision && <p className="library-state is-version">教材已有新版 · 保留建立時版本</p>}
          <button type="button" className="primary-button" onClick={() => writeRoute({ name: "podcast", podcastId: item.podcast_id, materialId: item.material_id })}>{podcastPreviewReady(item) ? "開啟播放" : "查看進度"}</button>
        </article>)}</div>{filtered?.length === 0 && <p role="status">找不到符合的 Podcast。</p>}</>}
    </div>
  </section>;
}

// 節目封面：底色沿用所屬教材的島嶼配色，同教材的不同節目再以角度區分；右下標示集數。
function PodcastCover({ item }: { item: PodcastSummary }) {
  const colors = islandColors(islandPalette(item.material_id));
  let hash = 0;
  for (const char of item.podcast_id) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return <span className="podcast-cover" aria-hidden="true" style={{ "--cover-from": colors.c2, "--cover-to": colors.c3, "--cover-angle": `${120 + (hash % 7) * 20}deg` } as CSSProperties}>
    <span className="podcast-cover-rings" />
    <img src="/assets/studydy/brand-idle.png" alt="" />
    <em>{item.delivery === "dialogue" ? "雙人對談" : "單人解說"}</em>
    <i>{item.episode_count} 集</i>
  </span>;
}
