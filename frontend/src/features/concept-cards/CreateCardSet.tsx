import { BackLink } from "../../ui/BackLink";
import { useEffect, useRef, useState } from "react";
import { ApiClientError, errorMessage, isNotFound, type StudydyApiClient } from "../../api/client";
import { NotFoundView } from "../../ui/NotFoundView";
import type { CardStudyView, KnowledgeOutlineView } from "../../api/contracts";
import { routePath, writeRoute, type AppRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";
import { StateView } from "../../ui/StateView";
import { useConceptContext } from "../material-flow/useConceptContext";
import { FlowFooter, FlowHeader, FlowStage } from "../material-flow/Flow";
import "../material-flow/creation.css";
import { MathText } from "../../ui/MathText";
import { claimText } from "../../ui/claim-text";
import { SourceButton, sourceLinks } from "../../ui/SourceButton";
import "./card-practice.css";

export function CreateCardSet({ apiClient, route, embedded = false }: {
  embedded?: boolean;
  apiClient: StudydyApiClient;
  route: Extract<AppRoute, { name: "card-set-create" | "card-set-edit" }>;
}) {
  const editing = route.name === "card-set-edit";
  // 管理卡組在教材內（已有教材頁首與分頁列）不再重複返回；從全站概念卡列表進入時保留返回該列表。
  const [fromCollection] = useState(() => !!window.history.state?.fromCollection);
  const routeKey = routePath(route);
  const [view, setView] = useState<KnowledgeOutlineView | null>(null);
  const [saved, setSaved] = useState<CardStudyView | null>(null);
  const [materialId, setMaterialId] = useState("");
  const [materialName, setMaterialName] = useState("");
  const [name, setName] = useState("");
  const [selected, setSelected] = useState(new Set<string>());
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [selectionFilter, setSelectionFilter] = useState<"all" | "selected" | "unselected">("all");
  const [conceptPage, setConceptPage] = useState(1);
  const [pageSize, setPageSize] = useState(8);
  const conceptList = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
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
        const deck = route.name === "card-set-edit" ? await apiClient.getCardStudy(route.cardSetId) : null;
        if (deck && route.name === "card-set-edit" && !route.materialId) { if (!cancelled) writeRoute({ name: "card-set-edit", cardSetId: route.cardSetId, materialId: deck.material_id }, true, window.history.state); return; }
        if (deck && route.materialId && deck.material_id !== route.materialId) { if (!cancelled) setNotFound(true); return; }
        const id = deck?.material_id ?? (route.name === "card-set-create" ? route.materialId : "");
        const revision = deck?.knowledge_structure_revision ?? (route.name === "card-set-create" ? route.structureRevision : "");
        const [material, structure] = await Promise.all([
          apiClient.getMaterial(id),
          apiClient.getKnowledgeOutline({ materialId: id, structureRevision: revision }),
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
        setSelected(new Set((deck?.navigation ?? structure.concepts).map((item) => item.concept_id)));
        setPreviewId(deck?.navigation[0]?.concept_id ?? structure.concepts[0]?.concept_id ?? null);
      } catch (failure) {
        if (!cancelled) { if (isNotFound(failure)) setNotFound(true); else setError(errorMessage(failure)); }
      }
    };
    void read();
    return () => { cancelled = true; };
  }, [apiClient, routeKey, reload]);
  useEffect(() => {
    const list = conceptList.current;
    if (!list) return;
    // 與 creation.css 的桌面填滿版面同一門檻（1000px，依內容寬度保留的特殊值）。
    const desktop = window.matchMedia("(min-width: 1000px)");
    let previousSize: number | null = null;
    const resize = () => {
      // 依自動欄數與實際格高分頁，長標題保留完整文字。
      const columns = getComputedStyle(list).gridTemplateColumns.split(" ").length;
      const rowHeight = list.firstElementChild?.getBoundingClientRect().height ?? 60;
      const size = desktop.matches ? Math.max(3, Math.floor((list.clientHeight + 8) / (rowHeight + 8))) * columns : 8;
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
  }, [view]);
  const previewRead = useConceptContext(apiClient, materialId, view?.knowledge_structure_revision ?? "", view && previewId ? [previewId] : [], true);
  const back = () => writeRoute(route.name === "card-set-edit"
    ? fromCollection ? { name: "concept-cards" } : { name: "card-set", cardSetId: route.cardSetId, ...(route.materialId ? { materialId: route.materialId } : {}) }
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
      if (current()) writeRoute({ name: "card-set", cardSetId: result.card_set_id, ...(route.name === "card-set-create" || route.materialId ? { materialId: route.materialId } : {}) }, false, { fromCollection });
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
  if (notFound) return <NotFoundView title={editing ? "找不到這個卡組" : "找不到這份教材"} description={editing ? "這個卡組可能已被刪除，或不屬於目前登入的帳號。" : "這份教材或地圖版本可能已被刪除，或不屬於目前登入的帳號。"}
    back={route.materialId ? null : editing ? { label: "回到概念卡", route: { name: "concept-cards" } } : { label: "回到我的教材", route: { name: "materials" } }} />;
  // 管理卡組自己一頁（不經 CreateStudyContent），頁首與建立頁同一套 flow-head。
  const header = !embedded && <FlowHeader back={!route.materialId && <BackLink label="返回我的概念卡" route={{ name: "concept-cards" }} />} title="管理卡組"
    description={materialName ? <span className="cards-material-name"><Icon name="book" size={16} /> {materialName}</span> : undefined} />;
  if (error || !view) {
    const state = <StateView title={error ? "無法讀取概念" : "正在準備概念卡"}
      description={error ?? "正在載入教材的概念與重點。"} tone={error ? "failure" : "loading"} live={!error} skeleton="page"
      action={error ? <button className="primary-button" type="button" onClick={() => setReload((n) => n + 1)}>重新讀取</button> : undefined} />;
    return embedded ? <section className="cards-page">{state}</section>
      : <section className="cards-page creation-page flow-page cards-creation-page">{header}{state}</section>;
  }
  const visible = view.concepts.filter((item) => (selectionFilter === "all" || selected.has(item.concept_id) === (selectionFilter === "selected")) && `${item.label} ${item.aliases.join(" ")}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const pageCount = Math.max(1, Math.ceil(visible.length / pageSize));
  const currentPage = Math.min(conceptPage, pageCount);
  const pageConcepts = visible.slice((currentPage - 1) * pageSize, currentPage * pageSize);
  const preview = previewRead.context?.concepts.find((item) => item.concept_id === previewId);
  const toggle = (id: string) => {
    setSelected((previous) => { const next = new Set(previous); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  };
  const stage = <FlowStage icon="cards" className="cards-create"
    title={editing ? "調整卡組內容" : "選擇概念"}
    description={editing ? "修改名稱或勾選要保留的概念，保存後卡組會依新內容更新。" : "勾選想做成卡片的概念，點概念名稱查看重點。"}
    aside={<label className="creation-name">卡組名稱<input value={name} maxLength={200} disabled={busy} onChange={(event) => setName(event.target.value)} placeholder="例如：資料結構考前複習" /></label>}
    footer={<FlowFooter
      secondary={<>
        <div className="cards-create-summary"><strong>{selected.size} 個觀念</strong><span>保存後，每個概念會生成一張或多張複習卡，依重點內容安排。</span></div>
        {editing && <button className="secondary-button" type="button" disabled={busy} onClick={back}>取消變更</button>}
      </>}
      primary={<button className="primary-button" type="button" disabled={busy || !validName || selected.size === 0 || conflict} onClick={() => void save()}>
        {busy ? "正在保存…" : editing ? "保存變更" : "保存並開始複習"}<Icon name="chevron-right" size={18} />
      </button>} />}>
    {saveError && <p className="form-error" role="alert">{saveError}{conflict && <button className="text-button" type="button" onClick={() => setReload((n) => n + 1)}>重新讀取卡組</button>}</p>}
    <div className="cards-create-grid">
      <section className="cards-selection" aria-label="選擇概念">
        <div className="cards-selection-heading">
          <input className="cards-concept-search" type="search" aria-label="搜尋概念" placeholder="搜尋概念…" value={query} onChange={(event) => { setQuery(event.target.value); setConceptPage(1); }} />
          <div className="cards-selection-actions"><button className="text-button" type="button" disabled={busy} onClick={() => setSelected(new Set(view.concepts.map((item) => item.concept_id)))}>全選</button><span>／</span><button className="text-button" type="button" disabled={busy} onClick={() => setSelected(new Set())}>取消全選</button></div>
          <div className="concept-selection-filters">
            {([["all", "全部", view.concepts.length], ["selected", "已選", selected.size], ["unselected", "未選", view.concepts.length - selected.size]] as const).map(([filter, label, count]) => <button key={filter} type="button" className="secondary-button" aria-pressed={selectionFilter === filter} onClick={() => { setSelectionFilter(filter); setConceptPage(1); }}>{label} {count}</button>)}
          </div>
        </div>
        <div className="cards-concept-list card-picker-grid" ref={conceptList}>
          {pageConcepts.map((concept) => <div className={`card-picker-option ${selected.has(concept.concept_id) ? "is-selected" : ""}`} key={concept.concept_id}>
            <input type="checkbox" aria-label={concept.label} checked={selected.has(concept.concept_id)} disabled={busy} onChange={() => toggle(concept.concept_id)} />
            <button type="button" aria-label={`預覽「${concept.label}」`} aria-pressed={previewId === concept.concept_id} onClick={() => setPreviewId(concept.concept_id)}><strong>{concept.label}</strong><small>{concept.claim_count} 個重點</small></button>
          </div>)}
          {visible.length === 0 && <p className="cards-no-results">找不到符合的概念。</p>}
        </div>
        {pageCount > 1 && <nav className="cards-concept-pagination" aria-label="概念清單分頁">
          <button className="secondary-button" type="button" disabled={currentPage === 1} onClick={() => setConceptPage(currentPage - 1)}>上一頁</button>
          <span aria-live="polite">第 {currentPage} / {pageCount} 頁</span>
          <button className="secondary-button" type="button" disabled={currentPage === pageCount} onClick={() => setConceptPage(currentPage + 1)}>下一頁</button>
        </nav>}
      </section>
      <section className="cards-preview card-picker-preview" aria-label="這個概念的重點">
        <h3>這個概念的重點</h3>
        {previewRead.error ? <p role="alert">{previewRead.error}<button type="button" onClick={previewRead.retry}>重新讀取預覽</button></p> : !preview && previewId ? <p role="status">正在讀取概念與來源…</p> : null}
        {preview && <><h4>{preview.label}</h4><ol>{preview.claims.map(claim => <li key={claim.claim_id}>
          <MathText text={claimText(claim)} code={claim.evidence.some(item => item.kind === "code")} />
          <div className="flashcard-source-links">{sourceLinks(claim.evidence).map(evidence => <SourceButton key={evidence.evidence_id} apiClient={apiClient} resolver={view.source_resolver} evidence={evidence} />)}</div>
        </li>)}</ol></>}
      </section>
    </div>
  </FlowStage>;
  return embedded ? stage : <section className="cards-page creation-page flow-page cards-creation-page">{header}{stage}</section>;
}
