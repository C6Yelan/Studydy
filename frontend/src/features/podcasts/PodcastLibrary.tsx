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

// 可播放且沒有進行中或失敗的影片（沒勾影片或已刪除影片的分集只播音訊）。
const podcastComplete = (item: PodcastSummary) => podcastPreviewReady(item) && !item.generation!.active;

// 預期會有影片的集數；舊回應沒有 expected_videos 時依是否勾選影片推算。
const videoTotal = (item: PodcastSummary) => item.generation?.expected_videos ?? (item.video_enabled ? item.episode_count : 0);

export const podcastStatus = { pending: "等待生成", running: "正在生成", ready: "可以播放", failed: "生成中斷", cancelled: "已取消" };

export function PodcastLibrary({ apiClient, learnerId, material, contentNavigation }: { apiClient: StudydyApiClient; learnerId: string; material?: MaterialLibraryItem; contentNavigation?: ReactNode }) {
  const [items, setItems] = useState<PodcastSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [deletingId, setDeletingId] = useState<string|null>(null);
  const heading = useRef<HTMLHeadingElement & HTMLDivElement>(null);
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
  return <section className="material-library is-collection podcast-library" aria-label={material ? "教材 Podcast" : "我的 Podcast"}>
    {contentNavigation}
    {material ? <header className="library-header library-header-compact collection-toolbar material-search-row">
      <div className="collection-summary" ref={heading} tabIndex={-1}><p>{items === null ? "—" : `已保存 ${items.length} 份 Podcast`}</p></div>
      <button className="primary-button" type="button" disabled={!structure} onClick={create}><Icon name="headphones" size={18} /> 建立 Podcast</button>
    </header> : <CollectionHeader title="我的 Podcast"
      subtitle={<>{items !== null && <><b>已保存 {items.length} 份 Podcast</b> · </>}把教材重點變成可以隨時收聽的節目，收聽進度會自動保存。</>}
      actions={<button className="primary-button" type="button" onClick={create}><Icon name="headphones" size={18} /> 建立 Podcast</button>}
      headingRef={heading} />}
      <div role={material ? "tabpanel" : undefined} id={material ? "material-panel-podcasts" : undefined} aria-labelledby={material ? "material-tab-podcasts" : undefined}>
    {error && items !== null && <p className="form-error" role="alert">無法更新 Podcast 列表。{error}<button className="text-button" onClick={()=>setReload(value=>value+1)}>重新讀取</button></p>}
    {error && items === null ? <StateView title="無法讀取 Podcast" description={error} tone="failure" action={<button type="button" className="secondary-button" onClick={() => setReload((v) => v + 1)}>重新讀取</button>} />
      : items === null ? <StateView title="正在讀取 Podcast" description="正在找回你保存的內容。" tone="loading" live skeleton="library" />
      : items.length === 0 ? <EmptyState title="讓教材說給你聽" description="挑選概念，選擇快速或完整模式。生成後保存在你的帳號，隨時可以再聽。"
        action={<button className="primary-button" type="button" disabled={!!material && !structure} onClick={create}><Icon name="headphones" size={18} /> {material ? "建立 Podcast" : "選擇教材"}</button>} />
      : <>
        <div className="library-grid">{items?.map((item) => <article className="surface library-item podcast-tile" key={item.podcast_id}>
          <PodcastCover item={item} />
          {/* 封面只是裝飾（aria-hidden），形式、集數與總時長另以文字提供給螢幕閱讀器。 */}
          <p className="sr-only">{item.delivery === "dialogue" ? "雙人對談" : "單人解說"} · {item.episode_count} 集{item.duration_seconds !== null && ` · 共 ${podcastLength(item.duration_seconds)}`}</p>
          <PodcastManagement item={item} apiClient={apiClient} onChanged={() => {mutationVersion.current++;setReload(v => v + 1);}} onDeleted={() => {
            mutationVersion.current++;
            try { localStorage.removeItem(`studydy.podcast.position:${learnerId}:${item.podcast_id}`); } catch { /* 不影響伺服器刪除。 */ }
            setItems(previous => previous?.filter(p => p.podcast_id !== item.podcast_id) ?? null);
            setReload(v=>v+1);heading.current?.focus();
          }} />
          {!material && <p className="podcast-material-name" title={item.material_name}>{item.material_name}</p>}
          {/* 全部完成時不顯示狀態（按鈕已是「開啟播放」）；生成中、中斷或部分影片失敗時才顯示進度。 */}
          {item.generation ? !podcastComplete(item) && <p className="library-state">{item.generation.active ? (item.status === 'ready' ? (videoTotal(item) ? '影片／字幕生成中' : '字幕生成中') : podcastStatus[item.status]) : item.generation.failed_videos ? '部分影片生成失敗' : podcastStatus[item.status]} · 音訊 {item.completed_episodes} / {item.episode_count} 集{videoTotal(item) ? ` · 影片 ${item.generation.completed_videos} / ${videoTotal(item)} 集` : ''}</p>
            : item.status !== "ready" && <p className="library-state">{podcastStatus[item.status]} · 音訊已完成 {item.completed_episodes} / {item.episode_count} 集</p>}
          {(item.generation?.active || ['pending','running'].includes(item.status)) && <PodcastCancelControl busy={deletingId!==null} onConfirm={()=>void cancelAndDelete(item.podcast_id)}/>}
          <button type="button" className="primary-button" onClick={() => writeRoute({ name: "podcast", podcastId: item.podcast_id, materialId: item.material_id }, false, material ? null : { fromCollection: true })}>{podcastPreviewReady(item) ? "開啟播放" : "查看進度"}</button>
        </article>)}</div></>}
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
    <i>{item.episode_count} 集{item.duration_seconds !== null && ` · ${podcastLength(item.duration_seconds)}`}</i>
  </span>;
}

// 總時長以分鐘顯示（不足 1 分鐘算 1 分），滿 60 分鐘改成「N 小時 M 分」。
export function podcastLength(seconds: number) {
  const minutes = Math.max(1, Math.round(seconds / 60));
  if (minutes < 60) return `${minutes} 分`;
  return `${Math.floor(minutes / 60)} 小時${minutes % 60 ? ` ${minutes % 60} 分` : ""}`;
}
