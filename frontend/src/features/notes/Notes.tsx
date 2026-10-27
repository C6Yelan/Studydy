import { MathText } from "../../ui/MathText";
import { Progress } from "../../ui/Progress";
import { ExportControls } from "../exports/ExportControls";
import { useEffect, useRef, useState, type CSSProperties } from "react";
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

function Items({ items }: { items: NoteItem[] }) {
  return <ul className="note-bullets">{items.map((item, i) => <li key={i}><MathText text={item.text}/>{item.children?.length ? <Items items={item.children}/> : null}</li>)}</ul>;
}

function Table({ label, caption, headers, rows }: { label: string; caption: string | null; headers: string[]; rows: string[][] }) {
  return <div className="note-table-scroll" role="region" aria-label={caption ?? label} tabIndex={0}><table>
    {caption && <caption>{caption}</caption>}
    <thead><tr>{headers.map((cell, i) => <th key={i} scope="col"><MathText text={cell}/></th>)}</tr></thead>
    <tbody>{rows.map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j}><MathText text={cell}/></td>)}</tr>)}</tbody>
  </table></div>;
}

function Block({ block, heading }: { block: NoteBlock; heading: string }) {
  switch (block.type) {
    case "text": return <p className="note-prose"><MathText text={block.text}/></p>;
    case "bullets": return <Items items={block.items}/>;
    case "table": return <Table label={`${heading}表格`} caption={block.caption} headers={block.headers} rows={block.rows}/>;
    case "formula": return <div className="note-formula"><MathText text={`$$${block.latex}$$`}/></div>;
    case "callout": {
      const label = calloutLabels[block.style];
      return <aside className={`note-callout note-callout-${block.style}`} aria-label={label}>
        <p className="note-callout-title">{label}{block.title && `：${block.title}`}</p><Items items={block.items}/>
      </aside>;
    }
  }
}

function ParagraphBody({ paragraph: p, number }: { paragraph: NoteParagraph; number?: NoteOutlineEntry }) {
  if (!("blocks" in p)) {
    // v11 以前的段落格式，重新生成前仍可閱讀。
    return <><h3>{p.heading}</h3><p className="note-prose"><MathText text={p.text}/></p>
      {p.formula && <p className="note-formula-text"><MathText text={p.formula}/></p>}
      {p.table && <Table label={`${p.heading}表格`} caption={null} headers={p.table.headers} rows={p.table.rows}/>}
    </>;
  }
  const level = number?.level ?? p.level;
  const Heading = level === 1 ? "h2" : "h3";
  return <><Heading>{number ? numberedHeading(number) : p.heading}</Heading>
    {p.blocks.map((block, i) => <Block key={i} block={block} heading={p.heading}/>)}
  </>;
}

function Outline({ entries, active, jump }: { entries: NoteOutlineEntry[]; active: string; jump: (id: string) => void }) {
  return <nav className="note-outline" aria-label="章節大綱"><h2>章節大綱</h2>
    <ul>{entries.map(o => <li key={o.paragraph_id} className={`note-outline-${o.level}`}>
      <button type="button" className="text-button" aria-current={active === o.paragraph_id ? "location" : undefined}
        onClick={() => jump(o.paragraph_id)}>{numberedHeading(o)}</button>
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
  NOTE_FORMAT_INVALID: "筆記回應格式不完整，尚未發布。可重新生成。",
  NOTE_CONTENT_UNSUPPORTED: "筆記內容與引用原文不符，重新生成後仍未通過核對，尚未發布。",
  NOTE_REVIEW_INVALID: "筆記內容核對未完成，尚未發布。請稍後重試。",
  NOTE_CONTRACT_INVALID: "筆記生成規則已更新，請重新生成。已完成的筆記與註記仍保留。",
  NOTE_COVERAGE_INCOMPLETE: "筆記仍有遺漏或未說明的重點，尚未發布。可重新生成。",
  NOTE_INPUT_TOO_LARGE: "教材內容超出這次筆記生成容量，尚未發布。",
  NOTE_REFERENCE_INVALID: "筆記的來源引用未通過驗證，尚未發布。可重新生成。",
  NOTE_SOURCE_EMPTY: "這份教材目前沒有可用來寫筆記的重點來源。",
};

function NotesContent({ api, materialId, revision, kind, changeKind }: { api: StudydyApiClient; materialId: string; revision: string; kind: NoteKind; changeKind: (kind: NoteKind) => void }) {
  const [view, setView] = useState<MaterialNotesView | null>(null);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const [busy, setBusy] = useState(false);
  const [editor, setEditor] = useState<Editor | null>(null);
  const sequence = useRef(0);
  const toolbar = useRef<HTMLDivElement>(null);
  const outlineDialog = useRef<HTMLDialogElement>(null);
  const outlineTrigger = useRef<HTMLButtonElement>(null);
  const jumpAfterClose = useRef<string | null>(null);
  const confirmDialog = useRef<HTMLDialogElement>(null);
  const regenerateTrigger = useRef<HTMLButtonElement>(null);
  const [outlineExpanded, setOutlineExpanded] = useState(true);
  const [activeSection, setActiveSection] = useState("");
  const [toolbarHeight, setToolbarHeight] = useState(0);
  const [now, setNow] = useState(Date.now());
  const pending = view?.status === "pending" || view?.status === "running";
  useEffect(() => {
    const element = toolbar.current;
    if (!element) return;
    const observer = new ResizeObserver(() => setToolbarHeight(element.getBoundingClientRect().height));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!pending || !view?.started_at) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [pending, view?.started_at]);
  useEffect(() => {
    const entries = view?.outline ?? [];
    const track = () => {
      const boundary = (toolbar.current?.getBoundingClientRect().bottom ?? 0) + 24;
      let current = entries[0]?.paragraph_id ?? "";
      for (const entry of entries) {
        const section = document.getElementById(`note-section-${entry.paragraph_id}`);
        if (section && section.getBoundingClientRect().top <= boundary) current = entry.paragraph_id;
      }
      if (window.scrollY > 0 && window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 1) {
        current = entries.at(-1)?.paragraph_id ?? current;
      }
      setActiveSection(current);
    };
    track();
    window.addEventListener("scroll", track, { passive: true });
    window.addEventListener("resize", track);
    return () => { window.removeEventListener("scroll", track); window.removeEventListener("resize", track); };
  }, [view?.outline, toolbarHeight]);
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
  return <div className="note-workspace" style={{ "--note-toolbar-height": `${toolbarHeight}px` } as CSSProperties}>
    <div ref={toolbar} className="note-reading-toolbar">
      <div className="note-kinds" role="group" aria-label="筆記種類">{(["summary", "full"] as const).map(k => <button key={k} type="button" aria-pressed={kind === k}
        className={kind === k ? "primary-button" : "secondary-button"} onClick={() => changeKind(k)}>{k === "summary" ? "重點整理" : "完整筆記"}</button>)}</div>
      <div className="note-reading-actions">
        {!!view?.outline.length && <button ref={outlineTrigger} type="button" className="secondary-button note-outline-mobile" onClick={() => outlineDialog.current?.showModal()}>章節目錄</button>}
        {!!view?.paragraphs.length && <ExportControls label="匯出筆記" options={[
          { value: "pdf", label: "PDF" }, { value: "md", label: "Markdown" }, { value: "docx", label: "Word（.docx）" },
        ]} download={format => api.downloadExport(`/v1/materials/${materialId}/knowledge-structures/${view.content_revision}/notes/${kind}/export/${format}`)} />}
        {kind === "full" && !!view?.paragraphs.length && !view.update_available && <details className="note-more">
          <summary aria-label="更多筆記操作">⋯</summary>
          <div><button ref={regenerateTrigger} type="button" className="text-button" disabled={busy || pending} onClick={() => confirmDialog.current?.showModal()}>重新生成</button></div>
        </details>}
      </div>
    </div>
    <div className={`note-reading-layout${outlineExpanded && view?.outline.length ? " has-outline" : ""}`}>
      {!!view?.outline.length && <aside className="note-outline-desktop">
        <button type="button" className="text-button" aria-expanded={outlineExpanded} onClick={() => setOutlineExpanded(v => !v)}>{outlineExpanded ? "收合目錄" : "展開目錄"}</button>
        {outlineExpanded && <Outline entries={view.outline} active={activeSection} jump={jump}/>}
      </aside>}
      <div className="note-reading-body">
        <p className="note-caption">{kind === "summary" ? "每個概念只整理最核心的重點，細節見完整筆記；長度隨教材大小而定。" : "涵蓋教材所有重點，依學習順序閱讀。"}內文不可編輯，可在段落旁加上自己的註記。</p>
    {error && <div role="alert"><p className="form-error">{error}</p><button className="text-button" onClick={() => setReload(v => v + 1)}>重新讀取</button></div>}
    {!view && !error && <p role="status">正在讀取筆記…</p>}
    {view && <>
      <p className="note-source-notice">由 AI 整理，每個重點都可點開原文對照。</p>
      <div className="note-status" aria-live="polite">
        {pending ? <p>{view.paragraphs.length > 0 ? "正在更新筆記，完成後自動替換" : view.status === "pending" ? "筆記已排入生成佇列。" : "正在撰寫並逐段核對筆記內容…"}</p>
          : view.status === "failed" ? <p>{failureMessages[view.error_code ?? ""] ?? "筆記生成未完成，請稍後重試。"}{view.paragraphs.length > 0 && "以下保留上一份筆記。"}</p>
          : view.status === "not_generated" ? <p>{kind === "full" ? "完整筆記依教材學習順序涵蓋所有重點，按下按鈕後開始生成。" : "重點整理尚未生成。"}</p> : null}
        {!pending && kind === "full" && view.update_available && <p>教材已更新，要更新完整筆記嗎？</p>}
        {(pending || view.status === "failed" || (kind === "full" && (!view.paragraphs.length || view.update_available))) && <button type="button" className={view.paragraphs.length ? "secondary-button" : "primary-button"} disabled={busy || pending} onClick={() => void generate()}>
          {busy ? "正在送出…" : pending ? "生成中" : view.status === "failed" ? "重試生成" : view.update_available ? "更新完整筆記" : "生成完整筆記"}
        </button>}
      </div>
      {(pending || view.progress) && <section className="note-progress" aria-label="完整筆記分區進度">
        <p>{view.status === "pending" ? "正在等待開始" : view.progress?.stage === "validating" ? "正在核對完整筆記…" : view.progress?.stage === "completed" ? "分區生成完成" : `正在整理：${view.progress?.current_section ?? "教材"}`}</p>
        <Progress aria-label="已完成分區" value={view.progress && view.progress.completed_parts > 0 ? view.progress.completed_sections : undefined} max={Math.max(1, view.progress?.total_sections ?? 1)}/>
        {view.progress && <p>已完成 {view.progress.completed_sections} / {view.progress.total_sections} 個分區</p>}
        {pending && elapsed !== null && <p>已耗時 {Math.floor(elapsed / 60)} 分 {elapsed % 60} 秒</p>}
        {view.status === "failed" && view.progress && view.progress.completed_parts > 0 && <p>已完成部分已保存，重試時會從未完成部分接續。</p>}
      </section>}
      {view.paragraphs.map(p => { const number = numbers.get(p.id); return <section key={`${view.content_revision}:${view.content_generation}:${p.id}`} id={`note-section-${p.id}`} tabIndex={-1}
        className={`note-paragraph${number?.level === 2 ? " note-subsection" : ""}`} aria-label={number ? numberedHeading(number) : p.heading}>
        <ParagraphBody paragraph={p} number={number}/>
        <div className="note-concepts">{p.concepts.map(c => <button className="text-button" key={c.concept_id} onClick={() => void openConcept(c.concept_id)}>{c.label} · 查看地圖</button>)}</div>
        <div className="note-sources" aria-label="段落來源">{sourceLinks(p.evidence).map(e => <SourceButton key={e.evidence_id} apiClient={api} resolver={view.source_resolver} evidence={e} reference={{quote:e.quote}}/>)}</div>
        {view.annotations.filter(a => a.paragraph_id === p.id).map(annotationItem)}
        <button className="text-button" onClick={e => edit(e.currentTarget, p.id)}>新增註記</button>
      </section>; })}
      {orphaned.length > 0 && <section className="note-previous" aria-labelledby="previous-notes-title"><h2 id="previous-notes-title">先前段落的註記</h2>
        <p>以下註記的來源與重點無法精確對應目前段落，已保留當時內容。</p>
        {orphaned.map(a => <div key={a.annotation_id} className="note-previous-item"><details><summary>當時段落：{a.original_paragraph.heading}</summary>
          <ParagraphBody paragraph={a.original_paragraph}/>
          <div className="note-sources">{sourceLinks(a.original_paragraph.evidence).map(e => <SourceButton key={e.evidence_id} apiClient={api} evidence={e} reference={{quote:e.quote}}
            resolver={`/v1/materials/${materialId}/knowledge-structures/${a.original_revision}/evidence`}/>)}</div>
        </details>{annotationItem(a)}</div>)}
      </section>}
    </>}
      </div>
    </div>
    <dialog ref={outlineDialog} className="note-outline-drawer" aria-label="章節目錄" onClose={() => {
      const target = jumpAfterClose.current;
      jumpAfterClose.current = null;
      if (target !== null) jump(target); else outlineTrigger.current?.focus();
    }}>
      <button type="button" className="secondary-button" onClick={() => outlineDialog.current?.close()}>關閉目錄</button>
      <Outline entries={view?.outline ?? []} active={activeSection} jump={id => { jumpAfterClose.current = id; outlineDialog.current?.close(); }}/>
    </dialog>
    <dialog ref={confirmDialog} className="note-regenerate-dialog" aria-labelledby="note-regenerate-title" onClose={() => regenerateTrigger.current?.focus()}>
      <h2 id="note-regenerate-title">重新生成完整筆記</h2>
      <p>將重新撰寫整份筆記，可能需要數分鐘</p>
      <div className="state-actions"><button type="button" className="secondary-button" onClick={() => confirmDialog.current?.close()}>取消</button>
        <button type="button" className="primary-button" onClick={() => { confirmDialog.current?.close(); void generate(); }}>確認重新生成</button></div>
    </dialog>
    {editor && <AnnotationEditor key={editor.id} api={api} editor={editor} close={() => setEditor(null)} saved={update}/>}
  </div>;
}

export function Notes({ api, materialId, revision }: { api: StudydyApiClient; materialId: string; revision: string }) {
  const [kind, setKind] = useState<NoteKind>("summary");
  return <div className="notes-page"><header><h1>筆記</h1><p>依教材整理的閱讀筆記，每段都能回查原文。</p></header>
    <NotesContent key={`${materialId}:${revision}:${kind}`} api={api} materialId={materialId} revision={revision} kind={kind} changeKind={setKind}/>
  </div>;
}
