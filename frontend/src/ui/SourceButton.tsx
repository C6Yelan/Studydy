import { createPortal } from "react-dom";
import { useEffect, useId, useRef, useState } from "react";
import { errorMessage, type StudydyApiClient } from "../api/client";
import type { EvidenceSourceView, EvidenceView } from "../api/contracts";
import "./source-reference.css";

import { citationNumberRanges, sourceLinks } from "./source-links";
export { sourceLinks } from "./source-links";

export function AnswerSourceList({ apiClient, resolver, citations }: {
  apiClient: StudydyApiClient;
  resolver: string;
  citations: { evidence: EvidenceView[] }[];
}) {
  return sourceLinks(citations.flatMap(citation => citation.evidence)).map(evidence => {
    const numbers = citations.flatMap((citation, index) => citation.evidence.some(item =>
      evidence.references.some(reference => reference.evidence_id === item.evidence_id)) ? [index + 1] : []);
    return <div className="answer-source-row" key={evidence.evidence_id}><SourceButton apiClient={apiClient} resolver={resolver}
      evidence={evidence} reference={{number: numbers[0], numbers, list: true, quote: [...new Set(evidence.references.map(item => item.quote))].join("\n…\n")}} /></div>;
  });
}

const timedSource = /\.(mp3|m4a|wav|mp4|youtube)$/i;

/** 音訊、影片與 YouTube 的「頁」是約 2 分鐘的逐字稿時間段，畫面稱為「段」。 */
export function evidencePlace(
  evidence: Pick<EvidenceView, "page" | "normalized_page" | "source_name">,
): string {
  const page = evidence.normalized_page ?? evidence.page;
  return evidence.source_name && timedSource.test(evidence.source_name)
    ? `第 ${page} 段`
    : `第 ${page} 頁`;
}

/** 完整來源名稱：文件標「PDF 第 N 頁」，音訊／影片／YouTube 標「第 N 段」。 */
export function evidenceSourceTitle(
  evidence: Pick<EvidenceView, "page" | "normalized_page" | "source_name">,
): string {
  const timed = !!evidence.source_name && timedSource.test(evidence.source_name);
  return `${evidence.source_name ?? "教材"} · ${timed ? "" : "PDF "}${evidencePlace(evidence)}`;
}

function clock(seconds: number): string {
  const whole = Math.floor(seconds);
  const hours = Math.floor(whole / 3600);
  const rest = `${String(Math.floor((whole % 3600) / 60)).padStart(2, "0")}:${String(whole % 60).padStart(2, "0")}`;
  return hours ? `${hours}:${rest}` : rest;
}

export function SourceButton({
  apiClient,
  resolver,
  evidence,
  label,
  title,
  reference,
}: {
  apiClient: StudydyApiClient;
  resolver: string;
  evidence: Pick<EvidenceView, "evidence_id" | "page" | "normalized_page" | "source_name"> & { citation_count?: number; references?: {evidence_id: string; quote?: string}[] };
  label?: string;
  /** 按鈕文字較短時，提示完整來源。 */
  title?: string;
  reference?: { number?: number; numbers?: number[]; list?: boolean; quote: string };
}) {
  const [source, setSource] = useState<EvidenceSourceView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [opened, setOpened] = useState(false);
  const [panelTop, setPanelTop] = useState(16);
  const [preview, setPreview] = useState<{left:number;top:number}|null>(null);
  const previewAt = () => { const box = opener.current?.getBoundingClientRect(); if (box) setPreview({left:Math.max(8, Math.min(box.left, window.innerWidth - Math.min(352, window.innerWidth * .75) - 8)),top:Math.min(box.bottom + 8, window.innerHeight - 240)}); };
  const previewId = useId();
  const previewTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const closePreview = () => { previewTimer.current = setTimeout(() => setPreview(null), 150); };
  const keepPreview = () => clearTimeout(previewTimer.current);
  useEffect(() => () => clearTimeout(previewTimer.current), []);
  const [busy, setBusy] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const opener = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (opened && source) {
      if (reference) {
        const position = () => setPanelTop(Math.max(8, (document.querySelector(".material-header")?.getBoundingClientRect().bottom ?? 8) + 8));
        position();
        dialog.current?.show();
        dialog.current?.querySelector<HTMLButtonElement>("button")?.focus();
        window.addEventListener("resize", position);
        window.addEventListener("scroll", position, { passive: true });
        return () => { window.removeEventListener("resize", position); window.removeEventListener("scroll", position); };
      }
      dialog.current?.showModal();
    }
  }, [source, opened]);
  const load = async () => {
    if (busy || source) return;
    setBusy(true);
    setError(null);
    try {
      setSource(await apiClient.resolveEvidence(resolver, evidence.evidence_id));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => { if (reference) void load(); }, [resolver, evidence.evidence_id]);
  const place = source?.playback ? `▶ ${clock(source.playback.start_seconds)}` : source?.format === "images" ? source.label : source ? `p.${source.normalized_page}` : "來源";
  const name = source?.display_name ?? source?.original_name ?? evidence.source_name ?? "教材";
  const mergedReference = reference?.list && (evidence.citation_count ?? 0) > 1;
  const content = (
    <>
      <button
        ref={opener}
        className={reference ? `text-button source-reference-button${reference.list ? ` source-reference-list${mergedReference ? " source-reference-merged" : ""}` : ""}` : "text-button source-chip"}
        type="button"
        disabled={busy}
        onFocus={() => { if (reference) previewAt(); }}
        onBlur={closePreview}
        onKeyDown={event => { if (event.key === "Escape" && preview) { event.stopPropagation(); setPreview(null); } }}
        onClick={() => { setPreview(null); setOpened(true); void load(); }}
        aria-describedby={reference && preview ? previewId : undefined}
        aria-label={reference ? `${reference.number ? `來源 ${reference.number}：` : ""}${name} · ${place}` : undefined}
        aria-haspopup="dialog"
        title={title ?? label}
      >
        {reference ? (reference.list ? mergedReference ? <>
          <span className="source-reference-fixed">{source?.playback ? "▶" : source?.format === "images" ? "▧" : "▤"} {reference.number ? `${citationNumberRanges(reference.numbers ?? [reference.number])}. ` : ""}</span>
          <span className="source-reference-filename">{name}</span>
          <span className="source-reference-fixed"> · {place}</span>
        </> : <>{source?.playback ? "▶" : source?.format === "images" ? "▧" : "▤"} {reference.number ? `${citationNumberRanges(reference.numbers ?? [reference.number])}. ` : ""}{name} · {place}</> : <sup>[{reference.number ?? place}]</sup>) : busy
          ? "正在讀取來源…"
          : label ?? (evidence.source_name
            ? evidenceSourceTitle(evidence)
            : `查看${evidencePlace(evidence)}來源`)}
        {evidence.citation_count != null && evidence.citation_count > 1 && <span className={mergedReference ? "source-reference-fixed" : undefined}> · 引用 {evidence.citation_count} 次</span>}
      </button>
      {reference && preview && createPortal(<span onMouseEnter={keepPreview} onMouseLeave={closePreview} style={preview} id={previewId} role="tooltip" className="source-reference-preview"><strong>{name} · {place}</strong><span>{reference.quote}</span>{source?.format === "images" && <iframe src={source.preview_url} title={`${name}圖片預覽`} tabIndex={-1}/>}</span>, document.body)}
      {error && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      {source && opened && createPortal(
        <dialog
          ref={dialog}
          className={reference ? "source-reference-panel" : "source-dialog"}
          style={reference ? { top: panelTop, maxHeight: `calc(100dvh - ${panelTop}px - 1rem)` } : undefined}
          aria-label="教材來源"
          onCancel={(event) => {
            event.preventDefault();
            event.stopPropagation();
            dialog.current?.close();
          }}
          onKeyDown={(event) => {
            if (event.key === "Escape") { event.stopPropagation(); if (reference) dialog.current?.close(); }
          }}
          onClose={() => {
            setOpened(false);
            if (!reference) setSource(null);
            opener.current?.focus();
          }}
        >
          <h2>{source.display_name ?? source.original_name}</h2>
          <p>{source.label}</p>
          {evidence.references && <blockquote>{evidence.references.map((item, index) =>
            <p key={`${item.evidence_id}:${index}`}>{item.quote}</p>)}</blockquote>}
          {source.accuracy !== "exact" && (
            <p>
              原始文件的位置{source.accuracy === "ambiguous" ? "可能對應多處" : "無法精確對應"}
              ，請以轉換後 PDF 回查。
            </p>
          )}
          {source.playback ? (
            <>
              <p>逐字稿由本機語音辨識或影片上傳者的字幕產生，可能有誤，請以原音回查。</p>
              {source.playback.kind === "audio" ? (
                <audio
                  className="source-media"
                  controls
                  autoPlay
                  preload="metadata"
                  src={`${source.playback.url}#t=${Math.floor(source.playback.start_seconds)}`}
                  aria-label={`從 ${clock(source.playback.start_seconds)} 播放原音`}
                />
              ) : (
                <iframe
                  className="source-media source-youtube"
                  src={`https://www.youtube-nocookie.com/embed/${source.playback.video_id}?start=${Math.floor(source.playback.start_seconds)}&autoplay=1&rel=0`}
                  title={`YouTube 影片，從 ${clock(source.playback.start_seconds)} 播放`}
                  allow="autoplay; encrypted-media; picture-in-picture"
                  allowFullScreen
                  referrerPolicy="strict-origin-when-cross-origin"
                />
              )}
            </>
          ) : source.format === "images" ? (
            <p>圖片中的文字由系統辨識，辨識品質不保證。</p>
          ) : (
            source.format !== "pdf" && <p>此 PDF 由系統自動轉換，轉換品質不保證。</p>
          )}
          <div className="state-actions">
            <a
              className={source.playback ? "secondary-button" : "primary-button"}
              href={source.preview_url}
              target="_blank"
              rel="noopener noreferrer"
            >
              {source.playback ? "開啟逐字稿頁" : source.format === "images" ? "開啟圖片頁" : "開啟 PDF 來源頁"}
            </a>
            {source.playback?.kind === "youtube" ? (
              <a
                className="secondary-button"
                href={`https://www.youtube.com/watch?v=${source.playback.video_id}&t=${Math.floor(source.playback.start_seconds)}s`}
                target="_blank"
                rel="noopener noreferrer"
              >
                在 YouTube 開啟
              </a>
            ) : (
              <a
                className="secondary-button"
                href={source.original_url}
                target="_blank"
                rel="noopener noreferrer"
              >
                {source.format === "video" ? "下載音軌" : "下載原檔"}
              </a>
            )}
            <button
              className="secondary-button"
              type="button"
              onClick={() => dialog.current?.close()}
            >
              關閉
            </button>
          </div>
        </dialog>, document.body
      )}
    </>
  );
  return reference ? <span className="source-reference" onMouseEnter={() => { keepPreview(); previewAt(); }} onMouseLeave={closePreview}>{content}</span> : content;
}
