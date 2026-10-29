import { Icon } from "../../ui/Icon";
import { Progress } from "../../ui/Progress";
import { ExportControls } from "../exports/ExportControls";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { MaterialNotesView, NoteBlock, NoteCalloutStyle, NoteItem, NoteKind, NoteOutlineEntry, NoteParagraph } from "../../api/contracts";
import { MarkupParagraph, NoteMarkup, NoteText } from "./NoteMarkup";
import { writeRoute } from "../../app/routes";
import { SourceButton, sourceLinks } from "../../ui/SourceButton";
import { NotesProgress } from "./NotesProgress";
import { pendingNoteTarget, useLocateParagraph, type NoteTarget } from "./note-location";
import "./styles.css";

const calloutLabels: Record<NoteCalloutStyle, string> = {
  case_study: "案例研究", discussion: "討論問題", reminder: "重要提醒", lesson: "重要教訓",
};

/** 「一、標題」與「1.1 標題」：章節編號後不空格，小節編號後空一格。 */
export function numberedHeading(entry: NoteOutlineEntry) {
  if (!entry.number) return entry.heading;
  return entry.number + (entry.level === 1 ? "" : " ") + entry.heading;
}

function Items({ items, tail, path }: { items: NoteItem[]; tail?: ReactNode; path: (string | number)[] }) {
  return <ul className="note-bullets">{items.map((item, i) => <li key={i}><NoteText text={item.text} path={[...path, i, "text"]}/>{item.children?.length ? <Items items={item.children} path={[...path, i, "children"]} tail={i === items.length - 1 ? tail : undefined}/> : i === items.length - 1 ? tail : null}</li>)}</ul>;
}

function Table({ label, caption, headers, rows, tail, path }: { tail?: ReactNode; label: string; caption: string | null; headers: string[]; rows: string[][]; path: (string | number)[] }) {
  return <div className="note-table-scroll" role="region" aria-label={caption ?? label} tabIndex={0}><table>
    {caption && <caption><NoteText text={caption} path={[...path, "caption"]}/></caption>}
    <thead><tr>{headers.map((cell, i) => <th key={i} scope="col"><NoteText text={cell} path={[...path, "headers", i]}/></th>)}</tr></thead>
    <tbody>{rows.map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j}><NoteText text={cell} path={[...path, "rows", i, j]}/>{i === rows.length - 1 && j === row.length - 1 && tail}</td>)}</tr>)}</tbody>
  </table></div>;
}

function Block({ block, heading, tail, path }: { block: NoteBlock; heading: string; tail?: ReactNode; path: (string | number)[] }) {
  switch (block.type) {
    case "text": return <p className="note-prose"><NoteText text={block.text} path={[...path, "text"]}/>{tail}</p>;
    case "bullets": return <Items items={block.items} path={[...path, "items"]} tail={tail}/>;
    case "table": return <Table label={`${heading}表格`} caption={block.caption} headers={block.headers} rows={block.rows} path={path} tail={tail}/>;
    case "formula": return <div className="note-formula"><NoteText text={block.latex} path={[...path, "latex"]} formula display={`$$${block.latex}$$`}/>{tail}</div>;
    case "callout": {
      const label = calloutLabels[block.style];
      return <aside className={`note-callout note-callout-${block.style}`} aria-label={label}>
        <p className="note-callout-title"><NoteText text={label} path={[...path, "label"]}/>{block.title && <>：<NoteText text={block.title} path={[...path, "title"]}/></>}</p><Items items={block.items} path={[...path, "items"]} tail={tail}/>
      </aside>;
    }
  }
}

function ParagraphBody({ paragraph: p, number, sources, sourceLevel }: { paragraph: NoteParagraph; number?: NoteOutlineEntry; sources?: ReactNode; sourceLevel?: 1 | 2 | 3 }) {
  if (!("blocks" in p)) {
    // v11 以前的段落格式，重新生成前仍可閱讀。
    return <>{p.heading && <h3><NoteText text={p.heading} path={["heading"]}/></h3>}<p className="note-prose"><NoteText text={p.text} path={["text"]}/>{!p.formula && !p.table && sources}</p>
      {p.formula && <p className="note-formula-text"><NoteText text={p.formula} path={["formula"]} formula/>{!p.table && sources}</p>}
      {p.table && <Table label={`${p.heading}表格`} caption={null} headers={p.table.headers} rows={p.table.rows} path={["table"]} tail={sources}/>}
    </>;
  }
  const level = number?.level ?? p.level;
  const Heading = sourceLevel === 3 ? "h5" : sourceLevel === 2 ? "h4" : sourceLevel === 1 ? "h3" : level === 1 ? "h2" : level === 2 ? "h3" : "h4";
  return <>{p.heading && <Heading><NoteText text={number ? numberedHeading(number) : p.heading} path={[number ? "display_heading" : "heading"]}/></Heading>}
    {p.blocks.map((block, i) => <Block key={i} block={block} path={["blocks", i]} heading={p.heading} tail={i === p.blocks.length - 1 ? sources : undefined}/>)}
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

const failureMessages: Record<string, string> = {
  NOTE_NO_SUPPORTED_CONTENT: "本次沒有可保留的筆記內容，已保留先前筆記。",
  NOTE_FORMAT_INVALID: "筆記回應格式不完整，尚未發布。可重試生成。",
  NOTE_CONTENT_UNSUPPORTED: "筆記內容與引用原文不符，重新生成後仍未通過核對，尚未發布。",
  NOTE_REVIEW_INVALID: "筆記內容核對未完成，尚未發布。請稍後重試。",
  NOTE_CONTRACT_INVALID: "筆記生成規則已更新，請重試生成。已完成的筆記與註記仍保留。",
  NOTE_COVERAGE_INCOMPLETE: "筆記仍有遺漏或未說明的重點，尚未發布。可重試生成。",
  NOTE_INPUT_TOO_LARGE: "教材內容超出這次筆記生成容量，尚未發布。",
  NOTE_REFERENCE_INVALID: "筆記的來源引用未通過驗證，尚未發布。可重試生成。",
  NOTE_SOURCE_EMPTY: "這份教材目前沒有可用來寫筆記的重點來源。",
};

function NotesContent({ api, materialId, revision, kind, changeKind, target, openProgress }: { api: StudydyApiClient; materialId: string; revision: string; kind: NoteKind; changeKind: (kind: NoteKind) => void; target: NoteTarget | null; openProgress: () => void }) {
  const [view, setView] = useState<MaterialNotesView | null>(null);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const [busy, setBusy] = useState(false);
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
  useLocateParagraph(view, target);
  const outline: NoteOutlineEntry[] = view?.sections.length
    ? view.sections.map(s => ({ paragraph_id: s.section_id, heading: s.heading, level: s.level, number: "" }))
    : view?.outline ?? [];
  useEffect(() => {
    if (!pending || !view?.started_at) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [pending, view?.started_at]);
  useEffect(() => {
    const entries = outline;
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
  }, [view?.outline, view?.sections, positionKey]);
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
  const paragraph = (p: NoteParagraph, sourceLevel?: 1 | 2 | 3) => {
    if (!view) return null;
    const number = sourceLevel ? undefined : numbers.get(p.id);
    return <MarkupParagraph key={`${view.content_revision}:${view.content_generation}:${p.id}`} paragraph={p}><section id={`note-section-${p.id}`} data-note-target={p.id} tabIndex={-1}
      className={`note-paragraph${sourceLevel ? " note-source-paragraph" : (number?.level ?? 1) > 1 ? " note-subsection" : ""}`} aria-label={number ? numberedHeading(number) : p.heading}>
      <ParagraphBody paragraph={p} number={number} sourceLevel={sourceLevel} sources={<span className="note-sources" aria-label="段落來源">{sourceLinks(p.evidence).map(e => <SourceButton key={e.evidence_id} apiClient={api} resolver={view.source_resolver} evidence={e} reference={{quote:e.quote}}/>)}</span>}/>
    </section></MarkupParagraph>;
  };
  return <div className="note-workspace">
    <div className="note-reading-toolbar">
      <div className="note-reading-controls">
      {!!outline.length && <>
        <button type="button" className="note-outline-toggle note-outline-desktop-toggle" aria-label="章節目錄" title="章節目錄" aria-expanded={outlineExpanded} onClick={() => setOutlineExpanded(v => !v)}><Icon name="book" size={18}/></button>
        <button ref={outlineTrigger} type="button" className="note-outline-toggle note-outline-mobile-toggle" aria-label="章節目錄" title="章節目錄" aria-expanded={outlineDrawerOpen} aria-haspopup="dialog" onClick={() => { setOutlineDrawerOpen(true); outlineDialog.current?.showModal(); }}><Icon name="book" size={18}/></button>
      </>}
      <div className="note-kinds" role="group" aria-label="筆記種類">{(["summary", "full"] as const).map(k => <button key={k} type="button" aria-pressed={kind === k}
        className={kind === k ? "primary-button" : "secondary-button"} onClick={() => changeKind(k)}>{k === "summary" ? "複習速覽" : "完整筆記"}</button>)}</div>
      </div>
      <p className="note-source-notice" title="由 AI 整理，每個重點都可點開原文對照。"><Icon name="sparkle" size={16}/><span>由 AI 整理，每個重點都可點開原文對照。</span></p>
      <div className="note-reading-actions">
        {!!view?.paragraphs.length && <ExportControls label="匯出筆記" options={[
          { value: "pdf", label: "PDF" }, { value: "md", label: "Markdown" }, { value: "docx", label: "Word（.docx）" },
        ]} download={format => api.downloadExport(`/v1/materials/${materialId}/knowledge-structures/${view.content_revision}/notes/${kind}/export/${format}`)} />}
      </div>
    </div>
    <div className={`note-reading-layout${outlineExpanded && outline.length ? " has-outline" : ""}`}>
      {outlineExpanded && !!outline.length && <aside className="note-outline-desktop"><Outline entries={outline} active={activeSection} jump={jump}/></aside>}
      <div ref={readingBody} className="note-reading-body"><div className="note-reading-column">
    {error && <div role="alert"><p className="form-error">{error}</p><button className="text-button" onClick={() => setReload(v => v + 1)}>重新讀取</button></div>}
    {!view && !error && <p role="status">正在讀取筆記…</p>}
    {view && <>
      <div className="note-status" aria-live="polite">
        {view.status === "failed" ? <p>{failureMessages[view.error_code ?? ""] ?? "筆記生成未完成，請稍後重試。"}{view.paragraphs.length > 0 && view.error_code !== "NOTE_NO_SUPPORTED_CONTENT" && "以下保留上一份筆記。"}</p>
          : view.status === "not_generated" && !view.paragraphs.length ? <p>{kind === "full" ? "完整筆記依教材學習順序涵蓋所有重點，按下按鈕後開始生成。" : "複習速覽尚未生成。"}</p> : null}
        {!pending && kind === "full" && view.update_available && <p>教材已更新，要更新完整筆記嗎？</p>}
        {!pending && (view.status === "failed" || (kind === "full" && (!view.paragraphs.length || view.update_available))) && <button type="button" className={view.paragraphs.length ? "secondary-button" : "primary-button"} disabled={busy} onClick={() => void generate()}>
          {busy ? "正在送出…" : view.status === "failed" ? "重試生成" : view.update_available ? "更新完整筆記" : "生成完整筆記"}
        </button>}
      </div>
      {pending && <section className="note-progress" aria-label="完整筆記分區進度" aria-live="polite">
        <span>{view.paragraphs.length > 0 ? "正在更新筆記，完成後自動替換" : view.status === "pending" ? "筆記已排入生成佇列。" : "正在撰寫並逐段核對筆記內容…"}</span>
        <span>{view.status === "pending" ? "正在等待開始" : view.progress?.stage === "validating" ? "正在核對完整筆記…" : `正在整理：${view.progress?.current_section ?? "教材"}`}</span>
        <Progress aria-label="已完成批次" value={view.progress && view.progress.completed_parts > 0 ? view.progress.completed_parts : undefined} max={Math.max(1, view.progress?.total_parts ?? 1)}/>
        {view.progress && <span>已完成 {view.progress.completed_parts} / {view.progress.total_parts} 批</span>}
        {elapsed !== null && <span>已耗時 {Math.floor(elapsed / 60)} 分 {elapsed % 60} 秒</span>}
        <button type="button" className="text-button" onClick={openProgress}>查看進度</button>
      </section>}
      {view.status === "failed" && view.progress && view.progress.completed_parts > 0 && <p>已完成部分已保存，重試時會從未完成部分接續。</p>}
      <NoteMarkup api={api} view={view} saved={update} openConcept={id => void openConcept(id)} renderPrevious={(paragraph, originalRevision) => "target_type" in paragraph ? <p>{paragraph.heading}</p> : <ParagraphBody paragraph={paragraph} sources={<span className="note-sources">{sourceLinks(paragraph.evidence).map(e => <SourceButton key={e.evidence_id} apiClient={api} evidence={e} reference={{ quote: e.quote }} resolver={`/v1/materials/${materialId}/knowledge-structures/${originalRevision}/evidence`}/>)}</span>}/> }>
      {view.sections.length ? view.sections.map(section => {
        const Heading = section.level === 1 ? "h2" : section.level === 2 ? "h3" : "h4";
        const target = { target_type: "section" as const, id: `section:${section.section_id}`, anchor: `section:${section.section_id}`, heading: section.heading, level: section.level };
        return <section key={section.section_id} id={`note-section-${section.section_id}`} data-note-target={target.id} tabIndex={-1}
          className={`note-source-section note-source-level-${section.level}`} aria-label={section.heading}>
          <MarkupParagraph paragraph={target}><Heading><NoteText text={section.heading} path={["heading"]}/></Heading></MarkupParagraph>
          {!!section.outline.length && <nav className="note-chapter-outline" aria-label={`${section.heading}章節大綱`}>
            <p>章節大綱</p><ul>{section.outline.map(entry => <li key={entry.section_id} className={`note-outline-${entry.level}`}>
              <button type="button" className="text-button" onClick={() => jump(entry.section_id)}>{entry.heading}</button>
            </li>)}</ul>
          </nav>}
          {section.paragraph_ids.map(id => paragraph(view.paragraphs.find(p => p.id === id)!, section.level))}
        </section>;
      }) : view.paragraphs.map(p => paragraph(p))}
      </NoteMarkup>
      {!!view.unorganized_concepts.length && <section className="note-unorganized" aria-labelledby="unorganized-concepts-title">
        <h2 id="unorganized-concepts-title">以下概念暫未整理</h2>
        <ul>{view.unorganized_concepts.map(concept => <li key={concept.concept_id}>{concept.label} <button type="button" className="text-button"
          aria-label={`${concept.label} · 在地圖查看`} onClick={() => void openConcept(concept.concept_id)}>在地圖查看</button></li>)}</ul>
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
      {outlineDrawerOpen && <Outline entries={outline} active={activeSection} jump={id => { jumpAfterClose.current = id; outlineDialog.current?.close(); }}/>}
    </dialog>
  </div>;
}

export function Notes({ api, materialId, revision }: { api: StudydyApiClient; materialId: string; revision: string }) {
  const [target] = useState(pendingNoteTarget);
  const [kind, setKind] = useState<NoteKind>(target?.kind ?? "summary");
  // 進度頁用 ?view=progress 區分網址：重新整理停在進度頁，上一頁回筆記。
  const readProgress = () => new URLSearchParams(window.location.search).get("view") === "progress";
  const [progressOpen, setProgressOpen] = useState(readProgress);
  useEffect(() => {
    const sync = () => setProgressOpen(readProgress());
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, []);
  const openProgress = () => { window.history.pushState(window.history.state, "", `${window.location.pathname}?view=progress`); setProgressOpen(true); };
  // 完成、失敗或按返回時用 replaceState，上一頁不會又回到進度頁。
  const closeProgress = useCallback(() => { window.history.replaceState(window.history.state, "", window.location.pathname); setProgressOpen(false); }, []);
  return <div className="notes-page">
    <div hidden={progressOpen} className="notes-page">
      <NotesContent key={`${materialId}:${revision}:${kind}`} api={api} materialId={materialId} revision={revision} kind={kind} changeKind={setKind}
        target={target} openProgress={openProgress}/>
    </div>
    {progressOpen && <NotesProgress api={api} materialId={materialId} revision={revision} kind={kind} close={closeProgress}/>}
  </div>;
}
