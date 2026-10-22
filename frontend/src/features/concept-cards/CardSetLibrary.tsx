import { useCallback, useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { CardReviewsView, CardSetSummary, MaterialLibraryItem } from "../../api/contracts";
import { writeRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";
import { islandColors, islandPalette } from "../../ui/art/Island";
import { CardSetManagement } from "./CardSetManagement";
import { ReviewDeck } from "./ReviewDeck";
import { useConceptArtwork } from "./VisualConceptCard";
import { StateView } from "../../ui/StateView";
import { CollectionHeader } from "../../ui/CollectionHeader";
import { EmptyState } from "../../ui/EmptyState";

export function CardSetLibrary({ apiClient, material, contentNavigation }: { apiClient: StudydyApiClient; material?: MaterialLibraryItem; contentNavigation?: ReactNode }) {
  const [sets, setSets] = useState<CardSetSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  // 各卡組需要加強（標成不熟）的張數；讀取失敗只少顯示張數，不影響卡組列表。
  const [weak, setWeak] = useState<Map<string, number> | null>(null);
  const applySummary = useCallback((summary: CardReviewsView) => {
    setWeak(new Map(summary.sets.map(set => [set.card_set_id, set.forgot_count])));
  }, []);
  useEffect(() => {
    let cancelled = false;
    void apiClient.getCardReviews().then(summary => { if (!cancelled) applySummary(summary); }, () => undefined);
    return () => { cancelled = true; };
  }, [apiClient, reload, applySummary]);
  const [strengthening, setStrengthening] = useState(false);
  const [removing, setRemoving] = useState<CardSetSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const restoreFocus = useRef(false);
  const deleting = useRef(false);
  const heading = useRef<HTMLHeadingElement & HTMLDivElement>(null);
  const active = useRef(false);
  const mutationVersion = useRef(0);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    let cancelled = false;
    const version = mutationVersion.current;
    setError(null);
    void apiClient.listCardSets().then(
      (result) => { if (!cancelled && version === mutationVersion.current) setSets(material ? result.card_sets.filter(c => c.material_id === material.material_id) : result.card_sets); },
      (failure) => { if (!cancelled && version === mutationVersion.current) setError(errorMessage(failure)); },
    );
    return () => { cancelled = true; };
  }, [apiClient, reload, material?.material_id]);
  useEffect(() => {
    if (!sets?.some(item => ["pending", "running"].includes(item.generation.status))) return;
    const timer = setTimeout(() => setReload(value => value + 1), 2000);
    return () => clearTimeout(timer);
  }, [sets]);
  const countSaved = useCallback((item: CardSetSummary) => {
    setSets(previous => previous?.map(current => current.card_set_id === item.card_set_id && current.version <= item.version ? item : current) ?? null);
  }, []);
  useEffect(() => {
    if (removing) cancel.current?.focus();
    else if (restoreFocus.current && opener.current?.isConnected) opener.current.focus({ preventScroll: true });
  }, [removing]);
  useEffect(() => { if (deleteError && !busy) cancel.current?.focus(); }, [deleteError, busy]);
  const closeDelete = () => {
    if (deleting.current) return;
    restoreFocus.current = true;
    setDeleteError(null); setRemoving(null);
  };
  const remove = async () => {
    if (!removing || deleting.current) return;
    deleting.current = true; setBusy(true); setDeleteError(null);
    try {
      await apiClient.deleteCardSet(removing.card_set_id);
      if (!active.current) return;
      mutationVersion.current++;
      setSets(previous => previous?.filter(item => item.card_set_id !== removing.card_set_id) ?? null);
      restoreFocus.current = false;
      setRemoving(null); setReload(value => value + 1);
      const focused = document.activeElement;
      if (focused === document.body || opener.current?.closest("article")?.contains(focused)) heading.current?.focus({ preventScroll: true });
    } catch (failure) { if (active.current) setDeleteError(`無法刪除卡組。${errorMessage(failure)}`); }
    finally { deleting.current = false; if (active.current) setBusy(false); }
  };
  const structure = material?.available_structures.find(s => s.knowledge_structure_revision === material.head_revision);
  const create = () => { if (material && structure) writeRoute({ name: "card-set-create", materialId: material.material_id, runId: structure.run_id, structureRevision: structure.knowledge_structure_revision }); else if (!material) writeRoute({ name: "card-set-new" }); };
  const weakTotal = weak ? [...weak.values()].reduce((sum, count) => sum + count, 0) : 0;
  return (
    <section className="material-library is-collection cards-library" aria-label={material ? "教材概念卡" : "我的概念卡"}>
      {contentNavigation}
      {material ? <header className="library-header library-header-compact collection-toolbar material-search-row">
        <div className="collection-summary" ref={heading} tabIndex={-1}><p>{sets === null ? "—" : `已保存 ${sets.length} 個卡組`}</p></div>
        <button className="primary-button" type="button" disabled={!structure} onClick={create}><Icon name="cards" /> 建立卡組</button>
      </header> : <CollectionHeader title="我的概念卡" headingRef={heading}
        subtitle={<>{sets !== null && <><b>已保存 {sets.length} 個卡組</b> · </>}收藏一組值得反覆看的重點，把不熟的卡片標起來加強。</>}
        actions={<button className="primary-button" type="button" onClick={create}><Icon name="cards" /> 建立卡組</button>} />}
      <div role={material ? "tabpanel" : undefined} id={material ? "material-panel-concept-cards" : undefined} aria-labelledby={material ? "material-tab-concept-cards" : undefined}>
      {error && sets !== null && <p className="form-error" role="alert">無法更新卡組列表。{error}<button className="text-button" onClick={()=>setReload(value=>value+1)}>重新讀取</button></p>}
      {error && sets === null ? <StateView title="無法讀取卡組" description={error} tone="failure" action={<button className="primary-button" type="button" onClick={() => setReload((n) => n + 1)}>重新讀取</button>} />
        : sets === null ? <StateView title="正在讀取卡組" description="正在載入你保存的概念卡。" tone="loading" live skeleton="library" />
        : sets.length === 0 ? (
          <EmptyState label="空卡組引導" title={material ? "這份教材還沒有卡組" : "建立你的第一組概念卡"} description="選擇教材中的概念，建立你的第一組概念卡。"
            action={<button className="primary-button" type="button" disabled={!!material && !structure} onClick={create}>{material ? "建立卡組" : "選擇教材"}</button>} />
        ) : <>
          {/* 跨卡組的需要加強：平常只佔一列，按「開始加強」才展開翻卡。 */}
          {!material && <section className={`cards-strengthen surface${strengthening ? " is-open" : ""}`} aria-labelledby="cards-strengthen-title">
            <div className="cards-strengthen-bar">
              <img src="/assets/Studydy_角色素材/引導/guide_present.png" alt="" />
              <p><b id="cards-strengthen-title">需要加強</b>
                {weak === null ? <span>—</span> : weakTotal ? <span>{weakTotal} 張卡片標成不熟</span> : <span>目前沒有，瀏覽卡組時把不熟的卡片標起來</span>}</p>
              {(strengthening || weakTotal > 0) && <button className={strengthening ? "secondary-button" : "primary-button"} type="button" onClick={() => setStrengthening(value => !value)}>
                {strengthening ? "收起" : "開始加強"}{!strengthening && <Icon name="chevron-right" size={18} />}
              </button>}
            </div>
            {strengthening && <ReviewDeck apiClient={apiClient} variant="home" onChange={(summary) => {
              applySummary(summary);
              // 標記後重新讀取學習活動，讓頁首經驗值與今日任務跟上（失敗時保留原畫面）。
              void apiClient.getLearningActivity().catch(() => undefined);
            }} />}
          </section>}

          <div className="library-grid">
            {sets.map((item) => (
              <article className="surface library-item" key={item.card_set_id} aria-label={item.name}>
                <CardSetArt item={item} />
                <CardSetManagement item={item} materialId={item.material_id} apiClient={apiClient} onChanged={() => setReload(n => n + 1)}
                  confirmingDelete={removing?.card_set_id === item.card_set_id}
                  onDelete={element => {
                    if (deleting.current) return;
                    opener.current = element; restoreFocus.current = false;
                    setDeleteError(null); setRemoving(item);
                  }} />
                {removing?.card_set_id === item.card_set_id && <form className="material-management-form" aria-label="刪除卡組確認" aria-busy={busy}
                  onSubmit={event => { event.preventDefault(); void remove(); }}
                  onKeyDown={event => { if (event.key === "Escape") { event.preventDefault(); closeDelete(); } }}>
                  <h3>確定刪除？</h3>
                  <div className="material-management-actions">
                    <button ref={cancel} className="secondary-button" type="button" disabled={busy} onClick={closeDelete}>取消</button>
                    <button className="secondary-button cancel-confirm-button" type="submit" aria-label={busy ? "正在刪除…" : "確認刪除卡組"} disabled={busy}>{busy ? "正在刪除…" : "刪除"}</button>
                  </div>
                  {deleteError && <p className="form-error" role="alert">{deleteError}</p>}
                </form>}
                <p className="library-metadata">{new Date(item.created_at).toLocaleDateString()} · <CardCount key={`${item.card_set_id}:${item.version}`} item={item} apiClient={apiClient} onSaved={countSaved} /></p>
                {!material && <p className="cards-library-material" title={item.material_name}>{item.material_name}</p>}
                {!item.is_current_revision && (item.moved_to_card_set_id
                  ? <p className="library-state is-version">已移到新版（保留舊版卡面）</p>
                  // 到教材頁才有「移到新版」；標籤直接帶過去。
                  : <button type="button" className="library-state is-version" onClick={() => writeRoute({ name: "card-set", cardSetId: item.card_set_id, materialId: item.material_id })}>教材已有新版 · 查看移到新版</button>)}
                {(weak?.get(item.card_set_id) ?? 0) > 0 && <p className="card-review-due"><Icon name="refresh" size={14} />需要加強 {weak!.get(item.card_set_id)} 張</p>}
                <div className="state-actions"><button className="primary-button" type="button" onClick={() => writeRoute({ name: "card-set", cardSetId: item.card_set_id, materialId: item.material_id })}>開始複習</button></div>
              </article>
            ))}
          </div>
        </>}
      </div>
    </section>
  );
}

// 卡組封面：疊起來的卡片，顏色沿用所屬教材的島嶼配色。
function CardSetArt({ item }: { item: CardSetSummary }) {
  const colors = islandColors(islandPalette(item.material_id));
  return <span className="card-set-art" aria-hidden="true" style={{ "--card-from": colors.c2, "--card-to": colors.c3 } as CSSProperties}>
    <span><b>{item.name}</b></span>
  </span>;
}

type CountProps = { item: CardSetSummary; apiClient: StudydyApiClient; onSaved: (item: CardSetSummary) => void };

function CardCount(props: CountProps) {
  const { item } = props;
  if (item.card_count !== null) return <span>{item.card_count} 張卡片</span>;
  if (item.generation.status !== "ready") return <span>{item.generation.status === "failed" ? "生成暫停 · 開啟後可重試" : `生成中 · ${item.generation.completed} / ${item.generation.total} 個觀念`}</span>;
  return <UncountedCards {...props} />;
}

// 僅尚未保存張數的版本執行一次實際排版；列表不再逐次下載教材及載入繪圖模組。
function UncountedCards({ item, apiClient, onSaved }: CountProps) {
  const { artworkModule, loadFailed } = useConceptArtwork();
  const [failed, setFailed] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setFailed(false);
    if (!artworkModule) return;
    void (async () => {
      const study = await apiClient.getCardStudy(item.card_set_id);
      if (cancelled) return;
      if (study.generation.status !== "ready" || study.card_count !== null) { onSaved(study); return; }
      let saved: CardSetSummary = study;
      for (let offset = 0; offset < study.navigation.length; offset += 8) {
        if (cancelled) return;
        if (study.navigation.slice(offset, offset + 8).every(n => n.page_count !== null)) continue;
        const batch = offset === 0 ? study.batch! : await apiClient.getCardBatch(item.card_set_id, study.version, offset);
        if (cancelled) return;
        if (batch.context.source_resolver !== study.source_resolver || batch.cards.some((card, i) => card.concept_id !== study.navigation[offset + i]?.concept_id)) throw new Error("卡片版本不一致。");
        const counts: Record<string, number> = {};
        for (const card of batch.cards) {
          if (cancelled) return;
          counts[card.concept_id] = artworkModule.conceptCardPages(card, batch.context).length;
          await new Promise(resolve => setTimeout(resolve, 0));
        }
        if (cancelled) return;
        saved = await apiClient.saveConceptCounts(item.card_set_id, study.version, counts);
      }
      if (!cancelled) onSaved(saved);
    })().catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; };
  }, [apiClient, artworkModule, item.card_set_id, item.material_id, item.knowledge_structure_revision, retry, onSaved]);
  if (failed || loadFailed) return <span>張數計算或保存失敗{!loadFailed && <button className="text-button" type="button" onClick={() => setRetry(n => n + 1)}>重試</button>}</span>;
  return <span>首次計算張數…</span>;
}
