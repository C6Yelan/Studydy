import { useEffect, useRef, useState } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import { writeRoute } from "../../app/routes";

// 同一教材／工作重試沿用同一意圖，回應遺失時不另建工作。
export function MaterialRunStartControl({
  apiClient,
  materialId,
  retryRun,
}: {
  apiClient: StudydyApiClient;
  materialId: string;
  retryRun: { runId: string; saved: boolean };
}) {
  const intent = useRef<string | null>(null);
  const button = useRef<HTMLButtonElement>(null);
  const inFlight = useRef(false);
  const mounted = useRef(true);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    if (failure && !busy) button.current?.focus({ preventScroll: true });
  }, [failure, busy]);
  const start = async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setFailure(null);
    intent.current ??= crypto.randomUUID();
    try {
      const next = await apiClient.retryRevision(retryRun.runId, intent.current);
      if (!mounted.current) return;
      intent.current = null;
      writeRoute({ name: "material-run", materialId, runId: next.run_id });
    } catch (error) {
      if (mounted.current) setFailure(`無法重新開始處理，請再試一次。${errorMessage(error)}`);
    } finally {
      inFlight.current = false;
      if (mounted.current) setBusy(false);
    }
  };
  return (
    <>
      <button
        ref={button}
        className="primary-button"
        type="button"
        disabled={busy}
        onClick={() => void start()}
      >
        {busy ? "正在重新處理…" : retryRun.saved ? "接續已保存的分析" : "重新分析原來源"}
      </button>
      {busy && (
        <span className="material-recovery-status" role="status">
          正在重新處理…
        </span>
      )}
      {failure && (
        <p className="form-error material-recovery-error" role="alert">
          {failure}
        </p>
      )}
    </>
  );
}
