import { useCallback, useEffect, useRef, useState } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { CardSetCardsView, CardSetInput, CardSetView, KnowledgeStructureView, MaterialLibraryItem } from "../../api/contracts";
import { writeRoute, type CardSetRoute } from "../../app/routes";
import { StateView } from "../../ui/StateView";
import { Flashcard } from "./Flashcard";
import "./styles.css";

function useCurrentPage() {
  const active = useRef(true);
  const path = useRef(window.location.pathname);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  return useCallback(() => active.current && path.current === window.location.pathname, []);
}

function LoadState({ error, retry, materialId }: { error: string | null; retry: () => void; materialId: string }) {
  return <StateView title={error ? "無法讀取圖卡組" : "正在讀取觀念圖卡"}
    description={error ?? "正在載入已保存的卡組與教材。"} tone={error ? "failure" : "loading"} live={!error}
    action={error && <div className="state-actions">
      <button className="primary-button" onClick={retry}>重新讀取</button>
      <button className="secondary-button" onClick={() => writeRoute({ name: "card-sets", materialId, view: "list" })}>我的圖卡組</button>
      <button className="text-button" onClick={() => writeRoute({ name: "materials" })}>返回教材庫</button>
    </div>} />;
}

function CardSetList({ apiClient, materialId }: { apiClient: StudydyApiClient; materialId: string }) {
  const [sets, setSets] = useState<CardSetView[] | null>(null);
  const [material, setMaterial] = useState<MaterialLibraryItem | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [deleting, setDeleting] = useState<CardSetView | null>(null);
  const [busy, setBusy] = useState(false);
  const current = useCurrentPage();
  const cancelDelete = useRef<HTMLButtonElement>(null);
  const title = useRef<HTMLHeadingElement>(null);
  useEffect(() => { if (deleting) cancelDelete.current?.focus(); }, [deleting]);
  useEffect(() => {
    let cancelled = false;
    setError(null);
    void Promise.all([apiClient.listCardSets(materialId), apiClient.getMaterial(materialId)]).then(([list, item]) => {
      if (!cancelled) { setSets(list.card_sets); setMaterial(item); setDeleting(null); }
    }, e => { if (!cancelled) setError(errorMessage(e)); });
    return () => { cancelled = true; };
  }, [apiClient, materialId, reload]);
  const remove = async () => {
    if (!deleting || busy) return;
    setBusy(true); setError(null);
    try {
      await apiClient.deleteCardSet(deleting);
      if (!current()) return;
      setSets(rows => rows!.filter(row => row.card_set_id !== deleting.card_set_id));
      setDeleting(null); title.current?.focus();
    } catch (e) { if (current()) setError(errorMessage(e)); }
    finally { if (current()) setBusy(false); }
  };
  if (!sets || !material) return <LoadState error={error} retry={() => setReload(n => n + 1)} materialId={materialId} />;
  const canCreate = material.available_structures.some(s => s.knowledge_structure_revision === material.head_revision);
  return <section className="card-area">
    <button className="text-button" onClick={() => writeRoute({ name: "materials" })}>← 返回教材庫</button>
    <header className="card-area-header">
      <div><p className="card-eyebrow">觀念圖卡 · {material.display_name}</p><h1 ref={title} tabIndex={-1}>我的圖卡組</h1>
        <p>把想複習的概念收成一組，隨時回來翻閱。</p></div>
      <button className="primary-button" disabled={!canCreate} onClick={() => writeRoute({ name: "card-sets", materialId, view: "new" })}>＋ 建立圖卡組</button>
    </header>
    {!canCreate && <p role="status">教材尚無可用的發布版本，暫時無法建立新卡組。</p>}
    {error && <div role="alert" className="form-error"><p>{error}</p><button className="text-button" onClick={() => setReload(n => n + 1)}>重新讀取</button></div>}
    {!sets.length && <div className="card-set-empty surface"><span aria-hidden="true" className="empty-card-stack">▱</span>
      <h2>還沒有圖卡組</h2><p>建立第一組，把這份教材的重點帶進下一次複習。</p></div>}
    <div className="card-set-list">
      {sets.map(set => <article className="card-set-tile surface" key={set.card_set_id} aria-label={set.name}>
        <span className="deck-mark" aria-hidden="true">▱</span>
        <div><h2>{set.name}</h2><p>{set.concept_ids.length} 張圖卡 · 教材發布順序</p>
          <p className="card-muted">上次停在第 {set.current_position + 1} / {set.concept_ids.length} 張</p></div>
        {deleting?.card_set_id === set.card_set_id ? <div className="card-delete-confirm" role="group" aria-label="確認刪除圖卡組">
          <p>刪除這個圖卡組？教材與學習紀錄會保留。</p>
          <div className="state-actions"><button ref={cancelDelete} className="secondary-button" disabled={busy} onClick={() => setDeleting(null)}>取消</button>
            <button className="primary-button" disabled={busy} onClick={() => void remove()}>{busy ? "正在刪除…" : "確認刪除"}</button></div>
        </div> : <div className="state-actions">
          <button className="primary-button" onClick={() => writeRoute({ name: "card-sets", materialId, view: "study", cardSetId: set.card_set_id })}>繼續複習</button>
          <button className="secondary-button" onClick={() => writeRoute({ name: "card-sets", materialId, view: "edit", cardSetId: set.card_set_id })}>編輯</button>
          <button className="text-button" onClick={() => setDeleting(set)}>刪除</button>
        </div>}
      </article>)}
    </div>
  </section>;
}

function CardSetEditor({ apiClient, materialId, cardSetId }: { apiClient: StudydyApiClient; materialId: string; cardSetId?: string }) {
  const [data, setData] = useState<{ map: KnowledgeStructureView; saved: CardSetView | null; material: MaterialLibraryItem } | null>(null);
  const [name, setName] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const intent = useRef<{ body: string; key: string } | null>(null);
  const current = useCurrentPage();
  useEffect(() => {
    let cancelled = false;
    setData(null); setError(null);
    void (async () => {
      try {
        const [material, saved] = await Promise.all([apiClient.getMaterial(materialId), cardSetId ? apiClient.readCardSet(materialId, cardSetId) : Promise.resolve(null)]);
        const revision = saved?.knowledge_structure_revision ?? material.head_revision;
        if (!revision) throw new Error("教材尚無已發布版本。");
        const map = await apiClient.getKnowledgeStructure({ materialId, structureRevision: revision });
        if (cancelled) return;
        setData({ map, saved, material }); setName(saved?.name ?? ""); setSelected(saved?.concept_ids ?? []);
      } catch (e) { if (!cancelled) setError(errorMessage(e)); }
    })();
    return () => { cancelled = true; };
  }, [apiClient, materialId, cardSetId, reload]);
  const save = async () => {
    if (!data || busy || !name.trim() || !selected.length) return;
    const input: CardSetInput = { name: name.trim(), knowledge_structure_revision: data.map.knowledge_structure_revision,
      concept_ids: data.map.concepts.filter(c => selected.includes(c.concept_id)).map(c => c.concept_id), ordering_policy: "published_order" };
    const body = JSON.stringify(input);
    if (intent.current?.body !== body) intent.current = { body, key: crypto.randomUUID() };
    setBusy(true); setError(null);
    try {
      const row = data.saved ? await apiClient.editCardSet(data.saved, input) : await apiClient.createCardSet(materialId, input, intent.current.key);
      if (current()) writeRoute({ name: "card-sets", materialId, view: "study", cardSetId: row.card_set_id });
    } catch (e) { if (current()) setError(errorMessage(e)); }
    finally { if (current()) setBusy(false); }
  };
  if (!data) return <LoadState error={error} retry={() => setReload(n => n + 1)} materialId={materialId} />;
  const query = search.trim().toLocaleLowerCase();
  const filtered = data.map.concepts.filter(c => [c.label, ...c.aliases].some(text => text.toLocaleLowerCase().includes(query)));
  return <section className="card-area card-editor">
    <button className="text-button" onClick={() => writeRoute({ name: "card-sets", materialId, view: "list" })}>← 我的圖卡組</button>
    <p className="card-eyebrow">{data.material.display_name}</p><h1>{data.saved ? "編輯圖卡組" : "建立圖卡組"}</h1>
    <form className="surface card-editor-form" onSubmit={event => { event.preventDefault(); void save(); }}>
      <label htmlFor="card-set-name">圖卡組名稱</label>
      <input id="card-set-name" autoFocus value={name} maxLength={200} required disabled={busy} placeholder="例如：第三章重點複習" onChange={e => setName(e.target.value)} />
      <label htmlFor="card-concept-search">選擇內容</label>
      <input id="card-concept-search" type="search" value={search} disabled={busy} placeholder="搜尋概念…" onChange={e => setSearch(e.target.value)} />
      <fieldset className="card-selection" disabled={busy}>
        <legend className="visually-hidden">選擇概念</legend>
        {filtered.map(c => <label key={c.concept_id}><input type="checkbox" checked={selected.includes(c.concept_id)}
          onChange={e => setSelected(ids => e.target.checked ? [...ids, c.concept_id] : ids.filter(id => id !== c.concept_id))} /><span>{c.label}</span></label>)}
        {!filtered.length && <p>找不到符合的概念。</p>}
      </fieldset>
      <p role="status">已選 {selected.length} 個概念</p>
      <label htmlFor="card-order">複習順序</label>
      <select id="card-order" disabled={busy} value="published_order" onChange={() => {}}><option value="published_order">教材發布順序</option></select>
      <p className="card-muted">卡組固定使用這個教材版本。修改選取內容會從第一張重新開始。</p>
      {error && <div className="form-error" role="alert"><p>{error}</p><button className="text-button" type="button" onClick={() => setReload(n => n + 1)}>重新讀取</button></div>}
      <div className="state-actions"><button className="primary-button" disabled={busy || !name.trim() || !selected.length} type="submit">
        {busy ? "正在儲存…" : data.saved ? "儲存圖卡組" : "建立圖卡組"}</button>
        <button className="secondary-button" type="button" onClick={() => writeRoute({ name: "card-sets", materialId, view: "list" })}>取消</button></div>
    </form>
  </section>;
}

function CardSetStudy({ apiClient, materialId, cardSetId }: { apiClient: StudydyApiClient; materialId: string; cardSetId: string }) {
  const [data, setData] = useState<CardSetCardsView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const [turn, setTurn] = useState(0);
  const current = useCurrentPage();
  useEffect(() => {
    let cancelled = false;
    setData(null); setError(null);
    void apiClient.readCardSetCards(materialId, cardSetId).then(value => {
      if (!cancelled) setData(value);
    }, e => { if (!cancelled) setError(errorMessage(e)); });
    return () => { cancelled = true; };
  }, [apiClient, materialId, cardSetId, reload]);
  const move = async (position: number) => {
    if (!data || busy) return;
    setBusy(true); setError(null);
    try {
      const saved = await apiClient.setCardPosition(data.card_set, position);
      if (current()) { setData({ ...data, card_set: saved }); setTurn(n => n + 1); }
    } catch (e) { if (current()) setError(errorMessage(e)); }
    finally { if (current()) setBusy(false); }
  };
  if (!data) return <LoadState error={error} retry={() => setReload(n => n + 1)} materialId={materialId} />;
  const { card_set: set, cards } = data;
  const position = set.current_position;
  return <section className="card-area card-study">
    <header className="card-study-heading"><p className="card-eyebrow">觀念圖卡</p><h1>{set.name}</h1>
      <p className="card-muted">先想一想，再翻面查看教材重點。</p></header>
    <p className="flashcard-progress" role="status" aria-label="複習進度">第 {position + 1} / {cards.cards.length} 張{busy ? " · 正在保存位置…" : ""}</p>
    <Flashcard key={`${set.card_set_id}/${position}/${turn}`} card={cards.cards[position]} view={cards} apiClient={apiClient} />
    {error && <div role="alert" className="form-error"><p>{error}</p><button className="text-button" onClick={() => setReload(n => n + 1)}>重新讀取卡組</button></div>}
    <nav className="flashcard-controls" aria-label="圖卡翻閱">
      <button className="secondary-button" disabled={busy || position === 0} onClick={() => void move(position - 1)}>← 上一張</button>
      {position + 1 < cards.cards.length
        ? <button className="primary-button" disabled={busy} onClick={() => void move(position + 1)}>下一張 →</button>
        : <button className="primary-button" disabled={busy} onClick={() => writeRoute({ name: "card-sets", materialId, view: "list" })}>完成複習</button>}
    </nav>
    <div className="flashcard-secondary"><button className="text-button" disabled={busy} onClick={() => void move(0)}>重新開始</button>
      <button className="text-button" onClick={() => writeRoute({ name: "card-sets", materialId, view: "list" })}>返回我的圖卡組</button></div>
  </section>;
}

export function CardSetPage({ apiClient, route }: { apiClient: StudydyApiClient; route: CardSetRoute }) {
  if (route.view === "list") return <CardSetList apiClient={apiClient} materialId={route.materialId} />;
  if (route.view === "new") return <CardSetEditor apiClient={apiClient} materialId={route.materialId} />;
  if ("cardSetId" in route && route.view === "edit") return <CardSetEditor apiClient={apiClient} materialId={route.materialId} cardSetId={route.cardSetId} />;
  if ("cardSetId" in route) return <CardSetStudy apiClient={apiClient} materialId={route.materialId} cardSetId={route.cardSetId} />;
  return null;
}
