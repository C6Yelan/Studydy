import type { MaterialProcessingRunView } from "../../api/contracts";
import type { StudydyApiClient } from "../../api/client";
import { writeRoute } from "../../app/routes";
import { MaterialRunStartControl } from "./MaterialRunStartControl";
import { materialElapsedLabel, materialFailureMessage, materialProgressStageLabel, materialStageCountLabel } from "./material-flow";
import "./run-outcomes.css";

export function MaterialRunFailure({apiClient,run}:{apiClient:StudydyApiClient;run:MaterialProcessingRunView}) {
  return <section className="surface processing-card run-failure-content" role="alert">
    <p>{run.error_code === "SOURCE_UPDATE_NEEDS_REVIEW"
      ? "先前流程因模型的複核提示而停止，這不代表教材已確認有矛盾。可接續已保存的結果完成更新。"
      : materialFailureMessage(run.error_code ?? "MATERIAL_ANALYSIS_FAILED")}</p>
    <p className="failure-progress" role="status">最後記錄進度：{materialProgressStageLabel(run.progress_stage)}{materialStageCountLabel(run) && `，${materialStageCountLabel(run)}`}</p>
    {!run.base_revision && !materialStageCountLabel(run) && run.total_pages !== null && <p>已處理 {run.completed_pages} / {run.total_pages} 頁</p>}
    <dl className="run-failure-times">
      <div><dt>已耗時</dt><dd>{materialElapsedLabel(run.created_at,Date.parse(run.completed_at??run.updated_at))}</dd></div>
      <div><dt>最近更新</dt><dd><time dateTime={run.updated_at}>{new Date(run.updated_at).toLocaleTimeString("zh-TW")}</time></dd></div>
    </dl>
    {run.analysis_saved ? <p role="status">已完成的處理進度已保存；使用相同來源與設定重試，會接續未完成的部分。</p>
      : run.input_source_set_id && <p role="status">這次沒有已保存的分析進度；重試將重新分析原來源。</p>}
    <div className="run-failure-actions"><MaterialRunStartControl apiClient={apiClient} materialId={run.material_id} retryRun={{runId:run.run_id,saved:!!run.analysis_saved}}/></div>
    {run.input_source_set_id && <button className="text-button" onClick={()=>writeRoute({name:"material-sources",materialId:run.material_id})}>修改來源與重新分析</button>}
    {run.error_code && <p className="run-failure-code">錯誤代碼：<code>{run.error_code}</code></p>}
  </section>;
}
