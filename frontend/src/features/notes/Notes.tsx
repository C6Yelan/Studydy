import { ExportControls } from "../exports/ExportControls";
import { useEffect, useRef, useState } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { MaterialNotesView, NoteAnnotation, NoteKind, NoteParagraph } from "../../api/contracts";
import { writeRoute } from "../../app/routes";
import { SourceButton, sourceLinks } from "../../ui/SourceButton";
import "./styles.css";

function ParagraphBody({ paragraph: p }: { paragraph: NoteParagraph }) {
  return <><h3>{p.heading}</h3><p className="note-prose">{p.text}</p>
    {p.formula && <p className="note-formula">{p.formula}</p>}
    {p.table && <div className="note-table-scroll" role="region" aria-label={`${p.heading}表格`} tabIndex={0}><table>
      <thead><tr>{p.table.headers.map((cell, i) => <th key={i} scope="col">{cell}</th>)}</tr></thead>
      <tbody>{p.table.rows.map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j}>{cell}</td>)}</tr>)}</tbody>
    </table></div>}
  </>;
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
  NOTE_SUMMARY_TOO_LONG: "重點整理超過 A4 一頁，尚未發布。可重新生成。",
  NOTE_COVERAGE_INCOMPLETE: "筆記仍有遺漏或未說明的重點，尚未發布。可重新生成。",
  NOTE_INPUT_TOO_LARGE: "教材內容超出這次筆記生成容量，尚未發布。",
  NOTE_REFERENCE_INVALID: "筆記的來源引用未通過驗證，尚未發布。可重新生成。",
  NOTE_SOURCE_EMPTY: "這份教材目前沒有可用來寫筆記的重點來源。",
};

function NotesContent({ api, materialId, revision, kind }: { api: StudydyApiClient; materialId: string; revision: string; kind: NoteKind }) {
  const [view, setView] = useState<MaterialNotesView | null>(null);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const [busy, setBusy] = useState(false);
  const [editor, setEditor] = useState<Editor | null>(null);
  const sequence = useRef(0);
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
  const pending = view?.status === "pending" || view?.status === "running";
  const orphaned = view?.annotations.filter(a => a.paragraph_id === null) ?? [];
  return <>
    {error && <div role="alert"><p className="form-error">{error}</p><button className="text-button" onClick={() => setReload(v => v + 1)}>重新讀取</button></div>}
    {!view && !error && <p role="status">正在讀取筆記…</p>}
    {view && <>
      {(view.source_status.quality === "needs_review" || view.source_status.processing !== "succeeded" || view.source_status.decision !== "retain") &&
        <p className="note-source-notice">教材仍有待核對或尚未完整處理的內容，請配合段落來源閱讀。</p>}
      <div className="note-status" aria-live="polite">
        {pending ? <p>{view.status === "pending" ? "筆記已排入生成佇列。" : "正在撰寫並逐段核對筆記內容…"}{view.paragraphs.length > 0 && "以下仍可閱讀上一份筆記。"}</p>
          : view.status === "failed" ? <p>{failureMessages[view.error_code ?? ""] ?? "筆記生成未完成，請稍後重試。"}{view.paragraphs.length > 0 && "以下保留上一份筆記。"}</p>
          : view.status === "not_generated" ? <p>{kind === "full" ? "完整筆記依教材學習順序涵蓋所有重點，按下按鈕後開始生成。" : "重點整理尚未生成。"}</p> : null}
        <button type="button" className={view.paragraphs.length ? "secondary-button" : "primary-button"} disabled={busy || pending} onClick={() => void generate()}>
          {busy ? "正在送出…" : pending ? "生成中" : view.paragraphs.length ? "重新生成" : view.status === "failed" ? "重試生成" : kind === "full" ? "生成完整筆記" : "生成重點整理"}
        </button>
      </div>
      {view.paragraphs.length > 0 && <ExportControls label="匯出筆記" options={[
        { value: "pdf", label: "PDF" }, { value: "md", label: "Markdown" }, { value: "docx", label: "Word（.docx）" },
      ]} download={format => api.downloadExport(`/v1/materials/${materialId}/knowledge-structures/${revision}/notes/${kind}/export/${format}`)} />}
      {view.progress && <section className="note-progress" aria-label="完整筆記分區進度">
        <p>{view.progress.stage === "validating" ? "正在核對完整筆記…" : view.progress.stage === "completed" ? "分區生成完成" : `正在整理：${view.progress.current_section ?? "教材"}`}</p>
        <progress aria-label="已完成分區" value={view.progress.completed_sections} max={Math.max(1, view.progress.total_sections)}/>
        <p>已完成 {view.progress.completed_sections} / {view.progress.total_sections} 區 · {view.progress.completed_parts} / {view.progress.total_parts} 批</p>
        {view.status === "failed" && view.progress.completed_parts > 0 && <p>已完成部分已保存，重試時會從未完成部分接續。</p>}
      </section>}
      {view.paragraphs.map(p => <section key={`${view.generation}:${p.id}`} className="note-paragraph" aria-label={p.heading}>
        <ParagraphBody paragraph={p}/>
        <div className="note-concepts">{p.concepts.map(c => <button className="text-button" key={c.concept_id} onClick={() => writeRoute({ name: "knowledge-map", materialId, runId: view.run_id, structureRevision: revision }, false,
          { knowledgeMap: { revision, mode: "focus", concept: c.concept_id, detail: c.concept_id } })}>{c.label} · 查看地圖</button>)}</div>
        <div className="note-sources" aria-label="段落來源">{sourceLinks(p.evidence).map(e => <SourceButton key={e.evidence_id} apiClient={api} resolver={view.source_resolver} evidence={e}/>)}</div>
        {view.annotations.filter(a => a.paragraph_id === p.id).map(annotationItem)}
        <button className="text-button" onClick={e => edit(e.currentTarget, p.id)}>新增註記</button>
      </section>)}
      {orphaned.length > 0 && <section className="note-previous" aria-labelledby="previous-notes-title"><h2 id="previous-notes-title">先前段落的註記</h2>
        <p>以下註記的來源與重點無法精確對應目前段落，已保留當時內容。</p>
        {orphaned.map(a => <div key={a.annotation_id} className="note-previous-item"><details><summary>當時段落：{a.original_paragraph.heading}</summary>
          <ParagraphBody paragraph={a.original_paragraph}/>
          <div className="note-sources">{sourceLinks(a.original_paragraph.evidence).map(e => <SourceButton key={e.evidence_id} apiClient={api} evidence={e}
            resolver={`/v1/materials/${materialId}/knowledge-structures/${a.original_revision}/evidence`}/>)}</div>
        </details>{annotationItem(a)}</div>)}
      </section>}
    </>}
    {editor && <AnnotationEditor key={editor.id} api={api} editor={editor} close={() => setEditor(null)} saved={update}/>}
  </>;
}

export function Notes({ api, materialId, revision }: { api: StudydyApiClient; materialId: string; revision: string }) {
  const [kind, setKind] = useState<NoteKind>("summary");
  return <div className="notes-page"><header><h1>筆記</h1><p>依教材整理的閱讀筆記，每段都能回查原文。</p></header>
    <div className="note-kinds" role="group" aria-label="筆記種類">{(["summary", "full"] as const).map(k => <button key={k} type="button" aria-pressed={kind === k}
      className={kind === k ? "primary-button" : "secondary-button"} onClick={() => setKind(k)}>{k === "summary" ? "重點整理" : "完整筆記"}</button>)}</div>
    <p className="note-caption">{kind === "summary" ? "正文以 A4 一頁為限；來源與個人註記另列。" : "涵蓋教材所有重點，依學習順序閱讀。"}內文不可編輯，可在段落旁加上自己的註記。</p>
    <NotesContent key={`${materialId}:${revision}:${kind}`} api={api} materialId={materialId} revision={revision} kind={kind}/>
  </div>;
}
