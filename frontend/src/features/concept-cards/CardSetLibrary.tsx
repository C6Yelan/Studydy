import { useEffect, useRef, useState, type ReactNode } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { CardSetSummary, MaterialLibraryItem } from "../../api/contracts";
import { writeRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";
import { CardSetManagement } from "./CardSetManagement";
import { useConceptArtwork } from "./VisualConceptCard";
import { StateView } from "../../ui/StateView";

export function CardSetLibrary({ apiClient, material, contentNavigation }: { apiClient: StudydyApiClient; material?: MaterialLibraryItem; contentNavigation?: ReactNode }) {
  const [sets, setSets] = useState<CardSetSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [query, setQuery] = useState("");
  const [removing, setRemoving] = useState<CardSetSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const restoreFocus = useRef(false);
  const deleting = useRef(false);
  const heading = useRef<HTMLDivElement>(null);
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
  const filtered = sets?.filter((item) => `${item.name} ${item.material_name}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  return (
    <section className="material-library is-collection cards-library" aria-label={material ? "教材概念卡" : "我的概念卡"}>
      {contentNavigation}
      <header className={`library-header library-header-compact collection-toolbar${material ? " material-search-row" : ""}`}>
          <form className="library-search" role="search" onSubmit={e => e.preventDefault()}><input type="search" aria-label={material ? "搜尋卡組" : "搜尋卡組或教材"} placeholder={material ? "搜尋卡組…" : "搜尋卡組或教材…"} value={query} onChange={e => setQuery(e.target.value)} onKeyDown={e => { if (e.key === "Escape") e.preventDefault(); }} /></form>
        <div className="collection-summary" ref={heading} tabIndex={-1}><p>{sets === null ? "—" : `已保存 ${sets.length} 個卡組`}</p></div>
        <button className="primary-button" type="button" disabled={!!material && !structure} onClick={create}><Icon name="cards" /> 建立卡組</button>
      </header>
      <div role={material ? "tabpanel" : undefined} id={material ? "material-panel-concept-cards" : undefined} aria-labelledby={material ? "material-tab-concept-cards" : undefined}>
      {error && sets !== null && <p className="form-error" role="alert">無法更新卡組列表。{error}<button className="text-button" onClick={()=>setReload(value=>value+1)}>重新讀取</button></p>}
      {error && sets === null ? <StateView title="無法讀取卡組" description={error} tone="failure" action={<button className="primary-button" type="button" onClick={() => setReload((n) => n + 1)}>重新讀取</button>} />
        : sets === null ? <StateView title="正在讀取卡組" description="正在載入你保存的概念卡。" tone="loading" live />
        : sets.length === 0 ? (
          <section className="library-empty surface" aria-label="空卡組引導"><div className="library-empty-illustration"><img src="/assets/studydy/empty-disappointed.png" alt="Studydy 坐在打開的空箱子旁" /></div><h2>收藏一組值得反覆看的重點</h2><p>選擇教材中的概念，建立你的第一組概念卡。</p><button className="primary-button" type="button" disabled={!!material && !structure} onClick={create}>{material ? "建立卡組" : "選擇教材"}</button></section>
        ) : <>

          <div className="library-grid">
            {filtered?.map((item) => (
              <article className="surface library-item" key={item.card_set_id} aria-label={item.name}>
                <span className="library-file-icon" aria-hidden="true"><Icon name="cards" size={25} /></span>
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
                <p className="library-metadata">{new Date(item.created_at).toLocaleDateString()} · <CardCount key={`${item.card_set_id}:${item.version}`} item={item} apiClient={apiClient} /></p>
                {!material && <p className="cards-library-material" title={item.material_name}>{item.material_name}</p>}
                {!item.is_current_revision && <p className="library-state">教材已有新版</p>}
                <div className="state-actions"><button className="primary-button" type="button" onClick={() => writeRoute({ name: "card-set", cardSetId: item.card_set_id, materialId: item.material_id })}>開始複習</button></div>
              </article>
            ))}
          </div>
          {filtered?.length === 0 && <p className="cards-no-results" role="status">找不到符合的卡組，試試其他關鍵字。</p>}
        </>}
      </div>
    </section>
  );
}

// 張數由瀏覽頁使用的同一套排版計算，不能拿選取概念數代替續圖後的張數。
function CardCount({ item, apiClient }: { item: CardSetSummary; apiClient: StudydyApiClient }) {
  const { artworkModule, loadFailed } = useConceptArtwork();
  const [count, setCount] = useState<number | null>(null);
  const [failed, setFailed] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setCount(null); setFailed(false);
    if (!artworkModule) return;
    void Promise.all([
      apiClient.getCardSet(item.card_set_id),
      apiClient.getKnowledgeStructure({ materialId: item.material_id, structureRevision: item.knowledge_structure_revision }),
    ]).then(([deck, structure]) => {
      if (!cancelled) setCount(deck.cards.reduce((total, card) => total + artworkModule.conceptCardPages(card, structure).length, 0));
    }, () => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; };
  }, [apiClient, artworkModule, item.card_set_id, item.material_id, item.knowledge_structure_revision, retry]);
  if (failed || loadFailed) return <span>張數載入失敗{!loadFailed && <button className="text-button" type="button" onClick={() => setRetry(n => n + 1)}>重試</button>}</span>;
  return <span>{count === null ? "正在計算張數…" : `${count} 張卡片`}</span>;
}
