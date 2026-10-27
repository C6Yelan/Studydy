import { useEffect, useRef, useState } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import { writeRoute } from "../../app/routes";

type StudyDestination = { materialId: string; runId: string; structureRevision: string; conceptId?: string };

/** 教材入口共用開始用例；指定概念優先，否則由後端依地圖編號建議。 */
export function useStudyStart(api: StudydyApiClient, scope = "") {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const active = useRef(true);
  const generation = useRef(0);
  const intent = useRef<{ signature: string; key: string } | null>(null);
  useEffect(() => {
    active.current = true; generation.current++;
    setBusy(false); setError(null); intent.current = null;
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
      intent.current = null;
      writeRoute({ name: "study-session", materialId: destination.materialId, runId: destination.runId,
        structureRevision: destination.structureRevision, studySessionId: result.session.study_session_id,
        ...(result.selected_set_id ? { assessmentSetId: result.selected_set_id } : {}) });
    } catch (failure) {
      if (!isCurrent()) return;
      setError(errorMessage(failure));
    } finally { if (isCurrent()) setBusy(false); }
  };
  return { start, busy, error };
}
