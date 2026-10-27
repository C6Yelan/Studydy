import type { MaterialProcessingRunView } from "../../api/contracts";

export function ProcessingSources({ run }: { run: MaterialProcessingRunView }) {
  if (run.base_revision) return run.added_sources && <div className="processing-sources">
    <p>本次新增 {run.added_sources.length} 份來源：{run.added_sources.map(source => source.display_name).join("、")}</p>
    {run.existing_source_count !== undefined && <p>並與既有 {run.existing_source_count} 份來源整合</p>}
  </div>;
  return run.source_names && <details className="processing-sources">
    <summary>本次分析 {run.source_names.length} 份來源</summary>
    <ol>{run.source_names.map((name, index) => <li key={index}>{name}</li>)}</ol>
  </details>;
}
