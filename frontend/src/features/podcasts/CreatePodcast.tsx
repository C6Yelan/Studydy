import { useEffect, useRef, useState } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { KnowledgeOutlineView, KnowledgeRegionsView } from "../../api/contracts";
import { routePath, writeRoute, type AppRoute } from "../../app/routes";
import { StateView } from "../../ui/StateView";
import { useConceptContext } from "../material-flow/useConceptContext";
import { claimText } from "../../ui/claim-text";
import { FlowFooter } from "../material-flow/Flow";
import { ChapterGroups, ConceptPickTools, chapterGroups, type SelectionFilter } from "../material-flow/ConceptPick";
import "../material-flow/creation.css";

export function CreatePodcast({ apiClient, route }: {
  apiClient: StudydyApiClient; route: Extract<AppRoute, { name: "podcast-create" }>;
}) {
  const [view, setView] = useState<KnowledgeOutlineView | null>(null);
  // 章節分區讀不到時是 null，概念清單退回原本的清單。
  const [regions, setRegions] = useState<KnowledgeRegionsView | null>(null);
  const [name, setName] = useState("");
  const [delivery, setDelivery] = useState<"solo" | "dialogue">("dialogue");
  // 影片較佔空間，預設只生成音訊。
  const [video, setVideo] = useState(false);
  const [selected, setSelected] = useState(new Set<string>());
  const [activePanel, setActivePanel] = useState<"selection" | "settings">("selection");
  const [selectionFilter, setSelectionFilter] = useState<SelectionFilter>("all");
  const [query, setQuery] = useState("");
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [busy, setBusy] = useState(false);
  const active = useRef(false), saving = useRef(false);
  const intent = useRef<{ fingerprint: string; key: string } | null>(null);
  const path = routePath(route);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    let cancelled = false;
    setError(null); setView(null);
    void Promise.all([apiClient.getMaterial(route.materialId), apiClient.getKnowledgeOutline({
      materialId: route.materialId, structureRevision: route.structureRevision,
    }), apiClient.getKnowledgeRegions({ materialId: route.materialId, structureRevision: route.structureRevision }).catch(() => null)]).then(([material, structure, regionsRead]) => {
      if (cancelled) return;
      if (!material.available_structures.some((s) => s.run_id === route.runId && s.knowledge_structure_revision === route.structureRevision)) {
        setError("這個教材版本已不可用，請重新選擇教材。"); return;
      }
      setView(structure);
      setRegions(regionsRead);
      setName(`${Array.from(material.display_name.replace(/\.[^.]+$/, "")).slice(0, 36).join("")}・聽重點`);
      setSelected(new Set(structure.concepts.map((c) => c.concept_id)));
      setPreviewId(null);
    }, (e) => { if (!cancelled) setError(errorMessage(e)); });
    return () => { cancelled = true; };
  }, [apiClient, route.materialId, route.runId, route.structureRevision, reload]);
  const previewRead = useConceptContext(apiClient, route.materialId, route.structureRevision, previewId ? [previewId] : []);
  const validName = name.trim().length > 0 && Array.from(name.trim()).length <= 40 && !/[\p{Cc}\p{Cs}]/u.test(name);
  const save = async () => {
    if (!view || !selected.size || !validName || saving.current) return;
    saving.current = true; setBusy(true); setSaveError(null);
    const body = { schema: "podcast-create/v1" as const, name: name.trim(), delivery, video,
      knowledge_structure_revision: view.knowledge_structure_revision, concept_ids: [...selected] };
    const fingerprint = JSON.stringify(body);
    if (intent.current?.fingerprint !== fingerprint) intent.current = { fingerprint, key: crypto.randomUUID() };
    try {
      const result = await apiClient.createPodcast(route.materialId, body, intent.current.key);
      if (active.current && window.location.pathname === path) writeRoute({ name: "podcast", podcastId: result.podcast_id, materialId: route.materialId }, false, { fromCollection: !!window.history.state?.fromCollection });
    } catch (e) { if (active.current) setSaveError(errorMessage(e)); }
    finally { saving.current = false; if (active.current) setBusy(false); }
  };
  if (!view || error) return <section className="cards-page"><StateView title={error ? "無法讀取教材" : "正在準備 Podcast"}
    description={error ?? "正在讀取概念與來源。"} tone={error ? "failure" : "loading"} live={!error} skeleton="page"
    action={error ? <button type="button" onClick={() => setReload((v) => v + 1)}>重新讀取</button> : undefined} /></section>;
  const matches = view.concepts.filter(c => `${c.label} ${c.aliases.join(" ")}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const visible = matches.filter(c => selectionFilter === "all" || selected.has(c.concept_id) === (selectionFilter === "selected"));
  const preview = previewRead.context?.concepts.find(c => c.concept_id === previewId);
  const claimCount = view.concepts.filter(c => selected.has(c.concept_id)).reduce((sum, c) => sum + c.claim_count, 0);
  const selectVisible = (choose: boolean) => setSelected(previous => {
    const next = new Set(previous);
    for (const concept of matches) { if (choose) next.add(concept.concept_id); else next.delete(concept.concept_id); }
    return next;
  });
  const toggle = (id: string) => setSelected(previous => { const next = new Set(previous); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  const groups = chapterGroups(view.concepts, regions);
  const conceptRow = (concept: KnowledgeOutlineView["concepts"][number]) => <div className={`podcast-concept-option${selected.has(concept.concept_id) ? " is-selected" : ""}`} key={concept.concept_id}>
    <label><input type="checkbox" disabled={busy} checked={selected.has(concept.concept_id)} onChange={() => toggle(concept.concept_id)} />
      <span><strong title={concept.label}>{concept.label}</strong><small>{concept.claim_count} 個重點</small></span></label>
    <button type="button" className="text-button" aria-label={`查看「${concept.label}」重點`} aria-pressed={previewId === concept.concept_id} onClick={() => setPreviewId(previewId === concept.concept_id ? null : concept.concept_id)}>預覽</button>
  </div>;
  return <div className="pick-stage podcast-create">
    {saveError && <p className="form-error" role="alert">{saveError}</p>}
    <nav className="podcast-config-nav" aria-label="Podcast 設定區域">{([
      ["selection", "選擇概念"], ["settings", "講解方式"],
    ] as const).map(([id, label]) => <button type="button" className={activePanel === id ? "primary-button" : "secondary-button"} aria-pressed={activePanel === id} aria-controls={`podcast-config-${id}`} key={id} onClick={() => setActivePanel(id)}>{label}</button>)}</nav>
    <div className="podcast-compose-grid">
      <section id="podcast-config-selection" className={`pick-panel podcast-config-section podcast-selection${activePanel === "selection" ? " is-active" : ""}`} aria-label="選擇 Podcast 概念">
        <ConceptPickTools searchLabel="搜尋 Podcast 概念" query={query} onQuery={setQuery} busy={busy}
          selectAllLabel={query.trim() ? "選取搜尋結果" : "全選"} selectNoneLabel={query.trim() ? "取消搜尋結果" : "取消全選"}
          onSelectAll={() => selectVisible(true)} onSelectNone={() => selectVisible(false)}
          filter={selectionFilter} onFilter={setSelectionFilter} total={view.concepts.length} selected={selected.size} />
        <div className={`podcast-concept-list${groups ? " is-grouped" : ""}`}>{groups
          ? <ChapterGroups groups={groups} visible={visible} selected={selected} busy={busy} itemsClassName="chapter-items is-wide"
            onToggleChapter={(ids, on) => setSelected(previous => { const next = new Set(previous); for (const id of ids) { if (on) next.add(id); else next.delete(id); } return next; })}
            renderConcept={conceptRow} />
          : visible.map(conceptRow)}{visible.length === 0 && <p className="cards-no-results">{"找不到符合的概念。"}</p>}</div>
        {previewRead.error ? <p role="alert">{previewRead.error}<button type="button" onClick={previewRead.retry}>重新讀取預覽</button></p> : previewId && !preview ? <p className="cards-preview-status" role="status">正在讀取概念重點…</p> : null}
        {preview && <section className="podcast-concept-preview" aria-label={`${preview.label}重點預覽`}><header><strong>{preview.label}</strong><button type="button" className="text-button" onClick={() => setPreviewId(null)}>關閉預覽</button></header><div>{preview.claims.map(claim => <p className="podcast-claim-text" key={claim.claim_id}>{claimText(claim)}</p>)}</div></section>}
      </section>
      <div className="pick-panel podcast-side">
        <label className="creation-name">Podcast 名稱<input value={name} maxLength={40} disabled={busy} onChange={e => setName(e.target.value)} /></label>
        <section id="podcast-config-settings" className={`podcast-config-section podcast-settings${activePanel === "settings" ? " is-active" : ""}`} aria-label="講解方式">
          <h3>講解方式</h3>
          <fieldset className="podcast-delivery"><legend>講解形式</legend>
            <label className={delivery === "dialogue" ? "is-selected" : undefined}><input type="radio" name="delivery" checked={delivery === "dialogue"} disabled={busy} onChange={() => setDelivery("dialogue")} /><span><strong>雙人對談</strong><small>兩位主持人用節目形式帶你走過重點。</small></span></label>
            <label className={delivery === "solo" ? "is-selected" : undefined}><input type="radio" name="delivery" checked={delivery === "solo"} disabled={busy} onChange={() => setDelivery("solo")} /><span><strong>單人解說</strong><small>一個人用口語說明重點。</small></span></label>
          </fieldset>
          <label className={`podcast-video-option${video ? " is-selected" : ""}`}><input type="checkbox" checked={video} disabled={busy} onChange={e => setVideo(e.target.checked)} />
            <span><strong>同時做教學影片</strong><small>比較佔儲存空間；不勾就只有聲音，逐字稿照常有。</small></span></label>
        </section>
      </div>
    </div>
    <FlowFooter
      secondary={<div className="cards-create-summary"><strong>{selected.size} 個概念 · {claimCount} 個重點</strong><span>{delivery === "dialogue" ? "雙人對談" : "單人解說"} · {video ? "聲音＋影片" : "只有聲音"} · 製作要幾分鐘，可以先離開這頁。</span></div>}
      primary={<button type="button" className="primary-button" disabled={busy || !selected.size || !validName} onClick={() => void save()}>{busy ? "正在建立…" : "開始製作 Podcast"}</button>} />
  </div>;
}
