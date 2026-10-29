import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import type { EvidenceSourceView } from "../api/contracts";

/**
 * 全站共用的右側原文面板，外觀與操作比照知識地圖的概念詳情：
 * 點任何來源頁碼都在同一個面板顯示（後點的取代先點的），正文讓出右側空間。
 */
export type SourceEntry = { key: string; source: EvidenceSourceView; name: string; place: string; quotes: string[]; opener: HTMLElement | null };

let current: SourceEntry | null = null;
const listeners = new Set<() => void>();
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
const read = () => current;
const update = (next: SourceEntry | null) => {
  current = next;
  document.documentElement.classList.toggle("source-panel-open", next !== null);
  listeners.forEach(listener => listener());
};

export function openSource(entry: SourceEntry) { update(entry); }

function close() {
  const opener = current?.opener;
  update(null);
  opener?.focus();
}

// 轉換出的原文常有只剩箭頭或標點的行，顯示時略過。
const meaningful = (line: string) => /[\p{L}\p{N}]/u.test(line);

function clock(seconds: number): string {
  const whole = Math.floor(seconds);
  const hours = Math.floor(whole / 3600);
  const rest = `${String(Math.floor((whole % 3600) / 60)).padStart(2, "0")}:${String(whole % 60).padStart(2, "0")}`;
  return hours ? `${hours}:${rest}` : rest;
}

export function SourcePanelHost({ routeKey }: { routeKey: string }) {
  const entry = useSyncExternalStore(subscribe, read);
  const heading = useRef<HTMLHeadingElement>(null);
  const [top, setTop] = useState(16);
  // 換頁時收起面板，避免帶著上一頁的原文。
  useEffect(() => () => update(null), [routeKey]);
  useEffect(() => { heading.current?.focus(); }, [entry?.key]);
  // 面板頂端對齊頁首下方，與地圖詳情面板同一條上緣。
  useLayoutEffect(() => {
    if (!entry) return;
    const position = () => setTop(Math.max(16, ((document.querySelector(".material-header") ?? document.querySelector(".app-header"))?.getBoundingClientRect().bottom ?? 0) + 12));
    position();
    window.addEventListener("resize", position);
    window.addEventListener("scroll", position, { passive: true });
    return () => { window.removeEventListener("resize", position); window.removeEventListener("scroll", position); };
  }, [entry]);
  if (!entry) return null;
  const { source } = entry;
  const lines = entry.quotes.flatMap(quote => quote.split("\n")).map(line => line.trim()).filter(meaningful);
  const note = source.playback ? "逐字稿由本機語音辨識或影片上傳者的字幕產生，可能有誤，請以原音回查。"
    : source.format === "images" ? "圖片中的文字由系統辨識，辨識品質不保證。"
    : source.format !== "pdf" ? "此 PDF 由系統自動轉換，轉換品質不保證。" : null;
  return createPortal(<aside className="source-side-panel" style={{ top }} role="dialog" aria-modal="false" aria-label="教材來源"
    onKeyDown={event => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); } }}>
    <header>
      <div>
        <span className="source-side-kicker">教材來源 · {entry.name}</span>
        <h2 ref={heading} tabIndex={-1}>{source.label}</h2>
      </div>
      <button type="button" className="source-side-close" aria-label="關閉" onClick={close}>×</button>
    </header>
    {lines.length > 0 && <section>
      <h3>引用原文</h3>
      <blockquote className="source-side-quote">{lines.map((line, index) => <p key={index}>{line}</p>)}</blockquote>
    </section>}
    {source.accuracy !== "exact" && <p className="source-side-note">原始文件的位置{source.accuracy === "ambiguous" ? "可能對應多處" : "無法精確對應"}，請以轉換後 PDF 回查。</p>}
    {source.playback && <section>
      <h3>原音</h3>
      {source.playback.kind === "audio"
        ? <audio className="source-media" controls preload="metadata" src={`${source.playback.url}#t=${Math.floor(source.playback.start_seconds)}`} aria-label={`從 ${clock(source.playback.start_seconds)} 播放原音`} />
        : <iframe className="source-media source-youtube" src={`https://www.youtube-nocookie.com/embed/${source.playback.video_id}?start=${Math.floor(source.playback.start_seconds)}&rel=0`}
          title={`YouTube 影片，從 ${clock(source.playback.start_seconds)} 播放`} allow="encrypted-media; picture-in-picture" allowFullScreen referrerPolicy="strict-origin-when-cross-origin" />}
    </section>}
    <div className="source-side-actions">
      <a className="primary-button" href={source.preview_url} target="_blank" rel="noopener noreferrer">{source.playback ? "開啟逐字稿頁" : source.format === "images" ? "開啟圖片頁" : "開啟 PDF 來源頁"}</a>
      {source.playback?.kind === "youtube"
        ? <a className="text-button" href={`https://www.youtube.com/watch?v=${source.playback.video_id}&t=${Math.floor(source.playback.start_seconds)}s`} target="_blank" rel="noopener noreferrer">在 YouTube 開啟</a>
        : <a className="text-button" href={source.original_url} target="_blank" rel="noopener noreferrer">{source.format === "video" ? "下載音軌" : "下載原檔"}</a>}
    </div>
    {note && <p className="source-side-note">{note}</p>}
  </aside>, entry.opener?.closest("dialog:modal") ?? document.body);
}
