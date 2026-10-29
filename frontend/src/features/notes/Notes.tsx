import { MathText } from "../../ui/MathText";
import { Icon } from "../../ui/Icon";
import { Progress } from "../../ui/Progress";
import { ExportControls } from "../exports/ExportControls";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { MaterialNotesView, NoteAnnotation, NoteBlock, NoteCalloutStyle, NoteItem, NoteKind, NoteOutlineEntry, NoteParagraph } from "../../api/contracts";
import { writeRoute } from "../../app/routes";
import { SourceButton, sourceLinks } from "../../ui/SourceButton";
import "./styles.css";

const calloutLabels: Record<NoteCalloutStyle, string> = {
  case_study: "案例研究", discussion: "討論問題", reminder: "重要提醒", lesson: "重要教訓",
};

/** 「一、標題」與「1.1 標題」：章節編號後不空格，小節編號後空一格。 */
export function numberedHeading(entry: NoteOutlineEntry) {
  return entry.number + (entry.level === 1 ? "" : " ") + entry.heading;
}

function Items({ items, tail }: { items: NoteItem[]; tail?: ReactNode }) {
  return <ul className="note-bullets">{items.map((item, i) => <li key={i}><MathText text={item.text}/>{item.children?.length ? <Items items={item.children} tail={i === items.length - 1 ? tail : undefined}/> : i === items.length - 1 ? tail : null}</li>)}</ul>;
}

function Table({ label, caption, headers, rows, tail }: { tail?: ReactNode; label: string; caption: string | null; headers: string[]; rows: string[][] }) {
  return <div className="note-table-scroll" role="region" aria-label={caption ?? label} tabIndex={0}><table>
    {caption && <caption>{caption}</caption>}
    <thead><tr>{headers.map((cell, i) => <th key={i} scope="col"><MathText text={cell}/></th>)}</tr></thead>
    <tbody>{rows.map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j}><MathText text={cell}/>{i === rows.length - 1 && j === row.length - 1 && tail}</td>)}</tr>)}</tbody>
  </table></div>;
}

function Block({ block, heading, tail }: { block: NoteBlock; heading: string; tail?: ReactNode }) {
  switch (block.type) {
    case "text": return <p className="note-prose"><MathText text={block.text}/>{tail}</p>;
    case "bullets": return <Items items={block.items} tail={tail}/>;
    case "table": return <Table label={`${heading}表格`} caption={block.caption} headers={block.headers} rows={block.rows} tail={tail}/>;
    case "formula": return <div className="note-formula"><MathText text={`$$${block.latex}$$`}/>{tail}</div>;
    case "callout": {
      const label = calloutLabels[block.style];
      return <aside className={`note-callout note-callout-${block.style}`} aria-label={label}>
        <p className="note-callout-title">{label}{block.title && `：${block.title}`}</p><Items items={block.items} tail={tail}/>
      </aside>;
    }
  }
}

/**
 * 比照文件編輯器的邊欄：滑過或聚焦段落時，右側出現「新增註記」與「在地圖查看」圖示，
 * 不在正文下方插入一排文字連結；多個概念時點地圖圖示展開清單。
 */
function ParagraphActions({ concepts, openConcept, annotate }: {
  concepts: NoteParagraph["concepts"]; openConcept: (id: string) => void; annotate: (opener: HTMLButtonElement) => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);
  return <div ref={root} className="note-paragraph-actions" data-open={open || undefined}>
    <button type="button" className="note-action" aria-label="新增註記" title="新增註記" onClick={e => annotate(e.currentTarget)}><Icon name="edit" size={18}/></button>
    {concepts.length === 1 ? <button type="button" className="note-action" aria-label={`${concepts[0].label} · 查看地圖`} title={`在地圖查看「${concepts[0].label}」`} onClick={() => openConcept(concepts[0].concept_id)}><Icon name="map" size={18}/></button>
      : concepts.length > 1 && <>
        <button type="button" className="note-action" aria-label="在地圖查看" title="在地圖查看" aria-expanded={open} onClick={() => setOpen(v => !v)}><Icon name="map" size={18}/></button>
        {open && <div className="note-concept-menu" role="group" aria-label="在地圖查看">{concepts.map(c => <button type="button" key={c.concept_id} aria-label={`${c.label} · 查看地圖`} onClick={() => { setOpen(false); openConcept(c.concept_id); }}>{c.label}</button>)}</div>}
      </>}
  </div>;
}

function ParagraphBody({ paragraph: p, number, sources }: { paragraph: NoteParagraph; number?: NoteOutlineEntry; sources?: ReactNode }) {
  if (!("blocks" in p)) {
    // v11 以前的段落格式，重新生成前仍可閱讀。
    return <><h3>{p.heading}</h3><p className="note-prose"><MathText text={p.text}/>{!p.formula && !p.table && sources}</p>
      {p.formula && <p className="note-formula-text"><MathText text={p.formula}/>{!p.table && sources}</p>}
      {p.table && <Table label={`${p.heading}表格`} caption={null} headers={p.table.headers} rows={p.table.rows} tail={sources}/>}
    </>;
  }
  const level = number?.level ?? p.level;
  const Heading = level === 1 ? "h2" : "h3";
  return <><Heading>{number ? numberedHeading(number) : p.heading}</Heading>
    {p.blocks.map((block, i) => <Block key={i} block={block} heading={p.heading} tail={i === p.blocks.length - 1 ? sources : undefined}/>)}
  </>;
}

function Outline({ entries, active, jump }: { entries: NoteOutlineEntry[]; active: string; jump: (id: string) => void }) {
  const navigation = useRef<HTMLElement>(null);
  useEffect(() => {
    const container = navigation.current;
    const selected = container?.querySelector<HTMLButtonElement>('[aria-current="location"]');
    if (!container || !selected) return;
    const bounds = container.getBoundingClientRect();
    const item = selected.getBoundingClientRect();
    if (item.top < bounds.top) container.scrollTop += item.top - bounds.top;
    else if (item.bottom > bounds.bottom) container.scrollTop += item.bottom - bounds.bottom;
  }, [active]);
  return <nav ref={navigation} className="note-outline" aria-label="章節大綱">
    <ul>{entries.map(o => <li key={o.paragraph_id} className={`note-outline-${o.level}`}>
      <button type="button" className="text-button" aria-current={active === o.paragraph_id ? "location" : undefined}
        title={numberedHeading(o)} onClick={() => jump(o.paragraph_id)}>{numberedHeading(o)}</button>
    </li>)}</ul>
  </nav>;
}

type Editor = { view: MaterialNotesView; paragraphId: string | null; annotation?: NoteAnnotation; id: string; opener: HTMLButtonElement };

function AnnotationEditor({ api, editor, close, saved }: { api: StudydyApiClient; editor: Editor; close: () => void; saved: (view: MaterialNotesView) => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [body, setBody] = useState(editor.annotation?.body ?? "");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { dialog.current?.showModal(); }, []);
  const save = async (remove = false) => {
    if (busy) return;
    setBusy(true); setError("");
    try {
      const result = await api.saveNoteAnnotation(editor.view, editor.id, editor.paragraphId, remove ? "" : body);
      saved(result); dialog.current?.close();
    } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  };
  return <dialog ref={dialog} className="note-editor" aria-labelledby="note-editor-title" onCancel={e => { if (busy) e.preventDefault(); }} onClose={() => { editor.opener.focus(); close(); }}>
    <form onSubmit={e => { e.preventDefault(); void save(); }}>
      <h2 id="note-editor-title">{editor.annotation ? "編輯個人註記" : "新增個人註記"}</h2>
      <label>個人註記<textarea autoFocus rows={7} maxLength={10000} value={body} onChange={e => setBody(e.target.value)} disabled={busy}/></label>
      {error && <p role="alert" className="form-error">{error}</p>}
      <div className="state-actions">
        <button className="primary-button" disabled={busy || !body.trim()} type="submit">{busy ? "正在儲存…" : "儲存註記"}</button>
        <button className="secondary-button" disabled={busy} type="button" onClick={() => dialog.current?.close()}>取消</button>
        {editor.annotation && <button className="text-button" disabled={busy} type="button" onClick={() => void save(true)}>刪除註記</button>}
      </div>
    </form>
  </dialog>;
}

const failureMessages: Record<string, string> = {
  NOTE_FORMAT_INVALID: "筆記回應格式不完整，尚未發布。可重試生成。",
  NOTE_CONTENT_UNSUPPORTED: "筆記內容與引用原文不符，重新生成後仍未通過核對，尚未發布。",
  NOTE_REVIEW_INVALID: "筆記內容核對未完成，尚未發布。請稍後重試。",
  NOTE_CONTRACT_INVALID: "筆記生成規則已更新，請重試生成。已完成的筆記與註記仍保留。",
  NOTE_COVERAGE_INCOMPLETE: "筆記仍有遺漏或未說明的重點，尚未發布。可重試生成。",
  NOTE_INPUT_TOO_LARGE: "教材內容超出這次筆記生成容量，尚未發布。",
  NOTE_REFERENCE_INVALID: "筆記的來源引用未通過驗證，尚未發布。可重試生成。",
  NOTE_SOURCE_EMPTY: "這份教材目前沒有可用來寫筆記的重點來源。",
};

function NotesContent({ api, materialId, revision, kind, changeKind }: { api: StudydyApiClient; materialId: string; revision: string; kind: NoteKind; changeKind: (kind: NoteKind) => void }) {
  const [view, setView] = useState<MaterialNotesView | null>(null);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const [busy, setBusy] = useState(false);
  const [editor, setEditor] = useState<Editor | null>(null);
  const sequence = useRef(0);
  const readingBody = useRef<HTMLDivElement>(null);
  const outlineDialog = useRef<HTMLDialogElement>(null);
  const outlineTrigger = useRef<HTMLButtonElement>(null);
  const jumpAfterClose = useRef<string | null>(null);
  const [outlineExpanded, setOutlineExpanded] = useState(true);
  const [outlineDrawerOpen, setOutlineDrawerOpen] = useState(false);
  const [activeSection, setActiveSection] = useState("");
  const positionKey = `studydy:notes-position:${materialId}:${revision}:${kind}`;
  const [previousPosition, setPreviousPosition] = useState(() => {
    try { return Number(localStorage.getItem(positionKey)) || 0; } catch { return 0; }
  });
  const [now, setNow] = useState(Date.now());
  const pending = view?.status === "pending" || view?.status === "running";
  useEffect(() => {
    if (!pending || !view?.started_at) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [pending, view?.started_at]);
  useEffect(() => {
    const entries = view?.outline ?? [];
    const body = readingBody.current;
    if (!body) return;
    const track = () => {
      const boundary = body.getBoundingClientRect().top + 32;
      let current = entries[0]?.paragraph_id ?? "";
      for (const entry of entries) {
        const section = document.getElementById(`note-section-${entry.paragraph_id}`);
        if (section && section.getBoundingClientRect().top <= boundary) current = entry.paragraph_id;
      }
      if (body.scrollTop > 0 && body.scrollTop + body.clientHeight >= body.scrollHeight - 1) {
        current = entries.at(-1)?.paragraph_id ?? current;
      }
      setActiveSection(current);
    };
    track();
    const read = () => {
      track();
      try { localStorage.setItem(positionKey, String(body.scrollTop)); } catch { /* 瀏覽器停用儲存時仍可閱讀。 */ }
    };
    body.addEventListener("scroll", read, { passive: true });
    window.addEventListener("resize", track);
    return () => { body.removeEventListener("scroll", read); window.removeEventListener("resize", track); };
  }, [view?.outline, positionKey]);
  const jump = (id: string) => {
    const target = document.getElementById(`note-section-${id}`);
    target?.scrollIntoView({ block: "start" });
    target?.focus({ preventScroll: true });
    setActiveSection(id);
  };
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      const request = ++sequence.current;
      try {
        const result = await api.getNotes(materialId, revision, kind);
        if (cancelled || request !== sequence.current) return;
        setView(result); setError("");
        if (result.status === "pending" || result.status === "running") timer = setTimeout(poll, 2000);
      } catch (e) { if (!cancelled && request === sequence.current) setError(errorMessage(e)); }
    };
    void poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [api, materialId, revision, kind, reload]);
  const update = (result: MaterialNotesView) => { sequence.current++; setView(result); setReload(v => v + 1); };
  const generate = async () => {
    if (!view || busy) return;
    setBusy(true); setError(""); sequence.current++;
    try { update(await api.generateNotes(materialId, revision, kind, view.generation)); }
    catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  };
  const edit = (opener: HTMLButtonElement, paragraphId: string | null, annotation?: NoteAnnotation) => {
    if (view) setEditor({ view, paragraphId, annotation, id: annotation?.annotation_id ?? crypto.randomUUID(), opener });
  };
  const annotationItem = (a: NoteAnnotation) => <aside key={a.annotation_id} className="note-annotation" aria-label="個人註記"><p>{a.body}</p>
    <button className="text-button" onClick={e => edit(e.currentTarget, a.paragraph_id, a)}>編輯註記</button></aside>;
  const orphaned = view?.annotations.filter(a => a.paragraph_id === null) ?? [];
  const numbers = new Map((view?.outline ?? []).map(o => [o.paragraph_id, o]));
  const elapsed = view?.started_at ? Math.max(0, Math.floor((now - Date.parse(view.started_at)) / 1000)) : null;
  const openConcept = async (concept: string) => {
    if (!view?.content_revision) return;
    try {
      const contentRevision = view.content_revision;
      const runId = contentRevision === revision ? view.run_id
        : (await api.getKnowledgeOutline({ materialId, structureRevision: contentRevision })).run_id;
      writeRoute({ name: "knowledge-map", materialId, runId, structureRevision: contentRevision }, false,
        { knowledgeMap: { revision: contentRevision, mode: "focus", concept, detail: concept } });
    } catch (e) { setError(errorMessage(e)); }
  };
  return <div className="note-workspace">
    <div className="note-reading-toolbar">
      <div className="note-reading-controls">
      {!!view?.outline.length && <>
        <button type="button" className="note-outline-toggle note-outline-desktop-toggle" aria-label="章節目錄" title="章節目錄" aria-expanded={outlineExpanded} onClick={() => setOutlineExpanded(v => !v)}><Icon name="book" size={18}/></button>
        <button ref={outlineTrigger} type="button" className="note-outline-toggle note-outline-mobile-toggle" aria-label="章節目錄" title="章節目錄" aria-expanded={outlineDrawerOpen} aria-haspopup="dialog" onClick={() => { setOutlineDrawerOpen(true); outlineDialog.current?.showModal(); }}><Icon name="book" size={18}/></button>
      </>}
      <div className="note-kinds" role="group" aria-label="筆記種類">{(["summary", "full"] as const).map(k => <button key={k} type="button" aria-pressed={kind === k}
        className={kind === k ? "primary-button" : "secondary-button"} onClick={() => changeKind(k)}>{k === "summary" ? "重點整理" : "完整筆記"}</button>)}</div>
      </div>
      <p className="note-source-notice" title="由 AI 整理，每個重點都可點開原文對照。"><Icon name="sparkle" size={16}/><span>由 AI 整理，每個重點都可點開原文對照。</span></p>
      <div className="note-reading-actions">
        {!!view?.paragraphs.length && <ExportControls label="匯出筆記" options={[
          { value: "pdf", label: "PDF" }, { value: "md", label: "Markdown" }, { value: "docx", label: "Word（.docx）" },
        ]} download={format => api.downloadExport(`/v1/materials/${materialId}/knowledge-structures/${view.content_revision}/notes/${kind}/export/${format}`)} />}
      </div>
    </div>
    <div className={`note-reading-layout${outlineExpanded && view?.outline.length ? " has-outline" : ""}`}>
      {outlineExpanded && !!view?.outline.length && <aside className="note-outline-desktop"><Outline entries={view.outline} active={activeSection} jump={jump}/></aside>}
      <div ref={readingBody} className="note-reading-body"><div className="note-reading-column">
    {error && <div role="alert"><p className="form-error">{error}</p><button className="text-button" onClick={() => setReload(v => v + 1)}>重新讀取</button></div>}
    {!view && !error && <p role="status">正在讀取筆記…</p>}
    {view && <>
      <div className="note-status" aria-live="polite">
        {view.status === "failed" ? <p>{failureMessages[view.error_code ?? ""] ?? "筆記生成未完成，請稍後重試。"}{view.paragraphs.length > 0 && "以下保留上一份筆記。"}</p>
          : view.status === "not_generated" && !view.paragraphs.length ? <p>{kind === "full" ? "完整筆記依教材學習順序涵蓋所有重點，按下按鈕後開始生成。" : "重點整理尚未生成。"}</p> : null}
        {!pending && kind === "full" && view.update_available && <p>教材已更新，要更新完整筆記嗎？</p>}
        {!pending && (view.status === "failed" || (kind === "full" && (!view.paragraphs.length || view.update_available))) && <button type="button" className={view.paragraphs.length ? "secondary-button" : "primary-button"} disabled={busy} onClick={() => void generate()}>
          {busy ? "正在送出…" : view.status === "failed" ? "重試生成" : view.update_available ? "更新完整筆記" : "生成完整筆記"}
        </button>}
      </div>
      {pending && <section className="note-progress" aria-label="完整筆記分區進度" aria-live="polite">
        <span>{view.paragraphs.length > 0 ? "正在更新筆記，完成後自動替換" : view.status === "pending" ? "筆記已排入生成佇列。" : "正在撰寫並逐段核對筆記內容…"}</span>
        <span>{view.status === "pending" ? "正在等待開始" : view.progress?.stage === "validating" ? "正在核對完整筆記…" : `正在整理：${view.progress?.current_section ?? "教材"}`}</span>
        <Progress aria-label="已完成分區" value={view.progress && view.progress.completed_parts > 0 ? view.progress.completed_sections : undefined} max={Math.max(1, view.progress?.total_sections ?? 1)}/>
        {view.progress && <span>已完成 {view.progress.completed_sections} / {view.progress.total_sections} 個分區</span>}
        {elapsed !== null && <span>已耗時 {Math.floor(elapsed / 60)} 分 {elapsed % 60} 秒</span>}
      </section>}
      {view.status === "failed" && view.progress && view.progress.completed_parts > 0 && <p>已完成部分已保存，重試時會從未完成部分接續。</p>}
      {view.paragraphs.map(p => { const number = numbers.get(p.id); return <section key={`${view.content_revision}:${view.content_generation}:${p.id}`} id={`note-section-${p.id}`} tabIndex={-1}
        className={`note-paragraph${number?.level === 2 ? " note-subsection" : ""}`} aria-label={number ? numberedHeading(number) : p.heading}>
        <ParagraphBody paragraph={p} number={number} sources={<span className="note-sources" aria-label="段落來源">{sourceLinks(p.evidence).map(e => <SourceButton key={e.evidence_id} apiClient={api} resolver={view.source_resolver} evidence={e} reference={{quote:e.quote}}/>)}</span>}/>
        <ParagraphActions concepts={p.concepts} openConcept={id => void openConcept(id)} annotate={opener => edit(opener, p.id)}/>
        {view.annotations.filter(a => a.paragraph_id === p.id).map(annotationItem)}
      </section>; })}
      {orphaned.length > 0 && <section className="note-previous" aria-labelledby="previous-notes-title"><h2 id="previous-notes-title">先前段落的註記</h2>
        <p>以下註記的來源與重點無法精確對應目前段落，已保留當時內容。</p>
        {orphaned.map(a => <div key={a.annotation_id} className="note-previous-item"><details><summary>當時段落：{a.original_paragraph.heading}</summary>
          <ParagraphBody paragraph={a.original_paragraph} sources={<span className="note-sources">{sourceLinks(a.original_paragraph.evidence).map(e => <SourceButton key={e.evidence_id} apiClient={api} evidence={e} reference={{quote:e.quote}}
            resolver={`/v1/materials/${materialId}/knowledge-structures/${a.original_revision}/evidence`}/>)}</span>}/>
        </details>{annotationItem(a)}</div>)}
      </section>}
    </>}
      </div></div>
    </div>
    {previousPosition > 0 && !!view?.paragraphs.length && <button type="button" className="note-resume secondary-button" onClick={() => {
      readingBody.current?.scrollTo({ top: previousPosition }); setPreviousPosition(0);
    }}>回到上次閱讀位置</button>}
    <dialog ref={outlineDialog} className="note-outline-drawer" aria-label="章節目錄" onClose={() => {
      setOutlineDrawerOpen(false);
      const target = jumpAfterClose.current;
      jumpAfterClose.current = null;
      if (target !== null) jump(target); else outlineTrigger.current?.focus();
    }}>
      <button type="button" className="secondary-button" onClick={() => outlineDialog.current?.close()}>關閉目錄</button>
      {outlineDrawerOpen && <Outline entries={view?.outline ?? []} active={activeSection} jump={id => { jumpAfterClose.current = id; outlineDialog.current?.close(); }}/>}
    </dialog>
    {editor && <AnnotationEditor key={editor.id} api={api} editor={editor} close={() => setEditor(null)} saved={update}/>}
  </div>;
}

export function Notes({ api, materialId, revision }: { api: StudydyApiClient; materialId: string; revision: string }) {
  const [kind, setKind] = useState<NoteKind>("summary");
  return <div className="notes-page">
    <NotesContent key={`${materialId}:${revision}:${kind}`} api={api} materialId={materialId} revision={revision} kind={kind} changeKind={setKind}/>
  </div>;
}
