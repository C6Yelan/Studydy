import { useEffect, useRef, useState } from "react";
import { ApiClientError, errorMessage, type StudydyApiClient } from "../../api/client";
import type { StartingLevel } from "../../api/contracts";
import { writeRoute } from "../../app/routes";
import "./starting-level.css";

export const levels: { value: StartingLevel; label: string; description: string }[] = [
  { value: "some_knowledge", label: "第一次接觸，或還不太熟", description: "從基礎概念開始，照先備順序直接作答，需要時可查看說明。" },
  { value: "familiar", label: "學過了，想跳過已經會的", description: "先做約 15 題快速檢測，答錯的概念優先學，答對的往後排。" },
  { value: "gaps", label: "想找出還不會的地方（例如準備考試）", description: "從答錯過的弱點開始；沒有作答紀錄時先做快速檢測。" },
];

export function StartingLevelOptions({ selected, onChange, disabled = false }: {
  selected: StartingLevel; onChange: (level: StartingLevel) => void; disabled?: boolean;
}) {
  return <fieldset disabled={disabled} className="starting-level-options">
    <legend className="sr-only">起始程度</legend>
    {levels.map(level => <label key={level.value} className={selected === level.value ? "is-selected" : ""}>
      <input type="radio" name="starting-level" value={level.value} checked={selected === level.value} onChange={() => onChange(level.value)} />
      <span><strong>{level.label}</strong><small>{level.description}</small></span>
    </label>)}
  </fieldset>;
}

export function StartingLevelDialog({ api, materialId, current, onSaved, onClose }: {
  api: StudydyApiClient; materialId: string; current: StartingLevel | null;
  onSaved: (level: StartingLevel) => void; onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const active = useRef(true);
  const [selected, setSelected] = useState<StartingLevel>(current === "beginner" || !current ? "some_knowledge" : current);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    active.current = true;
    dialog.current?.showModal();
    return () => { active.current = false; dialog.current?.close(); };
  }, []);
  const save = async () => {
    if (!selected || busy) return;
    setBusy(true); setError(null);
    try {
      await api.setStartingLevel(materialId, selected);
      if (active.current) onSaved(selected);
    } catch (failure) { if (active.current) setError(errorMessage(failure)); }
    finally { if (active.current) setBusy(false); }
  };
  return <dialog ref={dialog} className="starting-level-dialog" aria-labelledby="starting-level-title"
    onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
    <h2 id="starting-level-title">你想從哪裡開始？</h2>
    <p>依你對這份教材的熟悉程度選擇，之後可以在教材頁更改。</p>
    <StartingLevelOptions selected={selected} onChange={setSelected} disabled={busy} />
    {error && <p role="alert" className="form-error">{error}</p>}
    <div className="state-actions">
      <button className="secondary-button" type="button" disabled={busy} onClick={onClose}>取消</button>
      <button className="primary-button" type="button" disabled={busy || !selected} onClick={() => void save()}>{busy ? "正在儲存…" : "儲存選擇"}</button>
    </div>
  </dialog>;
}

export function StartingLevelControl({ api, materialId, onChanged, nextConcept, onContinue, busy }: {
  api: StudydyApiClient; materialId: string; onChanged: () => void;
  nextConcept: string | null; onContinue: (() => void) | null; busy: boolean;
}) {
  const [level, setLevel] = useState<StartingLevel | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setLevel(undefined); setError(null);
    void api.getStartingLevel(materialId).then(value => { if (!cancelled) setLevel(value.starting_level); }, failure => { if (!cancelled) setError(errorMessage(failure)); });
    return () => { cancelled = true; };
  }, [api, materialId, reload]);
  return <section className="starting-level-setting" aria-label="起始程度設定">
    <div><h2>起始程度</h2><p>只調整後續學習安排，已完成的重點與進行中的題組會保留。</p></div>
    {error ? <p role="alert">{error}<button className="text-button" onClick={() => setReload(value => value + 1)}>重新讀取程度</button></p>
      : <button className="secondary-button" disabled={level === undefined} onClick={() => setEditing(true)}>{level === undefined ? "正在讀取…" : `起始程度：${levels.find(item => item.value === (level === "beginner" ? "some_knowledge" : level))?.label ?? "尚未選擇"} · 更改`}</button>}
    {onContinue && <div className="starting-level-next">
      {nextConcept && <p>接下來：{nextConcept}</p>}
      <button className="primary-button" disabled={busy} onClick={onContinue}>{busy ? "正在接續學習…" : nextConcept ? "依目前路線接續學習" : "開始學習"}</button>
    </div>}
    {editing && level !== undefined && <StartingLevelDialog api={api} materialId={materialId} current={level}
      onClose={() => setEditing(false)} onSaved={value => { setLevel(value); setEditing(false); onChanged(); }} />}
  </section>;
}

type StudyDestination = { materialId: string; runId: string; structureRevision: string; conceptId?: string };

/** 所有教材入口共用同一個開始用例；路線與診斷重用由後端決定。 */
export function useStudyStart(api: StudydyApiClient, scope = "") {
  const [pending, setPending] = useState<StudyDestination | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const active = useRef(true);
  const generation = useRef(0);
  const intent = useRef<{ signature: string; key: string } | null>(null);
  useEffect(() => {
    active.current = true; generation.current++;
    setPending(null); setBusy(false); setError(null); intent.current = null;
    return () => { active.current = false; };
  }, [api, scope]);
  const start = async (destination: StudyDestination) => {
    if (busy) return;
    const signature = JSON.stringify(destination);
    if (intent.current?.signature !== signature) intent.current = { signature, key: crypto.randomUUID() };
    const path = window.location.pathname;
    const version = generation.current;
    const isCurrent = () => active.current && version === generation.current && path === window.location.pathname;
    setBusy(true); setError(null);
    try {
      const result = await api.startStudy(destination, intent.current.key, destination.conceptId);
      if (!isCurrent()) return;
      setPending(null); intent.current = null;
      writeRoute({ name: "study-session", materialId: destination.materialId, runId: destination.runId,
        structureRevision: destination.structureRevision, studySessionId: result.session.study_session_id,
        ...(result.selected_set_id ? { assessmentSetId: result.selected_set_id } : {}) });
    } catch (failure) {
      if (!isCurrent()) return;
      if (failure instanceof ApiClientError && failure.reasonCode === "STARTING_LEVEL_REQUIRED") setPending(destination);
      else setError(errorMessage(failure));
    } finally { if (isCurrent()) setBusy(false); }
  };
  return { start, busy, error, dialog: pending && <StartingLevelDialog api={api} materialId={pending.materialId} current={null}
    onClose={() => { setPending(null); intent.current = null; }} onSaved={() => { const destination = pending; setPending(null); void start(destination); }} /> };
}
