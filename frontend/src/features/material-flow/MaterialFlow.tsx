import { useEffect, useRef, useState } from "react";

import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { MaterialProcessingRunView } from "../../api/contracts";
import { writeRoute, type AppRoute } from "../../app/routes";
import KnowledgeMap from "../knowledge-map/App";
import {
  automaticPollIntervalMs,
  automaticPollLimit,
  formatFileSize,
  materialFailureMessage,
  materialRunLabel,
  validatePdfFile,
} from "./material-flow";


function UploadView({ apiClient }: { apiClient: StudydyApiClient }) {
  const [file, setFile] = useState<File | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const uploadKey = useRef(crypto.randomUUID());
  const runKey = useRef(crypto.randomUUID());

  const submit = async () => {
    const validation = validatePdfFile(file);
    if (validation || !file) {
      setMessage(validation);
      return;
    }
    setIsSubmitting(true);
    setMessage(null);
    try {
      const material = await apiClient.createMaterial(file, uploadKey.current);
      const run = await apiClient.createMaterialRun(
        {
          schema: "material-processing-create/v2",
          material_id: material.material_id,
          source_artifact_id: material.source_artifact_id,
        },
        runKey.current,
      );
      writeRoute({ name: "material-run", materialId: run.material_id, runId: run.run_id });
    } catch (error) {
      setMessage(errorMessage(error));
      setIsSubmitting(false);
    }
  };

  return (
    <section className="upload-page">
      <div className="upload-copy">
        <p className="eyebrow">PDF → 可回查的概念地圖</p>
        <h1>上傳完整教材，逐頁建立複核地圖</h1>
        <p>目前接受 1–32 頁的 application/pdf。系統會處理整份文件，不會截斷或只挑部分頁面。</p>
      </div>
      <div className="surface upload-card">
        <label className="file-drop">
          <input
            type="file"
            accept="application/pdf"
            disabled={isSubmitting}
            onChange={(event) => {
              const next = event.currentTarget.files?.[0] ?? null;
              setFile(next);
              setMessage(validatePdfFile(next));
            }}
          />
          <img src="/assets/studydy/upload-guide.png" alt="" />
          <strong>{file ? file.name : "選擇 PDF 教材"}</strong>
          <span>{file ? formatFileSize(file.size) : "最多 100 MiB，正式流程最多 32 頁"}</span>
        </label>
        {message && <p className="form-error" role="alert">{message}</p>}
        <button className="primary-button" type="button" disabled={isSubmitting} onClick={submit}>
          {isSubmitting ? "正在建立處理作業…" : "上傳並分析完整教材"}
        </button>
      </div>
    </section>
  );
}


function RunView({ apiClient, route }: {
  apiClient: StudydyApiClient;
  route: Extract<AppRoute, { name: "material-run" }>;
}) {
  const [run, setRun] = useState<MaterialProcessingRunView | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: number | null = null;
    let pollCount = 0;
    const poll = async () => {
      try {
        const next = await apiClient.getMaterialRun(route.runId);
        if (cancelled) return;
        if (next.material_id !== route.materialId) throw new Error("RUN_MATERIAL_MISMATCH");
        setRun(next);
        setMessage(null);
        if ((next.status === "pending" || next.status === "running") && pollCount < automaticPollLimit) {
          pollCount += 1;
          timer = window.setTimeout(poll, automaticPollIntervalMs);
        }
      } catch (error) {
        if (!cancelled) setMessage(errorMessage(error));
      }
    };
    void poll();
    return () => {
      cancelled = true;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [apiClient, route.materialId, route.runId]);

  if (message) return (
    <section className="state-page failure-page" role="alert">
      <img className="state-illustration" src="/assets/studydy/failure-confused.png" alt="" />
      <h1>無法讀取處理狀態</h1><p>{message}</p>
    </section>
  );
  if (!run || run.status === "pending" || run.status === "running") return (
    <section className="state-page" aria-live="polite">
      <img className="state-illustration" src="/assets/studydy/processing-laptop.png" alt="" />
      <div className="loading-ring" />
      <h1>{run ? materialRunLabel(run.status) : "正在讀取處理狀態"}</h1>
      <p>OCR 與概念模型會依序載入；完成前不會發布任何 domain revision。</p>
    </section>
  );
  if (run.status === "failed") return (
    <section className="state-page failure-page" role="alert">
      <img className="state-illustration" src="/assets/studydy/failure-confused.png" alt="" />
      <h1>教材處理失敗</h1>
      <p>{materialFailureMessage(run.error_code ?? "MATERIAL_ANALYSIS_FAILED")}</p>
      <code>{run.error_code}</code>
      <button className="secondary-button" type="button" onClick={() => writeRoute({ name: "home" })}>返回上傳</button>
    </section>
  );
  const binding = run.output_binding!;
  return (
    <section className="state-page success-page">
      <img className="state-illustration" src="/assets/studydy/success-thumbs-up.png" alt="" />
      <span className="outcome-badge is-review">需要複核</span>
      <h1>{materialRunLabel(run.status)}</h1>
      <p>共檢查 {binding.page_count} 頁；地圖只顯示有同頁 PDF locator 的概念與 Evidence。</p>
      <button className="primary-button" type="button" onClick={() => writeRoute({
        name: "knowledge-map",
        materialId: run.material_id,
        runId: run.run_id,
        mapRevision: binding.knowledge_map_revision,
      })}>開啟複核地圖</button>
    </section>
  );
}


export function MaterialFlow({ apiClient, route }: {
  apiClient: StudydyApiClient;
  route: AppRoute;
}) {
  if (route.name === "home") return <UploadView apiClient={apiClient} />;
  if (route.name === "material-run") return <RunView apiClient={apiClient} route={route} />;
  return <KnowledgeMap apiClient={apiClient} route={route} />;
}
