import { useEffect, useRef, useState } from "react";
import { errorMessage } from "../../api/client";
import "./styles.css";

export type GoalFormValues = { date: string; percent: 60 | 80 | 100 };

export function GoalEditor({ current, today, onSave, onClose }: {
  current: GoalFormValues | null; today: string;
  onSave: (value: GoalFormValues) => Promise<void>; onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const active = useRef(true);
  const [date, setDate] = useState(current?.date ?? "");
  const [percent, setPercent] = useState<GoalFormValues["percent"] | null>(current?.percent ?? null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    active.current = true; dialog.current?.showModal();
    return () => { active.current = false; dialog.current?.close(); };
  }, []);
  const save = async () => {
    if (!date || !percent || busy) return;
    setBusy(true); setError(null);
    try { await onSave({ date, percent }); }
    catch (failure) { if (active.current) setError(errorMessage(failure)); }
    finally { if (active.current) setBusy(false); }
  };
  return <dialog ref={dialog} className="goal-editor" aria-labelledby="goal-editor-title"
    onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
    <form onSubmit={event => { event.preventDefault(); void save(); }}>
      <h2 id="goal-editor-title">{current ? "更改學習目標" : "設定學習目標"}</h2>
      <p>依可檢測重點的完成比例安排學習，已保存的作答與完成紀錄會保留。</p>
      <GoalFields date={date} percent={percent} today={today} originalDate={current?.date} disabled={busy} onDateChange={setDate} onPercentChange={setPercent} />
      {error && <p role="alert" className="form-error">{error}</p>}
      <div className="state-actions">
        <button className="secondary-button" type="button" disabled={busy} onClick={onClose}>取消</button>
        <button className="primary-button" type="submit" disabled={busy || !date || !percent}>{busy ? "正在儲存…" : "儲存目標"}</button>
      </div>
    </form>
  </dialog>;
}

export function GoalFields({ date, percent, today, originalDate, disabled, onDateChange, onPercentChange }: {
  date: string; percent: GoalFormValues["percent"] | null; today: string; originalDate?: string;
  disabled: boolean; onDateChange: (date: string) => void; onPercentChange: (percent: GoalFormValues["percent"]) => void;
}) {
  return <>
      <label className="goal-date-field"><span>目標日期</span>
        <input type="date" required min={originalDate && originalDate < today ? originalDate : today} value={date} disabled={disabled} aria-describedby="goal-date-note" onChange={event => onDateChange(event.currentTarget.value)} />
      </label>
      <small id="goal-date-note">日期以台灣時間為準，包含目標當天。{originalDate && originalDate < today && "可保留原目標日期；若更改日期，請選今天或之後。"}</small>
      <fieldset className="goal-target-options" disabled={disabled}>
        <legend>掌握目標</legend>
        {([{ value: 60, label: "及格" }, { value: 80, label: "熟練" }, { value: 100, label: "精通" }] as const).map(option =>
          <label key={option.value} className={percent === option.value ? "is-selected" : ""}>
            <input type="radio" name="goal-target" value={option.value} checked={percent === option.value} onChange={() => onPercentChange(option.value)} />
            <strong>{option.value}%</strong><span>{option.label}</span>
          </label>)}
      </fieldset>
  </>;
}
