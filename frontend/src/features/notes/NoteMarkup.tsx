import { createContext, useContext, useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { HighlightStyle, MaterialNotesView, NoteAnnotation, NoteHighlight, NoteMarkupTarget, NoteSelection } from "../../api/contracts";
import { MathText } from "../../ui/MathText";
import { mathParts } from "../../ui/math-text";
import { Icon } from "../../ui/Icon";

const styles: [HighlightStyle, string][] = [["yellow", "黃色"], ["green", "綠色"], ["blue", "藍色"], ["pink", "粉色"], ["bold", "粗體"], ["underline", "底線"], ["strikethrough", "刪除線"]];
const Markup = createContext<{ highlights: NoteHighlight[]; annotations: NoteAnnotation[]; active: string | null; activate: (id: string) => void; selectFormula: (element: HTMLElement) => void } | null>(null);
const Paragraph = createContext<NoteMarkupTarget | null>(null);
export function MarkupParagraph({ paragraph, children }: { paragraph: NoteMarkupTarget; children: ReactNode }) {
  return <Paragraph.Provider value={paragraph}>{children}</Paragraph.Provider>;
}

export function NoteText({ text, path, formula = false, display }: { text: string; path: (string | number)[]; formula?: boolean; display?: string }) {
  const context = useContext(Markup), paragraph = useContext(Paragraph);
  if (!context || !paragraph) return <MathText text={display ?? text}/>;
  const key = JSON.stringify(path);
  const highlights = context.highlights.filter(h => h.paragraph_id === paragraph.id && JSON.stringify(h.block_path) === key);
  const annotations = context.annotations.filter(a => a.paragraph_id === paragraph.id && JSON.stringify(a.block_path) === key);
  let offset = 0;
  return <>{(formula ? [{ kind: "math", text }] : mathParts(text)).map((part, index) => {
    const start = offset;
    offset += Array.from(part.text).length;
    const end = offset;
    const atomic = part.kind === "math";
    const boundaries = atomic ? [start, end] : [...new Set([start, end, ...[...highlights, ...annotations].flatMap(h => [h.start, h.end]).filter((n): n is number => n != null && n > start && n < end)])].sort((a, b) => a - b);
    return boundaries.slice(0, -1).map((from, i) => {
      const to = boundaries[i + 1];
      const marks = highlights.filter(h => h.start! < to && h.end! > from);
      const notes = annotations.filter(a => a.start! < to && a.end! > from);
      const color = marks.filter(h => ["yellow", "green", "blue", "pink"].includes(h.style)).sort((a, b) => a.created_at.localeCompare(b.created_at)).at(-1)?.style;
      const decorations = [marks.some(h => h.style === "underline") && "underline", marks.some(h => h.style === "strikethrough") && "line-through"].filter(Boolean).join(" ");
      const style: CSSProperties = { fontWeight: marks.some(h => h.style === "bold") ? 700 : undefined, textDecoration: decorations || undefined };
      return <span key={`${index}:${from}`} data-note-text="true" data-paragraph={paragraph.id} data-path={key} data-start={from}
        data-note-formula={atomic ? "true" : undefined} data-note-source={atomic ? part.text : undefined}
        role={atomic ? "button" : undefined} tabIndex={atomic ? 0 : undefined} aria-label={atomic ? `選取公式：${part.text}` : undefined}
        onKeyDown={atomic ? event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); context.selectFormula(event.currentTarget); } } : undefined}
        data-note-ids={notes.length ? notes.map(a => a.annotation_id).join(" ") : undefined} className={`note-mark${atomic ? " note-mark-formula" : ""}${color ? ` note-mark-${color}` : ""}${notes.length ? " note-mark-comment" : ""}${notes.some(a => a.annotation_id === context.active) ? " is-active" : ""}`} style={style}
        onClick={event => {
          if (notes.length) context.activate(notes[(notes.findIndex(a => a.annotation_id === context.active) + 1) % notes.length].annotation_id);
          if (atomic && window.getSelection()?.isCollapsed) context.selectFormula(event.currentTarget);
        }}>{atomic ? <MathText text={display ?? part.text}/> : Array.from(part.text).slice(from - start, to - start).join("")}</span>;
    });
  })}</>;
}

type Draft = { id: string; paragraph_id: string | null; body: string; selection?: NoteSelection };
type Selected = { ranges: NoteSelection[]; quote: string; rect: DOMRect; formula?: HTMLElement };

function CommentCard({ annotation, draft, active, busy, change, save, cancel, edit, remove, activate }: {
  annotation?: NoteAnnotation; draft?: Draft; active: boolean; busy: boolean; change: (body: string) => void; save: () => void; cancel: () => void;
  edit: () => void; remove: () => void; activate: () => void;
}) {
  return <article className={`note-comment-card${active ? " is-active" : ""}`} aria-label="個人註記" onClick={activate}>
    <header><span>個人註記</span>{annotation && <time dateTime={annotation.updated_at}>{new Date(annotation.updated_at).toLocaleString("zh-TW", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}</time>}
      {annotation && !draft && <details className="note-comment-menu"><summary aria-label="註記操作">⋯</summary><div><button type="button" onClick={edit}>編輯註記</button><button type="button" disabled={busy} onClick={remove}>刪除註記</button></div></details>}
    </header>
    {(draft?.selection?.quote ?? annotation?.quote) && <blockquote>{draft?.selection?.quote ?? annotation?.quote}</blockquote>}
    {draft ? <form onSubmit={e => { e.preventDefault(); save(); }}>
      <label>個人註記<textarea autoFocus rows={4} maxLength={10000} value={draft.body} disabled={busy} onChange={e => change(e.target.value)}/></label>
      <div className="state-actions"><button className="primary-button" disabled={busy || !draft.body.trim()}>儲存註記</button><button type="button" className="text-button" disabled={busy} onClick={cancel}>取消</button></div>
    </form> : <p>{annotation?.body}</p>}
  </article>;
}

export function NoteMarkup({ api, view, saved, openConcept, renderPrevious, children }: { api: StudydyApiClient; view: MaterialNotesView; saved: (view: MaterialNotesView) => void; openConcept: (id: string) => void; renderPrevious: (paragraph: NoteMarkupTarget, revision: string) => ReactNode; children: ReactNode }) {
  const root = useRef<HTMLDivElement>(null), toolbar = useRef<HTMLDivElement>(null), drawer = useRef<HTMLDialogElement>(null);
  const [highlights, setHighlights] = useState<NoteHighlight[]>([]), [selected, setSelected] = useState<Selected | null>(null);
  const [active, setActive] = useState<string | null>(null), [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState(""), [busy, setBusy] = useState(false), [conceptMenu, setConceptMenu] = useState(false);
  const [positions, setPositions] = useState<Record<string, number>>({}), [narrow, setNarrow] = useState(false);
  const cardRefs = useRef(new Map<string, HTMLDivElement>());
  const annotations = view.annotations.filter(a => a.paragraph_id !== null);
  const cards = [...annotations.map(a => ({ id: a.annotation_id, paragraph_id: a.paragraph_id, annotation: a })), ...(draft && !view.annotations.some(a => a.annotation_id === draft.id) ? [{ id: draft.id, paragraph_id: draft.paragraph_id, annotation: undefined }] : [])];
  useEffect(() => {
    let stopped = false;
    if (view.content_revision) void api.getNoteHighlights(view).then(result => { if (!stopped) setHighlights(result.highlights); }, e => { if (!stopped) setError(errorMessage(e)); });
    return () => { stopped = true; };
  }, [api, view.material_id, view.kind, view.content_revision, view.content_generation]);
  useLayoutEffect(() => {
    const element = root.current;
    if (!element) return;
    const measure = () => {
      const isNarrow = element.clientWidth < 720;
      setNarrow(isNarrow);
      const top = element.getBoundingClientRect().top;
      let bottom = 0;
      const next: Record<string, number> = {};
      const ordered = cards.map(card => {
        const anchor = Array.from(element.querySelectorAll<HTMLElement>("[data-note-ids]")).find(n => n.dataset.noteIds?.split(" ").includes(card.id))
          ?? element.querySelector<HTMLElement>(`[data-note-target="${CSS.escape(card.paragraph_id ?? "")}"]`);
        return { ...card, top: anchor ? anchor.getBoundingClientRect().top - top : 0 };
      }).sort((a, b) => a.top - b.top);
      for (const card of ordered) {
        next[card.id] = Math.max(card.top, bottom);
        const height = isNarrow ? (element.querySelector<HTMLElement>(".note-comment-pin")?.offsetHeight ?? 32)
          : (cardRefs.current.get(card.id)?.offsetHeight ?? 160);
        bottom = next[card.id] + height + 12;
      }
      setPositions(previous => JSON.stringify(previous) === JSON.stringify(next) ? previous : next);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    for (const card of cardRefs.current.values()) observer.observe(card);
    return () => observer.disconnect();
  }, [view.annotations, draft, children]);
  const activate = (id: string, fromCard = false) => {
    setActive(id);
    if (fromCard) {
      const annotation = view.annotations.find(a => a.annotation_id === id);
      const anchor = Array.from(root.current?.querySelectorAll<HTMLElement>("[data-note-ids]") ?? []).find(n => n.dataset.noteIds?.split(" ").includes(id))
        ?? root.current?.querySelector<HTMLElement>(`[data-note-target="${CSS.escape(annotation?.paragraph_id ?? draft?.paragraph_id ?? "")}"]`);
      anchor?.scrollIntoView({ block: "center", behavior: "smooth" });
    } else if (narrow) drawer.current?.showModal();
    else cardRefs.current.get(id)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  };
  const capture = () => {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || !selection.rangeCount) { setSelected(null); return; }
    const range = selection.getRangeAt(0);
    if (!root.current?.contains(range.commonAncestorContainer)) { setSelected(null); return; }
    const ranges: NoteSelection[] = [];
    for (const span of root.current.querySelectorAll<HTMLElement>("[data-note-text]")) {
      if (!range.intersectsNode(span) || !span.firstChild) continue;
      const atomic = span.dataset.noteFormula === "true";
      const text = atomic ? span.dataset.noteSource! : span.textContent ?? "";
      const prefix = document.createRange(); prefix.setStart(span, 0);
      let from = 0, to = text.length;
      if (!atomic && span.contains(range.startContainer)) { prefix.setEnd(range.startContainer, range.startOffset); from = prefix.toString().length; }
      if (!atomic && span.contains(range.endContainer)) { prefix.setEnd(range.endContainer, range.endOffset); to = prefix.toString().length; }
      if (from >= to) continue;
      const start = Number(span.dataset.start) + Array.from(text.slice(0, from)).length;
      const end = Number(span.dataset.start) + Array.from(text.slice(0, to)).length;
      const path = JSON.parse(span.dataset.path!);
      const previous = ranges.at(-1);
      const quote = text.slice(from, to);
      if (previous && previous.paragraph_id === span.dataset.paragraph && JSON.stringify(previous.block_path) === span.dataset.path && previous.end === start) { previous.end = end; previous.quote += quote; }
      else ranges.push({ paragraph_id: span.dataset.paragraph!, block_path: path, start, end, quote });
    }
    setConceptMenu(false);
    setSelected(ranges.length ? { ranges, quote: ranges.map(r => r.quote).join("\n"), rect: range.getBoundingClientRect() } : null);
  };
  const selectFormula = (element: HTMLElement) => {
    window.getSelection()?.removeAllRanges();
    const quote = element.dataset.noteSource!;
    const start = Number(element.dataset.start);
    setConceptMenu(false);
    setSelected({ ranges: [{ paragraph_id: element.dataset.paragraph!, block_path: JSON.parse(element.dataset.path!), start, end: start + Array.from(quote).length, quote }], quote, rect: element.getBoundingClientRect(), formula: element });
  };
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    const changed = () => {
      clearTimeout(timer);
      const selection = window.getSelection();
      if (!selection || selection.isCollapsed || !root.current?.contains(selection.anchorNode)
        || toolbar.current?.contains(document.activeElement) || (document.activeElement instanceof HTMLTextAreaElement)) return;
      timer = setTimeout(capture, 120);
    };
    document.addEventListener("selectionchange", changed);
    return () => { clearTimeout(timer); document.removeEventListener("selectionchange", changed); };
  });
  useEffect(() => {
    if (!selected) return;
    const key = (event: KeyboardEvent) => {
      if (event.key === "Tab" && !event.shiftKey && !toolbar.current?.contains(document.activeElement)) { event.preventDefault(); toolbar.current?.querySelector<HTMLButtonElement>("button")?.focus(); }
      if (event.key === "Escape") setSelected(null);
    };
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node) && !toolbar.current?.contains(event.target as Node)) setSelected(null); };
    const scroll = () => setSelected(current => current?.formula
      ? { ...current, rect: current.formula.getBoundingClientRect() } : null);
    document.addEventListener("keydown", key); document.addEventListener("pointerdown", outside);
    root.current?.parentElement?.parentElement?.addEventListener("scroll", scroll);
    return () => { document.removeEventListener("keydown", key); document.removeEventListener("pointerdown", outside); root.current?.parentElement?.parentElement?.removeEventListener("scroll", scroll); };
  }, [selected]);
  const run = async (action: () => Promise<void>) => {
    setBusy(true); setError("");
    try { await action(); } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  };
  const mark = (style: HighlightStyle) => void run(async () => {
    for (const range of selected?.ranges ?? []) {
      const result = await api.addNoteHighlight(view, range, style);
      setHighlights(old => [...old, result]);
    }
  });
  const removeMarks = () => void run(async () => {
    const overlapping = highlights.filter(h => selected?.ranges.some(r => h.paragraph_id === r.paragraph_id && JSON.stringify(h.block_path) === JSON.stringify(r.block_path) && h.start! < r.end && h.end! > r.start));
    for (const highlight of overlapping) {
      await api.removeNoteHighlight(view, highlight.highlight_id);
      setHighlights(old => old.filter(h => h.highlight_id !== highlight.highlight_id));
    }
  });
  const save = () => draft && void run(async () => {
    saved(await api.saveNoteAnnotation(view, draft.id, draft.paragraph_id, draft.body, draft.selection));
    setDraft(null);
  });
  const remove = (a: NoteAnnotation) => void run(async () => { saved(await api.saveNoteAnnotation(view, a.annotation_id, a.paragraph_id, "")); setActive(null); });
  const card = (id: string, annotation?: NoteAnnotation) => <CommentCard key={id} annotation={annotation} draft={draft?.id === id ? draft : undefined} active={active === id} busy={busy}
    change={body => setDraft(d => d ? { ...d, body } : d)} save={() => void save()} cancel={() => { setDraft(null); if (narrow) drawer.current?.close(); }}
    edit={() => { if (annotation) setDraft({ id, paragraph_id: annotation.paragraph_id, body: annotation.body }); }} remove={() => annotation && remove(annotation)} activate={() => activate(id, true)}/>;
  const selectedParagraphs = view.paragraphs.filter(p => selected?.ranges.some(r => p.id === r.paragraph_id
    || view.sections.find(s => `section:${s.section_id}` === r.paragraph_id)?.paragraph_ids.includes(p.id)));
  const concepts = [...new Map(selectedParagraphs.flatMap(p => p.concepts).map(c => [c.concept_id, c])).values()];
  const orphaned = view.annotations.filter(a => a.paragraph_id === null);
  const earlier = highlights.filter(h => h.paragraph_id === null);
  return <Markup.Provider value={{ highlights, annotations: [...annotations, ...(draft?.selection ? [{ ...draft.selection, annotation_id: draft.id } as NoteAnnotation] : [])], active, activate, selectFormula }}>
    <div ref={root} className={`note-markup${cards.length ? " has-comments" : ""}${narrow ? " is-narrow" : ""}`} onMouseUp={e => { if ((e.target as HTMLElement).closest(".note-comment-card, .note-comment-pin")) return; capture(); }} onKeyUp={e => { if (e.key.startsWith("Arrow") && e.shiftKey) capture(); }}>
      <div className="note-markup-prose">{error && <p role="alert" className="form-error">{error}</p>}{children}
        {!!earlier.length && <section className="note-previous"><h2>先前的標記</h2><p>找不到唯一對應的位置，已保留原句與標記。</p>{earlier.map(h => <article key={h.highlight_id}><blockquote>{h.quote}</blockquote><p>{styles.find(s => s[0] === h.style)?.[1]}</p><details><summary>當時段落：{h.original_paragraph.heading}</summary>{renderPrevious(h.original_paragraph, h.original_revision)}</details><button type="button" className="text-button" disabled={busy} onClick={() => void run(async () => { await api.removeNoteHighlight(view, h.highlight_id); setHighlights(old => old.filter(x => x.highlight_id !== h.highlight_id)); })}>刪除標記</button></article>)}</section>}
        {!!orphaned.length && <section className="note-previous"><h2>先前的註記</h2><p>找不到唯一對應的位置，已保留當時內容。</p>{orphaned.map(a => <div key={a.annotation_id}><details><summary>當時段落：{a.original_paragraph.heading}</summary>{renderPrevious(a.original_paragraph, a.original_revision)}</details>{card(a.annotation_id, a)}</div>)}</section>}
      </div>
      {!narrow && <aside className="note-comment-lane" aria-label="註記邊欄" style={{ minHeight: Math.max(0, ...Object.values(positions)) + (cards.length ? 220 : 0) }}>{cards.map(c => <div key={c.id} ref={node => { if (node) cardRefs.current.set(c.id, node); else cardRefs.current.delete(c.id); }} style={{ top: positions[c.id] ?? 0 }}>{card(c.id, c.annotation)}</div>)}</aside>}
      {narrow && cards.map(c => <button key={c.id} className="note-comment-pin" type="button" style={{ top: positions[c.id] ?? 0 }} aria-label="開啟註記" onClick={() => activate(c.id)}><Icon name="chat" size={18}/></button>)}
    </div>
    <dialog ref={drawer} className="note-comments-drawer" aria-label="註記邊欄"><button type="button" className="text-button" onClick={() => drawer.current?.close()}>關閉註記</button>{narrow && cards.filter(c => c.id === active || c.id === draft?.id).map(c => card(c.id, c.annotation))}</dialog>
    {selected && createPortal(<div ref={toolbar} className="note-selection-toolbar" role="toolbar" aria-label="選取文字工具列" style={{ left: Math.max(8, Math.min(selected.rect.left, window.innerWidth - Math.min(600, window.innerWidth - 16) - 8)), top: window.innerWidth <= 720 ? Math.min(selected.rect.bottom + 8, window.innerHeight - 180) : Math.max(8, selected.rect.top - 100) }} onMouseDown={e => { e.preventDefault(); (e.target as HTMLElement).closest("button")?.focus({ preventScroll: true }); }}>
      <div className="note-selection-styles">{styles.map(([style, label]) => <button key={style} type="button" className={`note-style-${style}`} aria-label={`標記${label}`} title={label} disabled={busy} onClick={() => mark(style)}>{["yellow", "green", "blue", "pink"].includes(style) ? <span aria-hidden="true"/> : label}</button>)}<button type="button" disabled={busy} onClick={removeMarks}>移除標記</button></div>
      <div className="note-selection-actions"><button type="button" onClick={() => { window.dispatchEvent(new CustomEvent("studydy:ask-note", { detail: { materialId: view.material_id, text: selected.quote } })); setSelected(null); }}>問 AI BOT</button>
        <button type="button" disabled={selected.ranges.length !== 1} title={selected.ranges.length !== 1 ? "註記請選取同一段內的文字" : undefined} onClick={() => { const id = crypto.randomUUID(); setDraft({ id, paragraph_id: selected.ranges[0].paragraph_id, selection: selected.ranges[0], body: "" }); setActive(id); setSelected(null); if (narrow) drawer.current?.showModal(); }}>新增註記</button>
        <button type="button" disabled={!concepts.length} aria-expanded={conceptMenu} onClick={() => { if (concepts.length === 1) openConcept(concepts[0].concept_id); else setConceptMenu(v => !v); }}>在地圖查看</button>
        <button type="button" onClick={() => void run(async () => { const pages = [...new Set(selectedParagraphs.flatMap(p => p.evidence.map(e => e.page)))]; await navigator.clipboard.writeText(selected.quote + (pages.length ? `\n來源：第 ${pages.join("、")} 頁` : "")); })}>複製</button>
      </div>{selected.ranges.length !== 1 && <small>註記請選取同一段內的文字</small>}
      {conceptMenu && <div className="note-selection-concepts">{concepts.map(c => <button type="button" key={c.concept_id} onClick={() => openConcept(c.concept_id)}>{c.label}</button>)}</div>}
    </div>, document.body)}
  </Markup.Provider>;
}
