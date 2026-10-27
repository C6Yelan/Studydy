import { useEffect, useRef, useState } from "react";
import type { StartingLevel } from "../../api/contracts";
import { GoalFields, type GoalFormValues } from "../learning-goals/GoalEditor";
import { levels, StartingLevelOptions } from "./StartingLevel";
import "./creation-learning-settings.css";

export type CreationSettings = { level: StartingLevel; goal: GoalFormValues | null };

export function CreationLearningSettings({ value, today, disabled, onChange }: {
  value: CreationSettings; today: string; disabled: boolean; onChange: (value: CreationSettings) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const [hasGoal, setHasGoal] = useState(false);
  const [date, setDate] = useState("");
  const [percent, setPercent] = useState<GoalFormValues["percent"] | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const opener = useRef<HTMLButtonElement>(null);
  useEffect(() => { if (editing) dialog.current?.showModal(); }, [editing]);
  return <div className="creation-learning-settings">
    <div><strong>{levels.find(level => level.value === (value.level === "beginner" ? "some_knowledge" : value.level))?.label}</strong>
      <small>{value.goal ? `${value.goal.date} · 掌握 ${value.goal.percent}%` : "尚未設定目標（選填）"}</small></div>
    <button ref={opener} type="button" className="text-button" disabled={disabled} onClick={() => {
      setDraft(value); setHasGoal(!!value.goal); setDate(value.goal?.date ?? ""); setPercent(value.goal?.percent ?? null); setEditing(true);
    }}>程度與目標</button>
    {editing && <dialog ref={dialog} className="starting-level-dialog creation-settings-dialog" aria-labelledby="creation-settings-title"
      onCancel={event => { event.preventDefault(); dialog.current?.close(); }}
      onClose={() => { setEditing(false); opener.current?.focus(); }}>
      <form onSubmit={event => {
        event.preventDefault();
        if (hasGoal && (!date || !percent)) return;
        onChange({ level: draft.level, goal: hasGoal ? { date, percent: percent! } : null });
        dialog.current?.close();
      }}>
        <h2 id="creation-settings-title">程度與目標</h2>
        <p>只影響學習安排。之後可以在教材頁更改。</p>
        <StartingLevelOptions selected={draft.level === "beginner" ? "some_knowledge" : draft.level} onChange={level => setDraft({ ...draft, level })} />
        <label className="creation-goal-toggle"><input type="checkbox" checked={hasGoal} onChange={event => setHasGoal(event.currentTarget.checked)} />設定學習目標（選填）</label>
        {hasGoal && <GoalFields date={date} percent={percent} today={today} originalDate={value.goal?.date} disabled={false} onDateChange={setDate} onPercentChange={setPercent} />}
        <p className="creation-goal-note">{hasGoal ? "知識地圖建好後，會依目標自動安排每日關卡。" : "略過目標也可以開始學習，之後隨時可設定。"}</p>
        <div className="state-actions">
          <button type="button" className="secondary-button" onClick={() => dialog.current?.close()}>取消</button>
          <button type="submit" className="primary-button" disabled={hasGoal && (!date || !percent)}>套用設定</button>
        </div>
      </form>
    </dialog>}
  </div>;
}
