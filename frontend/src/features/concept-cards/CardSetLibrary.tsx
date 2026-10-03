import { useEffect, useRef, useState, type ReactNode } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { CardSetSummary, MaterialLibraryItem } from "../../api/contracts";
import { writeRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";
import { CardSetManagement } from "./CardSetManagement";
import { StateView } from "../../ui/StateView";

export function CardSetLibrary({ apiClient, material, contentNavigation }: { apiClient: StudydyApiClient; material?: MaterialLibraryItem; contentNavigation?: ReactNode }) {
  const [sets, setSets] = useState<CardSetSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [query, setQuery] = useState("");
  const [removing, setRemoving] = useState<CardSetSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const heading = useRef<HTMLDivElement>(null);
  const active = useRef(false);
  const deleting = useRef(false);
  useEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, []);
  useEffect(() => {
    let cancelled = false;
    setError(null);
    void apiClient.listCardSets().then(
      (result) => { if (!cancelled) setSets(material ? result.card_sets.filter(c => c.material_id === material.material_id) : result.card_sets); },
      (failure) => { if (!cancelled) setError(errorMessage(failure)); },
    );
    return () => { cancelled = true; };
  }, [apiClient, reload, material?.material_id]);
  useEffect(() => { if (removing) dialog.current?.showModal(); }, [removing]);
  const remove = async () => {
    if (!removing || deleting.current) return;
    deleting.current = true;
    setBusy(true);
    setDeleteError(null);
    try {
      await apiClient.deleteCardSet(removing.card_set_id);
      if (!active.current) return;
      setSets((previous) => previous?.filter((item) => item.card_set_id !== removing.card_set_id) ?? null);
      dialog.current?.close();
      heading.current?.focus();
    } catch (failure) {
      if (active.current) setDeleteError(errorMessage(failure));
    } finally {
      deleting.current = false;
      if (active.current) setBusy(false);
    }
  };
  const structure = material?.available_structures.find(s => s.knowledge_structure_revision === material.head_revision);
  const create = () => { if (material && structure) writeRoute({ name: "card-set-create", materialId: material.material_id, runId: structure.run_id, structureRevision: structure.knowledge_structure_revision }); else if (!material) writeRoute({ name: "card-set-new" }); };
  const filtered = sets?.filter((item) => `${item.name} ${item.material_name}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  return (
    <section className="material-library is-collection cards-library" aria-label={material ? "教材概念卡" : "我的概念卡"}>
      <header className="library-header library-header-compact collection-toolbar">
        {contentNavigation}
        <form className="library-search" role="search" onSubmit={e => e.preventDefault()}><input type="search" aria-label={material ? "搜尋卡組" : "搜尋卡組或教材"} placeholder={material ? "搜尋卡組…" : "搜尋卡組或教材…"} value={query} onChange={e => setQuery(e.target.value)} onKeyDown={e => { if (e.key === "Escape") e.preventDefault(); }} /></form>
        <div className="collection-summary" ref={heading} tabIndex={-1}><p>{sets === null ? "正在讀取…" : `已保存 ${sets.length} 個卡組`}</p></div>
        <button className="primary-button" type="button" disabled={!!material && !structure} onClick={create}><Icon name="cards" /> 建立卡組</button>
      </header>
      {error ? <StateView title="無法讀取卡組" description={error} tone="failure" action={<button className="primary-button" type="button" onClick={() => setReload((n) => n + 1)}>重新讀取</button>} />
        : sets === null ? <StateView title="正在讀取卡組" description="正在載入你保存的概念卡。" tone="loading" live />
        : sets.length === 0 ? (
          <section className="library-empty surface" aria-label="空卡組引導"><div className="library-empty-illustration"><img src="/assets/studydy/empty-disappointed.png" alt="Studydy 坐在打開的空箱子旁" /></div><h2>收藏一組值得反覆看的重點</h2><p>選擇教材中的概念，建立你的第一組概念卡。</p><button className="primary-button" type="button" disabled={!!material && !structure} onClick={create}>{material ? "建立卡組" : "選擇教材"}</button></section>
        ) : <>

          <div className="library-grid">
            {filtered?.map((item) => (
              <article className="surface library-item" key={item.card_set_id} aria-label={item.name}>
                <span className="library-file-icon" aria-hidden="true"><Icon name="cards" size={25} /></span>
                <CardSetManagement item={item} materialId={item.material_id} apiClient={apiClient} onChanged={() => setReload(n => n + 1)}
                  onDelete={(element) => { opener.current = element; setDeleteError(null); setRemoving(item); }} />
                <p className="library-metadata">{new Date(item.created_at).toLocaleDateString()} · {item.card_count} 張卡片</p>
                {!material && <p className="cards-library-material" title={item.material_name}>{item.material_name}</p>}
                {!item.is_current_revision && <p className="library-state">教材已有新版</p>}
                <div className="state-actions"><button className="primary-button" type="button" onClick={() => writeRoute({ name: "card-set", cardSetId: item.card_set_id, materialId: item.material_id })}>開始複習</button></div>
              </article>
            ))}
          </div>
          {filtered?.length === 0 && <p className="cards-no-results" role="status">找不到符合的卡組，試試其他關鍵字。</p>}
        </>}
      {removing && <dialog className="cards-dialog" ref={dialog} aria-labelledby="delete-card-set-title" onCancel={(event) => { if (deleting.current) event.preventDefault(); }} onClose={() => { setRemoving(null); opener.current?.isConnected && opener.current.focus(); }}>
        <h2 id="delete-card-set-title">刪除這個卡組？</h2><p>「{removing.name}」將從概念卡列表移除。原教材與學習紀錄會保留。</p>
        {deleteError && <p className="form-error" role="alert">{deleteError}</p>}
        <div className="state-actions"><button className="secondary-button" type="button" autoFocus disabled={busy} onClick={() => dialog.current?.close()}>取消</button><button className="primary-button cards-danger" type="button" disabled={busy} onClick={() => void remove()}>{busy ? "正在刪除…" : "確認刪除卡組"}</button></div>
      </dialog>}
    </section>
  );
}
