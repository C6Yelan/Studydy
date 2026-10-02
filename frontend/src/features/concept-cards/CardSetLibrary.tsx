import { useEffect, useRef, useState } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { CardSetSummary, MaterialLibraryItem } from "../../api/contracts";
import { writeRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";
import { CardSetManagement } from "./CardSetManagement";
import { StateView } from "../../ui/StateView";

export function CardSetLibrary({ apiClient }: { apiClient: StudydyApiClient }) {
  const [sets, setSets] = useState<CardSetSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [query, setQuery] = useState("");
  const [choosing, setChoosing] = useState(false);
  const [removing, setRemoving] = useState<CardSetSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
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
      (result) => { if (!cancelled) setSets(result.card_sets); },
      (failure) => { if (!cancelled) setError(errorMessage(failure)); },
    );
    return () => { cancelled = true; };
  }, [apiClient, reload]);
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
  const filtered = sets?.filter((item) => `${item.name} ${item.material_name}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  return (
    <section className="material-library is-collection cards-library">
      <header className="library-header">
        <div><h1 ref={heading} tabIndex={-1}>我的概念卡</h1><p className="library-subtitle">{sets?.length ? `已保存 ${sets.length} 個卡組，隨時接續你的複習。` : "選擇教材中的概念，建立自己的複習卡組。"}</p></div>
        <button className="primary-button" type="button" onClick={() => setChoosing(!choosing)}><Icon name="cards" /> 建立卡組</button>
      </header>
      {choosing && <MaterialPicker apiClient={apiClient} onClose={() => setChoosing(false)} />}
      {error ? <StateView title="無法讀取卡組" description={error} tone="failure" action={<button className="primary-button" type="button" onClick={() => setReload((n) => n + 1)}>重新讀取</button>} />
        : sets === null ? <StateView title="正在讀取卡組" description="正在載入你保存的概念卡。" tone="loading" live />
        : sets.length === 0 ? (
          <section className="library-empty surface" aria-label="空卡組引導"><div className="library-empty-illustration"><img src="/assets/studydy/empty-disappointed.png" alt="Studydy 坐在打開的空箱子旁" /></div><h2>收藏一組值得反覆看的重點</h2><p>選擇教材中的概念，建立你的第一組概念卡。</p><button className="primary-button" type="button" onClick={() => setChoosing(true)}>選擇教材</button></section>
        ) : <>
          <form className="library-search" role="search" onSubmit={(event) => event.preventDefault()}><input type="search" aria-label="搜尋卡組或教材" placeholder="搜尋卡組或教材…" value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Escape") event.preventDefault(); }} /></form>
          <div className="library-grid">
            {filtered?.map((item) => (
              <article className="surface library-item" key={item.card_set_id} aria-label={item.name}>
                <span className="library-file-icon" aria-hidden="true"><Icon name="cards" size={25} /></span>
                <CardSetManagement item={item}
                  onDelete={(element) => { opener.current = element; setDeleteError(null); setRemoving(item); }} />
                <p className="library-metadata">{new Date(item.created_at).toLocaleDateString()} · {item.card_count} 張卡片</p>
                <p className="cards-library-material">{item.material_name}</p>
                {!item.is_current_revision && <p className="library-state">教材已有新版</p>}
                <div className="state-actions"><button className="primary-button" type="button" onClick={() => writeRoute({ name: "card-set", cardSetId: item.card_set_id })}>開始複習</button><button className="secondary-button" type="button" onClick={() => writeRoute({ name: "card-set-edit", cardSetId: item.card_set_id })}>管理卡組</button></div>
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

function MaterialPicker({ apiClient, onClose }: { apiClient: StudydyApiClient; onClose: () => void }) {
  const [materials, setMaterials] = useState<MaterialLibraryItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setError(null);
    void apiClient.listMaterials().then((result) => {
      if (!cancelled) setMaterials(result.materials.filter((item) => item.available_structures.some((view) => view.knowledge_structure_revision === item.head_revision)));
    }, (failure) => { if (!cancelled) setError(errorMessage(failure)); });
    return () => { cancelled = true; };
  }, [apiClient, reload]);
  return <section className="cards-material-picker" aria-label="選擇教材">
    <header><h2>從哪份教材開始？</h2><button className="text-button" type="button" onClick={onClose}>收起</button></header>
    {error ? <p role="alert">{error} <button type="button" className="text-button" onClick={() => setReload((n) => n + 1)}>重新讀取</button></p>
      : materials === null ? <p role="status">正在讀取教材…</p>
      : materials.length === 0 ? <p>目前沒有已完成分析的教材。<button className="text-button" type="button" onClick={() => writeRoute({ name: "materials" })}>前往我的教材</button></p>
      : <div>{materials.map((item) => <button type="button" key={item.material_id} onClick={() => {
        const head = item.available_structures.find((view) => view.knowledge_structure_revision === item.head_revision)!;
        writeRoute({ name: "card-set-create", materialId: item.material_id, runId: head.run_id, structureRevision: head.knowledge_structure_revision });
      }}><Icon name="book" /><span>{item.display_name}</span><Icon name="chevron-right" size={18} /></button>)}</div>}
  </section>;
}
