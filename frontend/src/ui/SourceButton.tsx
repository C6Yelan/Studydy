import { useEffect, useRef, useState } from "react";
import { errorMessage, type StudydyApiClient } from "../api/client";
import type { EvidenceSourceView, EvidenceView } from "../api/contracts";

export function sourceLinks(evidence: EvidenceView[]): EvidenceView[] {
  // 僅合併同教材同 PDF 頁的入口，保留代表 Evidence 供 resolver 回查；不修改 Evidence。
  const links = new Map<string, EvidenceView>();
  for (const item of evidence) {
    const key = JSON.stringify([
      item.source_id
        ? ["id", item.source_id]
        : item.source_name
          ? ["name", item.source_name]
          : ["evidence", item.evidence_id],
      item.normalized_page ?? item.page,
    ]);
    if (!links.has(key)) links.set(key, item);
  }
  return [...links.values()];
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
}: {
  apiClient: StudydyApiClient;
  resolver: string;
  evidence: Pick<EvidenceView, "evidence_id" | "page" | "normalized_page" | "source_name">;
  label?: string;
  /** 按鈕文字較短時，提示完整來源。 */
  title?: string;
}) {
  const [source, setSource] = useState<EvidenceSourceView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const opener = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (source) dialog.current?.showModal();
  }, [source]);
  const open = async () => {
    if (busy) return;
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
  return (
    <>
      <button
        ref={opener}
        className="text-button source-chip"
        type="button"
        disabled={busy}
        onClick={() => void open()}
        aria-haspopup="dialog"
        title={title ?? label}
      >
        {busy
          ? "正在讀取來源…"
          : label ?? (evidence.source_name
            ? evidenceSourceTitle(evidence)
            : `查看${evidencePlace(evidence)}來源`)}
      </button>
      {error && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      {source && (
        <dialog
          ref={dialog}
          className="source-dialog"
          aria-label="教材來源"
          onCancel={(event) => {
            event.preventDefault();
            event.stopPropagation();
            dialog.current?.close();
          }}
          onKeyDown={(event) => {
            if (event.key === "Escape") event.stopPropagation();
          }}
          onClose={() => {
            setSource(null);
            opener.current?.focus();
          }}
        >
          <h2>{source.original_name}</h2>
          <p>{source.label}</p>
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
        </dialog>
      )}
    </>
  );
}
