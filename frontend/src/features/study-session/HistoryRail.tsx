import { useState } from "react";
import type { AssessmentSetSummary } from "../../api/contracts";

type Filter = "all" | "diagnostic" | "remediation";
const filters: [Filter, string][] = [["all", "全部"], ["diagnostic", "初篩"], ["remediation", "補強"]];

export function HistoryRail({ sets, selectedSetId, conceptLabel, onSelect }: {
  sets: AssessmentSetSummary[]; selectedSetId: string | null;
  conceptLabel: (conceptId: string) => string; onSelect: (setId: string) => void;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  return <aside className="result-rail" aria-label="作答紀錄">
    <header>
      <h2>作答紀錄（{sets.length}）</h2>
      <div className="result-rail-filter">
        {filters.map(([value, label]) => <button key={value} type="button" aria-pressed={filter === value} onClick={() => setFilter(value)}>{label}</button>)}
      </div>
    </header>
    <ol>
      {sets.flatMap((group, index) => filter !== "all" && (group.kind === "remediation") !== (filter === "remediation") ? [] : [
        <li key={group.set_id}><button type="button" className={`study-history-item is-${group.kind}`}
          aria-current={group.set_id === selectedSetId ? "true" : undefined} onClick={() => onSelect(group.set_id)}>
          <b>第 {sets.length - index} 組 · {group.kind === "remediation" ? "補強" : "初篩"}</b>
          <span>{conceptLabel(group.target_concept_id)}</span>
          <small>{group.published_count === 0 ? "此題組已結束" : group.answered_count === 0 ? "尚未作答" : `已答 ${group.answered_count}/${group.published_count}`}</small>
        </button></li>])}
    </ol>
  </aside>;
}
