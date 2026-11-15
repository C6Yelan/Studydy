import { ListBreadcrumb } from "../../ui/Breadcrumb";
import { useEffect, useRef, useState } from "react";
import { ApiClientError, errorMessage, isNotFound, type StudydyApiClient } from "../../api/client";
import { NotFoundView } from "../../ui/NotFoundView";
import type { CardStudyView, KnowledgeOutlineView, KnowledgeRegionsView } from "../../api/contracts";
import { routePath, writeRoute, type AppRoute } from "../../app/routes";
import { StateView } from "../../ui/StateView";
import { useConceptContext } from "../material-flow/useConceptContext";
import { FlowFooter } from "../material-flow/Flow";
import { ChapterGroups, ClampedClaims, ConceptPickTools, PickHead, PreviewSheet, chapterGroups, usePhoneLayout, type SelectionFilter } from "../material-flow/ConceptPick";
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
  // 管理卡組在教材內（已有教材側欄與返回鍵）不再重複返回；從全站概念卡列表進入時保留返回該列表。
  const [fromCollection] = useState(() => !!window.history.state?.fromCollection);
  const routeKey = routePath(route);
  const [view, setView] = useState<KnowledgeOutlineView | null>(null);
  // 章節分區讀不到時是 null，概念清單退回分頁清單。
  const [regions, setRegions] = useState<KnowledgeRegionsView | null>(null);
  const [saved, setSaved] = useState<CardStudyView | null>(null);
  const [materialId, setMaterialId] = useState("");
  const [name, setName] = useState("");
  const [selected, setSelected] = useState(new Set<string>());
  const [previewId, setPreviewId] = useState<string | null>(null);
  // 手機上點概念名稱才打開預覽抽屜；桌機的預覽一直放在右欄。
  const phone = usePhoneLayout();
  const [sheetOpen, setSheetOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selectionFilter, setSelectionFilter] = useState<SelectionFilter>("all");
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
        const [material, structure, regionsRead] = await Promise.all([
          apiClient.getMaterial(id),
          apiClient.getKnowledgeOutline({ materialId: id, structureRevision: revision }),
          apiClient.getKnowledgeRegions({ materialId: id, structureRevision: revision }).catch(() => null),
        ]);
        if (cancelled) return;
        if (structure.knowledge_structure_revision !== revision ||
          (route.name === "card-set-create" && !material.available_structures.some(
            (item) => item.run_id === route.runId && item.knowledge_structure_revision === revision))) {
          throw new Error("這個教材版本已不可用，請重新開啟卡組或知識地圖。");
        }
        setSaved(deck);
        setView(structure);
        setRegions(regionsRead);
        setMaterialId(id);
        setName(deck?.name ?? `${Array.from(material.display_name.replace(/\.[^.]+$/, "")).slice(0, 38).join("")}複習`);
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
  const validName = name.trim().length > 0 && Array.from(name.trim()).length <= 40 && !/[\p{Cc}\p{Cs}]/u.test(name);
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
    back={route.materialId ? null : editing ? { label: "回到翻卡片", route: { name: "concept-cards" } } : { label: "回到我的教材", route: { name: "materials" } }} />;
  // 調整卡組自己一頁（不經 CreateStudyContent）；建立時頁頂由 CreateStudyContent 放。
  const header = !embedded && <PickHead crumb={<ListBreadcrumb kind="concept-cards" materialId={route.materialId} current="調整卡組" />}
    title={<>調整卡組{saved && <small>{saved.name}</small>}</>} sub="可以改名稱，或勾選要保留的概念；保存後卡組會照新的內容更新。" />;
  if (error || !view) {
    const state = <StateView title={error ? "無法讀取概念" : "正在準備概念卡"}
      description={error ?? "正在載入教材的概念與重點。"} tone={error ? "failure" : "loading"} live={!error} skeleton="page"
      action={error ? <button className="primary-button" type="button" onClick={() => setReload((n) => n + 1)}>重新讀取</button> : undefined} />;
    return embedded ? <section className="cards-page">{state}</section>
      : <section className="cards-page creation-page flow-page">{header}{state}</section>;
  }
  const visible = view.concepts.filter((item) => (selectionFilter === "all" || selected.has(item.concept_id) === (selectionFilter === "selected")) && `${item.label} ${item.aliases.join(" ")}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const pageCount = Math.max(1, Math.ceil(visible.length / pageSize));
  const currentPage = Math.min(conceptPage, pageCount);
  const pageConcepts = visible.slice((currentPage - 1) * pageSize, currentPage * pageSize);
  const preview = previewRead.context?.concepts.find((item) => item.concept_id === previewId);
  const toggle = (id: string) => {
    setSelected((previous) => { const next = new Set(previous); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  };
  const groups = chapterGroups(view.concepts, regions);
  const conceptRow = (concept: KnowledgeOutlineView["concepts"][number]) => <div className={`card-picker-option ${selected.has(concept.concept_id) ? "is-selected" : ""}`} key={concept.concept_id}>
    <input type="checkbox" aria-label={concept.label} checked={selected.has(concept.concept_id)} disabled={busy} onChange={() => toggle(concept.concept_id)} />
    <button type="button" aria-label={`預覽「${concept.label}」`} aria-pressed={previewId === concept.concept_id} onClick={() => { setPreviewId(concept.concept_id); setSheetOpen(true); }}><strong><MathText text={concept.label} /></strong><small>{concept.claim_count} 個重點</small></button>
  </div>;
  const previewSection = <section className="cards-preview card-picker-preview" aria-label="這個概念的重點">
    <h3>這個概念的重點（教材原文）</h3>
    {previewRead.error ? <p role="alert">{previewRead.error}<button type="button" onClick={previewRead.retry}>重新讀取預覽</button></p> : !preview && previewId ? <p role="status">正在讀取概念與來源…</p> : null}
    {preview && <><h4><MathText text={preview.label} /></h4><ClampedClaims resetKey={preview.concept_id}><ol>{preview.claims.map(claim => <li key={claim.claim_id}>
      <span className="pick-claim-text"><MathText text={claimText(claim)} code={claim.evidence.some(item => item.kind === "code")} /></span>
      <div className="flashcard-source-links">{sourceLinks(claim.evidence).map(evidence => <SourceButton key={evidence.evidence_id} apiClient={apiClient} resolver={view.source_resolver} evidence={evidence} />)}</div>
    </li>)}</ol></ClampedClaims></>}
  </section>;
  const stage = <div className="pick-stage">
    {saveError && <p className="form-error" role="alert">{saveError}{conflict && <button className="text-button" type="button" onClick={() => setReload((n) => n + 1)}>重新讀取卡組</button>}</p>}
    <div className="cards-create-grid">
      <section className="pick-panel cards-selection" aria-label="選擇概念">
        <ConceptPickTools searchLabel="搜尋概念" query={query} onQuery={(value) => { setQuery(value); setConceptPage(1); }} busy={busy}
          onSelectAll={() => setSelected(new Set(view.concepts.map((item) => item.concept_id)))} onSelectNone={() => setSelected(new Set())}
          filter={selectionFilter} onFilter={(value) => { setSelectionFilter(value); setConceptPage(1); }} total={view.concepts.length} selected={selected.size} />
        <div className={`cards-concept-list card-picker-grid${groups ? " is-grouped" : ""}`} ref={conceptList}>
          {groups ? <ChapterGroups groups={groups} visible={visible} selected={selected} busy={busy} itemsClassName="chapter-items"
            onToggleChapter={(ids, on) => setSelected((previous) => { const next = new Set(previous); for (const id of ids) { if (on) next.add(id); else next.delete(id); } return next; })}
            renderConcept={conceptRow} /> : pageConcepts.map(conceptRow)}
          {visible.length === 0 && <p className="cards-no-results">找不到符合的概念。</p>}
        </div>
        {!groups && pageCount > 1 && <nav className="cards-concept-pagination" aria-label="概念清單分頁">
          <button className="secondary-button" type="button" disabled={currentPage === 1} onClick={() => setConceptPage(currentPage - 1)}>上一頁</button>
          <span aria-live="polite">第 {currentPage} / {pageCount} 頁</span>
          <button className="secondary-button" type="button" disabled={currentPage === pageCount} onClick={() => setConceptPage(currentPage + 1)}>下一頁</button>
        </nav>}
      </section>
      <div className="pick-panel cards-side">
        <label className="creation-name">卡組名稱<input value={name} maxLength={40} disabled={busy} onChange={(event) => setName(event.target.value)} placeholder="例如：資料結構考前複習" /></label>
        {!phone && previewSection}
      </div>
    </div>
    <FlowFooter
      secondary={<div className="cards-create-summary"><strong>已選 {selected.size} 個概念</strong><span>每個概念會做成一張或多張卡，依重點內容安排。</span></div>}
      primary={<div>
        {editing && <button className="secondary-button" type="button" disabled={busy} onClick={back}>取消變更</button>}
        <button className="primary-button" type="button" disabled={busy || !validName || selected.size === 0 || conflict} onClick={() => void save()}>
          {busy ? "正在保存…" : editing ? "保存變更" : "建立並開始翻卡"}
        </button>
      </div>} />
    {phone && <PreviewSheet title="概念重點預覽" open={sheetOpen} onClose={() => setSheetOpen(false)}>{previewSection}</PreviewSheet>}
  </div>;
  return embedded ? stage : <section className="cards-page creation-page flow-page">{header}{stage}</section>;
}
