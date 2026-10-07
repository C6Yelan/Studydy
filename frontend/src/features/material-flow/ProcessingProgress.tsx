import type { MaterialProcessingRunView } from "../../api/contracts";
import { materialCurrentStagePercent, materialOverallProgressPercent, materialProgressStageLabel,
  materialProgressStages, materialStageCountLabel, materialElapsedLabel } from "./material-flow";

export function ProcessingProgress({ run, now, showSources = true }: { run: MaterialProcessingRunView; now: number; showSources?: boolean }) {
  const percent = materialCurrentStagePercent(run);
  const finished = materialOverallProgressPercent(run) === 100;
  const active = run.status === "pending" || run.status === "running";
  const stageLabel = materialProgressStageLabel(run.progress_stage);
  const countLabel = materialStageCountLabel(run);
  return <>
    <div className="processing-status" aria-live="polite">
      <div className="progress-heading overall-heading">
        <h2>{finished ? "處理完成" : `第 ${materialProgressStages.indexOf(run.progress_stage) + 1} / ${materialProgressStages.length - 1} 階段`}</h2>
        {finished && <strong>100%</strong>}
      </div>
      <section className="processing-current">
        <h3>{percent === null ? "目前狀態" : "本階段進度"}</h3>
        <div className="progress-heading">
          <strong className="stage-label stage-status-label">
            {active && <span className="processing-status-indicator" aria-hidden="true" />}{stageLabel}
          </strong>
          <strong>{percent === null ? (active ? "進行中" : "已停止") : `${percent}%`}</strong>
        </div>
        {percent !== null ? <>
          <progress className="processing-progress" max={100} value={percent}
            aria-label={`本階段進度 ${percent}%，${countLabel}`} />
          <p className="stage-pages">{countLabel}</p>
        </> : <p>{run.progress_stage === "queued" ? "正在等待處理資源，開始後會自動更新進度。"
          : run.progress_stage === "publishing" ? "正在合併並驗證知識結構，保存成功後才會完成。"
          : "此階段尚無可計算的工作總數。"}</p>}
        {run.progress_stage === "review" && <p className="progress-estimate-note">計數只包含已通過檢查的批次；容量拆分時總批次數會更新。</p>}
      </section>
    </div>
    {showSources && run.source_names && <details className="processing-sources">
      <summary>本次分析 {run.source_names.length} 份來源</summary>
      <ol>{run.source_names.map((name, index) => <li key={index}>{name}</li>)}</ol>
    </details>}
    <dl className="processing-times">
      <div><dt>已耗時</dt><dd>{materialElapsedLabel(run.created_at, active ? now : Date.parse(run.completed_at ?? run.updated_at))}</dd></div>
      <div><dt>最近更新</dt><dd><time dateTime={run.updated_at}>{new Date(run.updated_at).toLocaleTimeString("zh-TW")}</time></dd></div>
    </dl>
    <p className="processing-leave-note">進度會自動保存，可稍後從「我的教材」返回查看。</p>
  </>;
}
