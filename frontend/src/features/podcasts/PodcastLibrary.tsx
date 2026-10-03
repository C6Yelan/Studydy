import { useEffect, useRef, useState, type ReactNode } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { PodcastSummary, MaterialLibraryItem } from "../../api/contracts";
import { writeRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";
import { PodcastManagement } from "./PodcastManagement";
import { StateView } from "../../ui/StateView";

export const podcastStatus = { pending: "等待生成", running: "正在生成", ready: "可以播放", failed: "生成中斷", cancelled: "已取消" };

export function PodcastLibrary({ apiClient, learnerId, material, contentNavigation }: { apiClient: StudydyApiClient; learnerId: string; material?: MaterialLibraryItem; contentNavigation?: ReactNode }) {
  const [items, setItems] = useState<PodcastSummary[] | null>(null);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const heading = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let cancelled = false;
    setError(null);
    void apiClient.listPodcasts().then((list) => {
      if (!cancelled) setItems(material ? list.podcasts.filter(p => p.material_id === material.material_id) : list.podcasts);
    }, (e) => { if (!cancelled) setError(errorMessage(e)); });
    return () => { cancelled = true; };
  }, [apiClient, reload, material?.material_id]);
  const structure = material?.available_structures.find(s => s.knowledge_structure_revision === material.head_revision);
  const create = () => { if (material && structure) writeRoute({ name: "podcast-create", materialId: material.material_id, runId: structure.run_id, structureRevision: structure.knowledge_structure_revision }); else if (!material) writeRoute({ name: "podcast-new" }); };
  const filtered = items?.filter((item) => `${item.name} ${item.material_name}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  return <section className="material-library is-collection podcast-library" aria-label={material ? "教材 Podcast" : "我的 Podcast"}>
    <header className={`library-header library-header-compact collection-toolbar${material ? " material-search-row" : ""}`}>
      <form className="library-search" role="search" onSubmit={e => e.preventDefault()}><input type="search" aria-label={material ? "搜尋 Podcast" : "搜尋 Podcast 或教材"} placeholder={material ? "搜尋 Podcast…" : "搜尋 Podcast 或教材…"} value={query} onChange={e => setQuery(e.target.value)} /></form>
      <div className="collection-summary" ref={heading} tabIndex={-1}><p>{items === null ? "—" : `已保存 ${items.length} 份 Podcast`}</p></div>
      <button className="primary-button" type="button" disabled={!!material && !structure} onClick={create}><Icon name="headphones" size={18} /> 建立 Podcast</button>
    </header>
      {contentNavigation}
      <div role={material ? "tabpanel" : undefined} id={material ? "material-panel-podcasts" : undefined} aria-labelledby={material ? "material-tab-podcasts" : undefined}>
    {error && items !== null && <p className="form-error" role="alert">無法更新 Podcast 列表。{error}<button className="text-button" onClick={()=>setReload(value=>value+1)}>重新讀取</button></p>}
    {error && items === null ? <StateView title="無法讀取 Podcast" description={error} tone="failure" action={<button type="button" className="secondary-button" onClick={() => setReload((v) => v + 1)}>重新讀取</button>} />
      : items === null ? <StateView title="正在讀取 Podcast" description="正在找回你保存的內容。" tone="loading" live />
      : items.length === 0 ? <StateView tone="empty" icon="headphones" title="讓教材說給你聽" description="挑選概念，選擇快速或完整模式。生成後保存在你的帳號，隨時可以再聽。" />
      : <>
        <div className="library-grid">{filtered?.map((item) => <article className="surface library-item podcast-tile" key={item.podcast_id}>
          <span className="podcast-cover" aria-hidden="true"><Icon name="headphones" size={32} /></span>
          <p className="library-metadata">{item.mode === "quick" ? "快速複習" : "完整講解"} · {item.episode_count} 集{item.delivery === "dialogue" ? " · 雙人對談" : ""}</p>
          <PodcastManagement item={item} apiClient={apiClient} onChanged={() => setReload(v => v + 1)} onDeleted={() => {
            try { localStorage.removeItem(`studydy.podcast.position:${learnerId}:${item.podcast_id}`); } catch { /* 不影響伺服器刪除。 */ }
            setItems(previous => previous?.filter(p => p.podcast_id !== item.podcast_id) ?? null);
            heading.current?.focus();
          }} />
          {!material && <p className="podcast-material-name" title={item.material_name}>{item.material_name}</p>}
          {item.status !== "ready" && <p className="library-state">{podcastStatus[item.status]} · 已完成 {item.completed_episodes} / {item.episode_count} 集</p>}
          {!item.is_current_revision && <small>保留建立時的教材版本</small>}
          <button type="button" className="primary-button" onClick={() => writeRoute({ name: "podcast", podcastId: item.podcast_id, materialId: item.material_id })}>{item.status === "ready" ? "開啟播放" : "查看進度"}</button>
        </article>)}</div>{filtered?.length === 0 && <p role="status">找不到符合的 Podcast。</p>}</>}
    </div>
  </section>;
}
