import { useEffect, useState } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { MaterialNotesView, NoteKind } from "../../api/contracts";
import { BackLink } from "../../ui/BackLink";
import { Progress } from "../../ui/Progress";
import { FlowHeader, LearningSteps } from "../material-flow/Flow";
import { ProcessingTimeline } from "../material-flow/ProcessingTimeline";
import { materialElapsedLabel } from "../material-flow/material-flow";
import type { IconName } from "../../ui/Icon";

const noteSteps = ["分析教材", "生成筆記", "閱讀筆記"];
const stages: { label: string; icon: IconName }[] = [
  { label: "規劃分批", icon: "route" }, { label: "逐批撰寫", icon: "edit" }, { label: "逐段核對", icon: "search" },
  { label: "整理章節", icon: "book" }, { label: "發布", icon: "check" },
];
const kindLabels: Record<NoteKind, string> = { summary: "重點整理", full: "完整筆記" };

/** 筆記生成或更新的進度頁，版面比照上傳教材處理頁；生成中舊稿仍可從「返回筆記」閱讀。 */
export function NotesProgress({ api, materialId, revision, kind, close }: {
  api: StudydyApiClient; materialId: string; revision: string; kind: NoteKind; close: () => void;
}) {
  const [view, setView] = useState<MaterialNotesView | null>(null);
  const [error, setError] = useState("");
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const result = await api.getNotes(materialId, revision, kind);
        if (cancelled) return;
        setView(result); setError("");
        if (result.status === "pending" || result.status === "running") timer = setTimeout(poll, 2000);
        else close();
      } catch (e) { if (!cancelled) { setError(errorMessage(e)); timer = setTimeout(poll, 5000); } }
    };
    void poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [api, materialId, revision, kind, close]);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const progress = view?.progress ?? null;
  const current = !progress || view?.status === "pending" ? 0 : progress.stage === "generating" ? 1 : progress.stage === "validating" ? 2 : 4;
  const total = progress?.total_parts ?? 0;
  const percent = progress && total > 0 ? Math.min(99, Math.floor(progress.completed_parts / total * 100)) : 0;
  const updating = !!view?.paragraphs.length;
  const timeline = stages.map((stage, index) => ({ ...stage,
    state: index < current ? "complete" as const : index === current ? "active" as const : "waiting" as const,
    status: index === 0 && view?.status === "pending" ? "排隊中"
      : index === 1 && index === current && progress ? `已完成 ${progress.completed_parts} / ${total} 批${progress.current_section ? `・${progress.current_section}` : ""}` : undefined }));
  return <div className="notes-progress-scroll"><section className="flow-page processing-page task-page is-processing" aria-label="筆記生成進度">
    <FlowHeader back={<BackLink label="返回筆記" onClick={close} />}
      title={updating ? `正在更新${kindLabels[kind]}` : `正在生成${kindLabels[kind]}`}
      description={updating ? "舊的筆記仍可閱讀，完成後會自動替換。" : "Studydy 會分批撰寫並逐段核對原文，完成後開啟筆記。"} />
    <LearningSteps labels={noteSteps} current={1} label="建立筆記步驟" />
    <div className="processing-grid">
      <section className="surface processing-card">
        <div className="processing-status" aria-live="polite">
          <div className="progress-heading overall-heading"><h2>整體進度</h2><strong>{percent}%</strong></div>
          <Progress max={100} value={percent} aria-label={`整體進度 ${percent}%`} />
          <p className="progress-estimate-note">{progress ? `已完成 ${progress.completed_parts} / ${total} 批。` : "正在規劃分批。"}完成後自動開啟筆記。</p>
          {error && <p role="alert">{error}</p>}
        </div>
        <dl className="processing-times">
          <div><dt>已耗時</dt><dd>{view?.started_at ? materialElapsedLabel(view.started_at, now) : "—"}</dd></div>
          <div><dt>最近更新</dt><dd>{view?.updated_at ? <time dateTime={view.updated_at}>{new Date(view.updated_at).toLocaleTimeString("zh-TW")}</time> : "—"}</dd></div>
        </dl>
        <p className="processing-leave-note">進度會自動保存，可先返回閱讀，稍後再回來查看。</p>
      </section>
      <ProcessingTimeline title={kindLabels[kind]} stages={timeline} currentIndex={current} />
    </div>
  </section></div>;
}
