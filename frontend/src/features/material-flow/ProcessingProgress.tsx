import type { MaterialProcessingRunView } from "../../api/contracts";
import { materialCurrentStagePercent, materialOverallProgressPercent, materialProgressStageLabel, materialElapsedLabel } from "./material-flow";

export function ProcessingProgress({ run, now, showSources = true }: { run: MaterialProcessingRunView; now: number; showSources?: boolean }) {
  const currentPercent = materialCurrentStagePercent(run);
  const overallPercent = materialOverallProgressPercent(run);
  const stageLabel = materialProgressStageLabel(run.progress_stage);
  const stageActivity =
    run.progress_stage === "queued"
      ? "排隊中"
      : run.progress_stage === "publishing"
        ? "發布中"
        : "處理中";
  return (
    <>
      <div className="processing-status" aria-live="polite">
        <div className="progress-heading overall-heading">
          <h2>整體流程進度（估計）</h2>
          <strong>{overallPercent === null ? "—" : `${overallPercent}%`}</strong>
        </div>
        <progress
          className="processing-progress"
          max={100}
          value={overallPercent ?? undefined}
          aria-label={
            overallPercent === null
              ? "整體流程進度（估計），尚無可估計資料"
              : `整體流程進度（估計） ${overallPercent}%`
          }
        />
        <p className="progress-estimate-note">依處理階段與頁數估算，不代表剩餘時間。</p>
        <section className="processing-current">
          <h3>{currentPercent === null ? "目前狀態" : "本階段進度"}</h3>
          <div className="progress-heading">
            <strong
              className={`stage-label${currentPercent === null ? " stage-status-label" : ""}`}
            >
              {currentPercent === null && (
                <span className="processing-status-indicator" aria-hidden="true" />
              )}
              {stageLabel}
            </strong>
            <strong>{currentPercent === null ? stageActivity : `${currentPercent}%`}</strong>
          </div>
          {currentPercent === null ? (
            <p>
              {run.progress_stage === "queued"
                ? "正在等待本機處理資源，開始後會自動更新進度。"
                : run.progress_stage === "publishing"
                  ? "正在整理並發布可開啟的知識地圖。"
                  : "正在處理教材內容。"}
            </p>
          ) : (
            <progress
              className="processing-progress"
              max={100}
              value={currentPercent}
              aria-label={`本階段進度 ${currentPercent}%，已完成 ${run.completed_pages} / ${run.total_pages} 頁`}
            />
          )}
          {currentPercent !== null && (
            <p className="stage-pages">
              已完成 {run.completed_pages} / {run.total_pages} 頁
            </p>
          )}
        </section>
      </div>
      {showSources && run.source_names && (
        <div className="processing-sources">
          <h2>這次分析的來源（{run.source_names.length} 份）</h2>
          <ol>
            {run.source_names.map((name, index) => (
              <li key={index}>{name}</li>
            ))}
          </ol>
        </div>
      )}
      <dl className="processing-times">
        <div>
          <dt>已耗時</dt>
          <dd>{materialElapsedLabel(run.created_at, now)}</dd>
        </div>
        <div>
          <dt>最近更新</dt>
          <dd>
            <time dateTime={run.updated_at}>
              {new Date(run.updated_at).toLocaleTimeString("zh-TW")}
            </time>
          </dd>
        </div>
      </dl>
      <p className="processing-leave-note">進度會自動保存，可稍後從「我的教材」返回查看。</p>
    </>
  );
}
