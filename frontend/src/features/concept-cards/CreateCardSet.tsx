import { useEffect, useRef, useState } from "react";
import { ApiClientError, errorMessage, type StudydyApiClient } from "../../api/client";
import type { CardSetView, KnowledgeStructureView } from "../../api/contracts";
import { routePath, writeRoute, type AppRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";
import { StateView } from "../../ui/StateView";
import { Flashcard } from "./Flashcard";

export function CreateCardSet({ apiClient, route, embedded = false }: {
  embedded?: boolean;
  apiClient: StudydyApiClient;
  route: Extract<AppRoute, { name: "card-set-create" | "card-set-edit" }>;
}) {
  const editing = route.name === "card-set-edit";
  const routeKey = routePath(route);
  const [view, setView] = useState<KnowledgeStructureView | null>(null);
  const [saved, setSaved] = useState<CardSetView | null>(null);
  const [materialId, setMaterialId] = useState("");
  const [materialName, setMaterialName] = useState("");
  const [name, setName] = useState("");
  const [selected, setSelected] = useState(new Set<string>());
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [conceptPage, setConceptPage] = useState(1);
  const [pageSize, setPageSize] = useState(8);
  const conceptList = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const active = useRef(false);
  const saving = useRef(false);
  const intent = useRef<{ fingerprint: string; key: string } | null>(null);
  useEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, []);
  useEffect(() => {
    let cancelled = false;
    setError(null);
    setSaveError(null);
    setConflict(false);
    setView(null);
    setConceptPage(1);
    const read = async () => {
      try {
        const deck = route.name === "card-set-edit" ? await apiClient.getCardSet(route.cardSetId) : null;
        if (deck && route.name === "card-set-edit" && !route.materialId) { if (!cancelled) writeRoute({ name: "card-set-edit", cardSetId: route.cardSetId, materialId: deck.material_id }, true); return; }
        if (deck && route.materialId && deck.material_id !== route.materialId) throw new Error("這個卡組不屬於此教材。");
        const id = deck?.material_id ?? (route.name === "card-set-create" ? route.materialId : "");
        const revision = deck?.knowledge_structure_revision ?? (route.name === "card-set-create" ? route.structureRevision : "");
        const [material, structure] = await Promise.all([
          apiClient.getMaterial(id),
          apiClient.getKnowledgeStructure({ materialId: id, structureRevision: revision }),
        ]);
        if (cancelled) return;
        if (structure.knowledge_structure_revision !== revision ||
          (route.name === "card-set-create" && !material.available_structures.some(
            (item) => item.run_id === route.runId && item.knowledge_structure_revision === revision))) {
          throw new Error("這個教材版本已不可用，請重新開啟卡組或知識地圖。");
        }
        setSaved(deck);
        setView(structure);
        setMaterialId(id);
        setMaterialName(material.display_name);
        setName(deck?.name ?? `${material.display_name.replace(/\.[^.]+$/, "").slice(0, 190)}複習`);
        setSelected(new Set((deck?.cards ?? structure.concepts).map((item) => item.concept_id)));
        setPreviewId(deck?.cards[0]?.concept_id ?? structure.concepts[0]?.concept_id ?? null);
      } catch (failure) {
        if (!cancelled) setError(errorMessage(failure));
      }
    };
    void read();
    return () => { cancelled = true; };
  }, [apiClient, routeKey, reload]);
  useEffect(() => {
    const list = conceptList.current;
    if (!list) return;
    const desktop = window.matchMedia("(min-width: 1000px)");
    let previousSize: number | null = null;
    const resize = () => {
      // 桌面每列三個概念，依 60px 列高與 8px 間距使用清單的實際剩餘空間。
      const size = embedded && desktop.matches ? Math.max(3, Math.floor((list.clientHeight + 8) / 68)) * 3 : 8;
      if (size === previousSize) return;
      previousSize = size;
      setPageSize(size);
      setConceptPage(1);
    };
    const observer = new ResizeObserver(resize);
    observer.observe(list);
    desktop.addEventListener("change", resize);
    resize();
    return () => { observer.disconnect(); desktop.removeEventListener("change", resize); };
  }, [view, embedded]);
  const back = () => writeRoute(route.name === "card-set-edit"
    ? { name: "card-set", cardSetId: route.cardSetId, ...(route.materialId ? { materialId: route.materialId } : {}) }
    : { ...route, name: "knowledge-map" });
  const validName = name.trim().length > 0 && Array.from(name.trim()).length <= 200 && !/[\p{Cc}\p{Cs}]/u.test(name);
  const save = async () => {
    if (!view || !validName || !selected.size || saving.current || conflict) return;
    const conceptIds = [...selected];
    const body = {
      schema: "card-set-create/v1" as const,
      knowledge_structure_revision: view.knowledge_structure_revision,
      name: name.trim(), concept_ids: conceptIds,
    };
    const fingerprint = JSON.stringify(body);
    if (intent.current?.fingerprint !== fingerprint) intent.current = { fingerprint, key: crypto.randomUUID() };
    saving.current = true;
    setBusy(true);
    setSaveError(null);
    const current = () => active.current && window.location.pathname === routeKey;
    try {
      const result = saved
        ? await apiClient.updateCardSet(saved.card_set_id, {
            schema: "card-set-update/v1", name: name.trim(),
            concept_ids: conceptIds, expected_version: saved.version,
          })
        : await apiClient.createCardSet(materialId, body, intent.current.key);
      if (current()) writeRoute({ name: "card-set", cardSetId: result.card_set_id, ...(route.name === "card-set-create" || route.materialId ? { materialId: route.materialId } : {}) });
    } catch (failure) {
      if (current()) {
        setSaveError(errorMessage(failure));
        setConflict(failure instanceof ApiClientError && failure.reasonCode === "CARD_SET_CONFLICT");
      }
    } finally {
      saving.current = false;
      if (current()) setBusy(false);
    }
  };
  const backLabel = editing ? "返回卡組" : "返回知識地圖";
  if (error || !view) return (
    <section className="cards-page">
      {!embedded && <button className="text-button" type="button" onClick={back}>← {backLabel}</button>}
      <StateView title={error ? "無法讀取概念" : "正在準備概念卡"}
        description={error ?? "正在載入教材的概念與重點。"} tone={error ? "failure" : "loading"} live={!error}
        action={error ? <button className="primary-button" type="button" onClick={() => setReload((n) => n + 1)}>重新讀取</button> : undefined} />
    </section>
  );
  const visible = view.concepts.filter((item) => `${item.label} ${item.aliases.join(" ")}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const pageCount = Math.max(1, Math.ceil(visible.length / pageSize));
  const pageConcepts = visible.slice((conceptPage - 1) * pageSize, conceptPage * pageSize);
  const preview = view.concepts.find((item) => item.concept_id === previewId);
  const toggle = (id: string) => {
    setSelected((previous) => { const next = new Set(previous); if (next.has(id)) next.delete(id); else next.add(id); return next; });
    setPreviewId(id);
  };
  return <section className="cards-page cards-create">
    {!embedded && <button className="cards-back text-button" type="button" onClick={back}><Icon name="arrow-left" size={17} /> {backLabel}</button>}
    {!embedded && <header className="cards-page-header"><div><h1>{editing ? "管理卡組" : "建立概念卡組"}</h1><p className="cards-material-name"><Icon name="book" size={16} /> {materialName}</p></div></header>}
    <header className="cards-create-toolbar">
      <label className="cards-field">卡組名稱<input value={name} maxLength={200} disabled={busy} onChange={(event) => setName(event.target.value)} placeholder="例如：資料結構考前複習" /></label>
      <div className="cards-create-summary"><strong>{selected.size} 張概念卡</strong><span>{editing ? "調整名稱或勾選要保留的概念" : "保存後可隨時回來複習"}</span></div>
      <div className="state-actions">
        {editing && <button className="secondary-button" type="button" disabled={busy} onClick={back}>取消變更</button>}
        <button className="primary-button" type="button" disabled={busy || !validName || selected.size === 0 || conflict} onClick={() => void save()}>
          {busy ? "正在保存…" : editing ? "保存變更" : "保存並開始複習"}<Icon name="chevron-right" size={18} />
        </button>
      </div>
      {saveError && <p className="form-error" role="alert">{saveError}{conflict && <button className="text-button" type="button" onClick={() => setReload((n) => n + 1)}>重新讀取卡組</button>}</p>}
    </header>
    <div className="cards-create-grid">
      <section className="cards-selection" aria-label="選擇概念">
        <div className="cards-selection-heading"><h2>選擇概念</h2><span aria-live="polite">已選 {selected.size} / {view.concepts.length}</span></div>
        <div className="cards-selection-toolbar">
          <input className="cards-concept-search" type="search" aria-label="搜尋概念" placeholder="搜尋概念…" value={query} onChange={(event) => { setQuery(event.target.value); setConceptPage(1); }} />
          <div className="cards-selection-actions"><button className="text-button" type="button" disabled={busy} onClick={() => setSelected(new Set(view.concepts.map((item) => item.concept_id)))}>全選</button><span>／</span><button className="text-button" type="button" disabled={busy} onClick={() => setSelected(new Set())}>取消全選</button></div>
        </div>
        <div className="cards-concept-list" ref={conceptList}>
          {pageConcepts.map((concept) => <div className={`cards-concept-row ${selected.has(concept.concept_id) ? "is-selected" : ""}`} key={concept.concept_id}>
            <label><input type="checkbox" checked={selected.has(concept.concept_id)} disabled={busy} onChange={() => toggle(concept.concept_id)} /><span><strong>{concept.label}</strong><small>{concept.claims.length} 個重點</small></span></label>
            <button className="text-button" type="button" aria-label={`預覽「${concept.label}」`} aria-pressed={previewId === concept.concept_id} onClick={() => { setPreviewId(concept.concept_id); }}>預覽</button>
          </div>)}
          {visible.length === 0 && <p className="cards-no-results">找不到符合的概念。</p>}
        </div>
        {pageCount > 1 && <nav className="cards-concept-pagination" aria-label="概念清單分頁">
          <button className="secondary-button" type="button" disabled={conceptPage === 1} onClick={() => setConceptPage((page) => page - 1)}>上一頁</button>
          <span aria-live="polite">第 {conceptPage} / {pageCount} 頁</span>
          <button className="secondary-button" type="button" disabled={conceptPage === pageCount} onClick={() => setConceptPage((page) => page + 1)}>下一頁</button>
        </nav>}
      </section>
      <section className="cards-preview" aria-label="卡片預覽"><div className="cards-preview-heading"><h2>卡片預覽</h2><span>一張卡，一個概念</span></div>{preview && <Flashcard key={preview.concept_id} card={preview} structure={view} materialName={materialName} apiClient={apiClient} sourceResolver={view.source_resolver} />}</section>
    </div>
  </section>;
}
