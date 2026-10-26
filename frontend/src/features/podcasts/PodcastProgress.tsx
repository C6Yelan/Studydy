import { lunaCallLimitMessage } from "../../api/client";
import { PodcastCancelControl } from "./PodcastCancelControl";
import type { PodcastView } from "../../api/contracts";
import { ProcessingTimeline } from "../material-flow/ProcessingTimeline";
import { podcastStatus } from "./PodcastLibrary";

export function PodcastProgress({ view, busy, onAction }: {
  view: PodcastView; busy: boolean; onAction: (action: "retry" | "cancel") => void;
}) {
  const current = view.episodes.findIndex(episode => !episode.audio);
  const episode = view.episodes[current];
  const paused = view.status === "failed" || view.status === "cancelled";
  const retryable = paused || view.generation?.retryable_videos;
  // 影片分母只算預期會有影片的集數；沒有影片的 Podcast 只有兩個階段，不顯示影片集數。
  const expectedVideos = view.generation?.expected_videos ?? (view.video_enabled ? view.episode_count : 0);
  const withVideo = expectedVideos > 0;
  const completedVideos = withVideo ? view.generation?.completed_videos ?? 0 : 0;
  const stage = !episode ? (withVideo ? 2 : 1) : episode.script ? 1 : 0;
  const stages = [{ label: "整理內容", icon: "book" as const }, { label: "製作音訊", icon: "headphones" as const },
    ...(withVideo ? [{ label: "製作影片", icon: "check" as const }] : [])];
  const total = view.episode_count + expectedVideos;
  const activity = paused || (retryable && !view.generation?.active) ? "已中斷" : view.status === "pending" ? "排隊中" : "處理中";
  // 只計已完成集數；API 沒有單集內部百分比，不把模型生成時間當成可量測進度。
  const completed = view.completed_episodes + completedVideos;
  const percent = Math.floor(completed / total * 100);
  return <section className="is-processing" aria-label="Podcast 生成進度">
    <div className="processing-grid">
      <section className="surface processing-card">
        <div className="processing-status" aria-live="polite">
          <div className="progress-heading overall-heading"><h2>{withVideo ? "影音生成進度" : "音訊生成進度"}</h2><strong>{percent}%</strong></div>
          <progress className="processing-progress" value={completed} max={total} aria-label={withVideo ? "已完成影音集數" : "已完成音訊集數"} />
          <p className="progress-estimate-note">音訊 {view.completed_episodes} / {view.episode_count} 集{withVideo ? ` · 影片 ${completedVideos} / ${expectedVideos} 集` : ""}；依完成數計算，不代表剩餘時間。</p>
          {view.generation?.error_code === "LUNA_CALL_LIMIT_EXCEEDED" ? <p role="alert">{lunaCallLimitMessage}</p> : !!view.generation?.failed_videos && <p role="alert">{view.generation.failed_videos} 集影片生成失敗；接續生成會保留已完成的影音。</p>}
          <section className="processing-current"><h3>{paused ? podcastStatus[view.status] : "目前狀態"}</h3>
            <div className="progress-heading"><strong className="stage-label stage-status-label">{!paused && <span className="processing-status-indicator" aria-hidden="true" />}{stages[stage].label}</strong><strong>{activity}</strong></div>
            <p role="status">{current >= 0 ? `第 ${current + 1} / ${view.episode_count} 集 · ${paused ? "停在" : view.status === "pending" ? "接下來：" : ""}${stages[stage].label}` : view.generation?.active ? "音訊已完成，正在製作影片。" : "影片尚未全部完成，請接續生成。"}</p>
            {view.error_code === "LUNA_CALL_LIMIT_EXCEEDED" && <p role="alert">{lunaCallLimitMessage}</p>}
            {view.error_code === "PODCAST_STORAGE_FAILED" && <p role="alert">這一集未能保存，請重試。已完成的部分會保留。</p>}
            {view.error_code === "PODCAST_SCRIPT_NEEDS_REVIEW" && <p role="alert">本集內容的來源或教學品質核對未通過，請重試。尚未發布音訊。</p>}
          </section>
        </div>
        <p className="processing-leave-note">{paused ? "已完成的進度會保留，接續生成會從未完成的部分開始。" : `可稍後從「我的 Podcast」返回查看。全部${withVideo ? "音訊與影片" : "音訊"}完成後才會開啟預覽。`}</p>
        <div className="processing-cancel">{retryable && <button type="button" className="secondary-button" disabled={busy} onClick={()=>onAction('retry')}>接續生成</button>}<PodcastCancelControl busy={busy} onConfirm={()=>onAction('cancel')}/></div>
      </section>
      <ProcessingTimeline stages={stages} currentIndex={stage} activity={activity} />
    </div>
  </section>;
}
