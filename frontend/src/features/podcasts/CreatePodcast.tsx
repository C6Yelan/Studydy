import { useEffect, useRef, useState, type ReactNode } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { KnowledgeStructureView } from "../../api/contracts";
import { routePath, writeRoute, type AppRoute } from "../../app/routes";
import { CardRecommendations } from "../concept-cards/CardRecommendations";
import { StateView } from "../../ui/StateView";
import { Icon } from "../../ui/Icon";
import { claimText } from "../../ui/claim-text";

export function CreatePodcast({ apiClient, route, embedded = false, materialField }: {
  embedded?: boolean; materialField?: ReactNode;
  apiClient: StudydyApiClient; route: Extract<AppRoute, { name: "podcast-create" }>;
}) {
  const [view, setView] = useState<KnowledgeStructureView | null>(null);
  const [materialName, setMaterialName] = useState("");
  const [name, setName] = useState("");
  const [delivery, setDelivery] = useState<"solo" | "dialogue">("dialogue");
  const [mode, setMode] = useState<"quick" | "full">("quick");
  const [selected, setSelected] = useState(new Set<string>());
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [activePanel, setActivePanel] = useState<"recommend" | "selection" | "settings">("selection");
  const [onlySelected, setOnlySelected] = useState(false);
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
    void Promise.all([apiClient.getMaterial(route.materialId), apiClient.getKnowledgeStructure({
      materialId: route.materialId, structureRevision: route.structureRevision,
    })]).then(([material, structure]) => {
      if (cancelled) return;
      if (!material.available_structures.some((s) => s.run_id === route.runId && s.knowledge_structure_revision === route.structureRevision)) {
        setError("這個教材版本已不可用，請重新選擇教材。"); return;
      }
      setView(structure); setMaterialName(material.display_name);
      setName(`${material.display_name.replace(/\.[^.]+$/, "").slice(0, 180)}・聽重點`);
      setSelected(new Set(structure.concepts.map((c) => c.concept_id)));
      setPreviewId(null);
      setSessionId(material.study_sessions.find((s) => s.knowledge_structure_revision === route.structureRevision)?.study_session_id ?? null);
    }, (e) => { if (!cancelled) setError(errorMessage(e)); });
    return () => { cancelled = true; };
  }, [apiClient, route.materialId, route.runId, route.structureRevision, reload]);
  const validName = name.trim().length > 0 && Array.from(name.trim()).length <= 200 && !/[\p{Cc}\p{Cs}]/u.test(name);
  const save = async () => {
    if (!view || !selected.size || !validName || saving.current) return;
    saving.current = true; setBusy(true); setSaveError(null);
    const body = { schema: "podcast-create/v1" as const, name: name.trim(), mode, delivery,
      knowledge_structure_revision: view.knowledge_structure_revision, concept_ids: [...selected] };
    const fingerprint = JSON.stringify(body);
    if (intent.current?.fingerprint !== fingerprint) intent.current = { fingerprint, key: crypto.randomUUID() };
    try {
      const result = await apiClient.createPodcast(route.materialId, body, intent.current.key);
      if (active.current && window.location.pathname === path) writeRoute({ name: "podcast", podcastId: result.podcast_id, materialId: route.materialId });
    } catch (e) { if (active.current) setSaveError(errorMessage(e)); }
    finally { saving.current = false; if (active.current) setBusy(false); }
  };
  if (!view || error) return <section className="cards-page"><StateView title={error ? "無法讀取教材" : "正在準備 Podcast"}
    description={error ?? "正在讀取概念與來源。"} tone={error ? "failure" : "loading"} live={!error}
    action={error ? <button type="button" onClick={() => setReload((v) => v + 1)}>重新讀取</button> : undefined} /></section>;
  const matches = view.concepts.filter(c => `${c.label} ${c.aliases.join(" ")}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const visible = matches.filter(c => !onlySelected || selected.has(c.concept_id));
  const preview = view.concepts.find(c => c.concept_id === previewId);
  const claimCount = view.concepts.filter(c => selected.has(c.concept_id)).reduce((sum, c) => sum + c.claims.length, 0);
  const selectVisible = (choose: boolean) => setSelected(previous => {
    const next = new Set(previous);
    for (const concept of matches) { if (choose) next.add(concept.concept_id); else next.delete(concept.concept_id); }
    return next;
  });
  return <section className="cards-page podcast-create">
    {!embedded && <button type="button" className="text-button" onClick={() => writeRoute({ name: "material-content", materialId: route.materialId, kind: "podcasts" })}><Icon name="arrow-left" size={17} /> 此教材的 Podcast</button>}
    {!embedded && <header className="cards-page-header"><div><h1>建立 Podcast</h1><p className="cards-material-name"><Icon name="book" size={16} /> {materialName}</p></div></header>}
    <div className="podcast-identity-fields">{materialField}<label className="cards-field">Podcast 名稱<input value={name} maxLength={200} disabled={busy} onChange={e => setName(e.target.value)} /></label></div>
    <nav className="podcast-config-nav" aria-label="Podcast 設定區域">{([
      ["recommend", "幫我選概念"], ["selection", "選擇概念"], ["settings", "講解設定"],
    ] as const).map(([id, label]) => <button type="button" className={activePanel === id ? "primary-button" : "secondary-button"} aria-pressed={activePanel === id} aria-controls={`podcast-config-${id}`} key={id} onClick={() => setActivePanel(id)}>{label}</button>)}</nav>
    <div className="podcast-compose-grid">
      <section id="podcast-config-recommend" className={`surface podcast-config-section podcast-recommendations${activePanel === "recommend" ? " is-active" : ""}`} aria-label="幫我選概念">
        <h2>幫我選概念</h2>
        <CardRecommendations podcast apiClient={apiClient} view={view} studySessionId={sessionId} disabled={busy} onApply={items => {
          setSelected(new Set(items.map(i => i.conceptId))); setQuery(""); setOnlySelected(true); setPreviewId(null); setActivePanel("selection");
        }} />
      </section>
      <section id="podcast-config-selection" className={`surface podcast-config-section podcast-selection${activePanel === "selection" ? " is-active" : ""}`} aria-label="選擇 Podcast 概念">
        <div className="cards-selection-heading"><h2>選擇概念</h2><span aria-live="polite">已選 {selected.size} / {view.concepts.length}</span></div>
        <input className="cards-concept-search" type="search" aria-label="搜尋 Podcast 概念" placeholder="搜尋概念…" value={query} onChange={e => setQuery(e.target.value)} />
        <div className="podcast-selection-tools"><div><button type="button" className="text-button" disabled={busy} onClick={() => selectVisible(true)}>{query.trim() ? "選取搜尋結果" : "全選"}</button><span>／</span><button type="button" className="text-button" disabled={busy} onClick={() => selectVisible(false)}>{query.trim() ? "取消搜尋結果" : "取消全選"}</button></div>
          <label><input type="checkbox" checked={onlySelected} onChange={e => setOnlySelected(e.target.checked)} />只看已選</label></div>
        <div className="podcast-concept-list">{visible.map(concept => <div className={`podcast-concept-option${selected.has(concept.concept_id) ? " is-selected" : ""}`} key={concept.concept_id}>
          <label><input type="checkbox" disabled={busy} checked={selected.has(concept.concept_id)} onChange={() => setSelected(previous => { const next = new Set(previous); if (next.has(concept.concept_id)) next.delete(concept.concept_id); else next.add(concept.concept_id); return next; })} />
            <strong title={concept.label}>{concept.label}</strong><small>{concept.claims.length} 點</small></label>
          <button type="button" className="text-button" aria-label={`查看「${concept.label}」重點`} aria-pressed={previewId === concept.concept_id} onClick={() => setPreviewId(previewId === concept.concept_id ? null : concept.concept_id)}>預覽</button>
        </div>)}{visible.length === 0 && <p>{onlySelected ? "目前篩選範圍沒有已選概念。" : "找不到符合的概念。"}</p>}</div>
        {preview && <section className="podcast-concept-preview" aria-label={`${preview.label}重點預覽`}><header><strong>{preview.label}</strong><button type="button" className="text-button" onClick={() => setPreviewId(null)}>關閉預覽</button></header><div>{preview.claims.map(claim => <p className="podcast-claim-text" key={claim.claim_id}>{claimText(claim)}</p>)}</div></section>}
      </section>
      <section id="podcast-config-settings" className={`surface podcast-config-section podcast-settings${activePanel === "settings" ? " is-active" : ""}`} aria-label="講解設定">
        <h2>講解設定</h2>
        <fieldset className="podcast-delivery"><legend>講解形式</legend><label><input type="radio" name="delivery" checked={delivery === "dialogue"} disabled={busy} onChange={() => setDelivery("dialogue")} /><span><strong>雙人對談</strong><small>以對話串起教材重點。</small></span></label><label><input type="radio" name="delivery" checked={delivery === "solo"} disabled={busy} onChange={() => setDelivery("solo")} /><span><strong>單人解說</strong><small>以自然口語說明重點。</small></span></label></fieldset>
        <div className="podcast-mode-options" role="group" aria-label="講解模式"><button type="button" disabled={busy} aria-pressed={mode === "quick"} onClick={() => setMode("quick")}><strong>快速複習</strong><span>保留全部重點與必要條件，省略延伸。</span></button><button type="button" disabled={busy} aria-pressed={mode === "full"} onClick={() => setMode("full")}><strong>完整講解</strong><span>較完整的說明、例子與理解整理。</span></button></div>
      </section>
    </div>
    <footer className="surface podcast-create-footer"><div><strong>{selected.size} 個概念 · {claimCount} 個重點</strong><span>所選重點全部保留，內容較多時自動拆集。</span></div><button type="button" className="primary-button" disabled={busy || !selected.size || !validName} onClick={() => void save()}>{busy ? "正在建立…" : "開始生成 Podcast"}<Icon name="chevron-right" size={18} /></button>{saveError && <p className="form-error" role="alert">{saveError}</p>}</footer>
  </section>;
}
