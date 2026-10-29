import { Progress } from "../../ui/Progress";
import { PodcastCancelControl } from "./PodcastCancelControl";
import type { PodcastView } from "../../api/contracts";
import { FlowHeader, LearningSteps, podcastSteps } from "../material-flow/Flow";
import { ProcessingTimeline } from "../material-flow/ProcessingTimeline";
import { materialElapsedLabel } from "../material-flow/material-flow";
import type { IconName } from "../../ui/Icon";

const stepInfo = [
  { step: "script", label: "寫講稿", icon: "edit" },
  { step: "audio", label: "配音", icon: "microphone" },
  { step: "video", label: "製作影片", icon: "monitor" },
  { step: "alignment", label: "對齊字幕", icon: "file" },
] as const satisfies readonly { step: string; label: string; icon: IconName }[];

const finished = (status: string) => status === "completed" || status === "skipped";

/** 版面比照上傳教材的處理頁：左側是整份 Podcast 的進度，右側是目前這一集的製作步驟。 */
export function PodcastProgress({ view, busy, onAction }: {
  view: PodcastView; busy: boolean; onAction: (action: "retry" | "cancel") => void;
}) {
  const steps = view.generation?.steps ?? [];
  const paused = view.status === "failed" || view.status === "cancelled";
  const retryable = paused || view.generation?.retryable_videos;
  const total = view.episode_count;
  const episodes = Array.from({ length: total }, (_, index) => steps.filter(step => step.episode_index === index));
  // 對齊字幕要等全部音訊完成才統一進行，判斷「這集做完」與「目前這集」時不算它。
  const produced = (own: typeof steps) => own.length > 0 && own.every(step => step.step === "alignment" || finished(step.status));
  const doneEpisodes = episodes.filter(produced).length;
  const pending = episodes.findIndex(own => !produced(own));
  const episode = pending >= 0 ? pending : episodes.findIndex(own => !own.every(step => finished(step.status)));
  const percent = steps.length ? Math.floor(steps.filter(step => finished(step.status)).length / steps.length * 100) : 0;
  const stepText = { completed: "已完成", skipped: "已略過", running: "進行中", queued: "排隊中", failed: "失敗", waiting: "尚未開始" } as const;
  const timeline = episode < 0 ? [] : stepInfo.flatMap(info => episodes[episode].filter(step => step.step === info.step).map(step => ({
    label: info.label, icon: info.icon,
    state: finished(step.status) ? "complete" as const : step.status === "waiting" ? "waiting" as const : "active" as const,
    status: paused && !finished(step.status) && step.status !== "failed" ? "已停止" : stepText[step.status] })));
  // API 沒有 updated_at；以最近一個步驟的開始時間當作最近更新。
  const lastUpdate = steps.map(step => step.started_at).filter((at): at is string => !!at).sort().at(-1);
  return <section className="flow-page processing-page task-page is-processing" aria-label="Podcast 生成進度">
    <FlowHeader title={view.status === "cancelled" ? "已取消 Podcast 生成" : paused ? "Podcast 生成中斷" : "正在生成 Podcast"}
      description={paused ? "已完成的部分會保留，接續生成會從未完成的部分開始。" : "Studydy 會一集一集寫講稿、配音，全部完成後對齊字幕並開啟預覽。"} />
    <LearningSteps labels={podcastSteps} current={2} label="建立 Podcast 步驟" />
    <div className="processing-grid">
      <section className="surface processing-card">
        <div className="processing-status" aria-live="polite">
          <div className="progress-heading overall-heading"><h2>整體進度</h2><strong>{percent}%</strong></div>
          <Progress max={100} value={percent} tone={paused ? "warning" : "default"} aria-label={`整體進度 ${percent}%`} />
          <p className="progress-estimate-note">已完成 {doneEpisodes} / {total} 集；全部完成後對齊字幕並開啟預覽。</p>
          {!!view.generation?.failed_videos && <p role="alert">{view.generation.failed_videos} 集影片生成失敗；接續生成會保留已完成的影音。</p>}
          {view.error_code === "PODCAST_STORAGE_FAILED" && <p role="alert">這一集未能保存，請重試。已完成的部分會保留。</p>}
          {view.error_code === "PODCAST_SCRIPT_NEEDS_REVIEW" && <p role="alert">本集大部分重點無法核對原文，因此尚未發布音訊。可接續生成重試。</p>}
        </div>
        {/* 沒有任何步驟時間時，中斷頁無法得知停止時間，不顯示時間格。 */}
        {(!paused || lastUpdate) && <dl className="processing-times">
          <div><dt>已耗時</dt><dd>{materialElapsedLabel(view.created_at, paused && lastUpdate ? Date.parse(lastUpdate) : Date.now())}</dd></div>
          <div><dt>最近更新</dt><dd>{lastUpdate ? <time dateTime={lastUpdate}>{new Date(lastUpdate).toLocaleTimeString("zh-TW")}</time> : "—"}</dd></div>
        </dl>}
        <p className="processing-leave-note">{paused ? "已完成的進度會保留，接續生成會從未完成的部分開始。" : "進度會自動保存，可稍後從「我的 Podcast」返回查看。"}</p>
        <div className="processing-cancel">{retryable && <button type="button" className="secondary-button" disabled={busy} onClick={() => onAction("retry")}>接續生成</button>}<PodcastCancelControl busy={busy} onConfirm={() => onAction("cancel")} /></div>
      </section>
      <ProcessingTimeline title={episode >= 0 ? `第 ${episode + 1} / ${total} 集` : "處理流程"} stages={timeline} currentIndex={-1} />
    </div>
  </section>;
}
