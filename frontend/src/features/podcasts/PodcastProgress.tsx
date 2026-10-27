import { Progress } from "../../ui/Progress";
import { PodcastCancelControl } from "./PodcastCancelControl";
import type { PodcastView } from "../../api/contracts";
import { podcastStatus } from "./PodcastLibrary";

const stepLabels = {script:"寫講稿", audio:"配音", alignment:"對齊字幕", video:"製作影片"};
const stepStates = {waiting:"尚未開始", queued:"排隊中", running:"進行中", completed:"已完成", failed:"未完成", skipped:"已略過"};

export function PodcastProgress({ view, busy, onAction }: {
  view: PodcastView; busy: boolean; onAction: (action: "retry" | "cancel") => void;
}) {
  const steps = view.generation?.steps ?? [];
  const current = steps.find(step => step.status === "running") ?? steps.find(step => step.status === "queued") ?? steps.find(step => step.status === "failed");
  const paused = view.status === "failed" || view.status === "cancelled";
  const retryable = paused || view.generation?.retryable_videos;
  // 影片分母只算預期會有影片的集數；沒有影片的 Podcast 不顯示影片集數。
  const expectedVideos = view.generation?.expected_videos ?? (view.video_enabled ? view.episode_count : 0);
  const withVideo = expectedVideos > 0;
  const completedVideos = withVideo ? view.generation?.completed_videos ?? 0 : 0;
  const total = view.episode_count + expectedVideos;
  // 只計已完成集數；API 沒有單集內部百分比，不把模型生成時間當成可量測進度。
  const completed = view.completed_episodes + completedVideos;
  const measurable = view.episode_count > 1 && completed > 0;
  const percent = Math.floor(completed / total * 100);
  return <section className="is-processing" aria-label="Podcast 生成進度">
    <div className="processing-grid">
      <section className="surface processing-card">
        <div className="processing-status" aria-live="polite">
          <div className="progress-heading overall-heading"><h2>{withVideo ? "影音生成進度" : "音訊生成進度"}</h2>{measurable && <strong>{percent}%</strong>}</div>
          <Progress value={measurable ? completed : undefined} max={total} aria-label={withVideo ? "已完成影音集數" : "已完成音訊集數"} />
          <p className="progress-estimate-note">音訊 {view.completed_episodes} / {view.episode_count} 集{withVideo ? ` · 影片 ${completedVideos} / ${expectedVideos} 集` : ""}{measurable && "；依完成數計算，不代表剩餘時間。"}</p>
          {!!view.generation?.failed_videos && <p role="alert">{view.generation.failed_videos} 集影片生成失敗；接續生成會保留已完成的影音。</p>}
          <section className="processing-current"><h3>{paused ? podcastStatus[view.status] : "目前狀態"}</h3>
            <p role="status">{current ? `第 ${current.episode_index + 1} / ${view.episode_count} 集 · ${stepLabels[current.step]} · ${stepStates[current.status]}` : paused ? podcastStatus[view.status] : "正在準備內容…"}</p>
            {view.error_code === "PODCAST_STORAGE_FAILED" && <p role="alert">這一集未能保存，請重試。已完成的部分會保留。</p>}
            {view.error_code === "PODCAST_SCRIPT_NEEDS_REVIEW" && <p role="alert">本集大部分重點無法核對原文，因此尚未發布音訊。可接續生成重試。</p>}
          </section>
        </div>
        <p className="processing-leave-note">{paused ? "已完成的進度會保留，接續生成會從未完成的部分開始。" : `可稍後從「我的 Podcast」返回查看。全部${withVideo ? "音訊與影片" : "音訊"}完成後才會開啟預覽。`}</p>
        <div className="processing-cancel">{retryable && <button type="button" className="secondary-button" disabled={busy} onClick={()=>onAction('retry')}>接續生成</button>}<PodcastCancelControl busy={busy} onConfirm={()=>onAction('cancel')}/></div>
      </section>
      <section className="surface processing-card podcast-generation-steps" aria-label="各集製作步驟">
        <h2>製作步驟</h2>
        {view.episodes.map((_, index) => <section key={index} className="podcast-generation-episode">
          {view.episode_count > 1 && <h3>第 {index + 1} 集</h3>}
          <ol>{steps.filter(step => step.episode_index === index).map(step => <li key={step.step} data-status={step.status}>
            <strong>{step.status === 'completed' && <span aria-hidden="true">✓ </span>}{stepLabels[step.step]}</strong>
            <span>{step.status === 'queued' && step.queue_ahead !== null ? `排隊中，前面還有 ${step.queue_ahead} 個` : stepStates[step.status]}</span>
            {step.elapsed_seconds !== null && <small>已耗時 {Math.floor(step.elapsed_seconds / 60)} 分 {Math.floor(step.elapsed_seconds % 60)} 秒</small>}
          </li>)}</ol>
        </section>)}
        {!steps.length && <p>正在讀取製作步驟…</p>}
      </section>
    </div>
  </section>;
}
