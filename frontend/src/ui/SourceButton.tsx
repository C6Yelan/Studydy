import { useEffect, useRef, useState } from "react";
import { errorMessage, type StudydyApiClient } from "../api/client";
import type { EvidenceSourceView, EvidenceView } from "../api/contracts";
import { openSource } from "./SourcePanel";
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
  return `${evidence.source_name ?? "教材"} · ${evidencePlace(evidence)}`;
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
  const [busy, setBusy] = useState(false);
  const opener = useRef<HTMLButtonElement>(null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  const load = async (): Promise<EvidenceSourceView | null> => {
    if (source) return source;
    if (busy) return null;
    setBusy(true);
    setError(null);
    try {
      const loaded = await apiClient.resolveEvidence(resolver, evidence.evidence_id);
      setSource(loaded);
      return loaded;
    } catch (e) {
      setError(errorMessage(e));
      return null;
    } finally {
      setBusy(false);
    }
  };
  // 原文改由全站共用的右側面板顯示（SourcePanel）。
  const open = async () => {
    const loaded = await load();
    if (!loaded || !mounted.current) return;
    const loadedPlace = loaded.playback ? `▶ ${clock(loaded.playback.start_seconds)}` : loaded.format === "images" ? loaded.label : `p.${loaded.normalized_page}`;
    openSource({ key: `${resolver}:${evidence.evidence_id}`, source: loaded, name: loaded.display_name ?? loaded.original_name,
      place: loadedPlace, quotes: evidence.references?.map(item => item.quote ?? "") ?? (reference ? [reference.quote] : []), opener: opener.current });
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
        onClick={() => void open()}
        aria-label={reference ? `${reference.number ? `來源 ${reference.number}：` : ""}${name} · ${place}` : undefined}
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
      </button>
      {error && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
    </>
  );
  return reference ? <span className="source-reference">{content}</span> : content;
}
