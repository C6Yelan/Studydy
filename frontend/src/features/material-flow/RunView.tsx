import { useEffect, useState } from "react";

import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { MaterialProcessingRunView } from "../../api/contracts";
import { writeRoute, type AppRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";
import { StateView } from "../../ui/StateView";
import {
  automaticPollIntervalMs,
  materialFailureMessage,
  materialRunHasUsableMap,
  materialRunLabel,
} from "./material-flow";

export function RunView({ apiClient, route }: {
  apiClient: StudydyApiClient;
  route: Extract<AppRoute, { name: "material-run" }>;
}) {
  const [run, setRun] = useState<MaterialProcessingRunView | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let timer: number | null = null;
    const poll = async () => {
      try {
        const next = await apiClient.getMaterialRun(route.runId);
        if (cancelled) return;
        if (next.material_id !== route.materialId) throw new Error("RUN_MATERIAL_MISMATCH");
        setRun(next);
        setMessage(null);
        if (next.status === "pending" || next.status === "running") {
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
  }, [apiClient, reload, route.materialId, route.runId]);

  if (message) return (
    <StateView
      action={<button className="primary-button" type="button" onClick={() => setReload((value) => value + 1)}><Icon name="refresh" />重新讀取</button>}
      description={message}
      image="/assets/studydy/failure-confused.png"
      title="無法讀取處理狀態"
      tone="failure"
    />
  );

  if (!run || run.status === "pending" || run.status === "running") return (
    <section className="processing-page" aria-live="polite">
      <header className="processing-hero">
        <img src="/assets/studydy/processing-laptop.png" alt="" />
        <p className="eyebrow">Material Processing</p>
        <h1>{run ? materialRunLabel(run.status) : "正在讀取處理狀態"}</h1>
        <p>Studydy 正在整理教材；正式內容會在來源與安全檢查通過後才發布。</p>
      </header>
      <div className="processing-grid">
        <section className="surface processing-card">
          <h2>目前狀態</h2>
          <div className="indeterminate-progress" aria-label="教材處理中"><span /></div>
          <strong>{run?.status === "pending" ? "等待本機處理資源" : "正在分析完整教材"}</strong>
          <p>處理時間依教材頁數與頁面內容而不同，頁面可保持開啟。</p>
        </section>
        <section className="surface processing-card">
          <h2>處理原則</h2>
          <ol className="status-timeline">
            <li className="is-complete"><span><Icon name="check" /></span><div><strong>教材已安全接收</strong><p>已建立這份教材的獨立處理作業。</p></div></li>
            <li className="is-active"><span><Icon name="process" /></span><div><strong>分析內容與來源</strong><p>保留頁面定位，並對概念與關係做安全檢查。</p></div></li>
            <li><span><Icon name="map" /></span><div><strong>發布可複核知識地圖</strong><p>只有通過 contract 的公開內容會出現在前端。</p></div></li>
          </ol>
        </section>
      </div>
    </section>
  );

  if (run.status === "failed") return (
    <section className="terminal-failure">
      <StateView
        action={<button className="secondary-button" type="button" onClick={() => writeRoute({ name: "home" })}><Icon name="arrow-left" />返回上傳</button>}
        description={materialFailureMessage(run.error_code ?? "MATERIAL_ANALYSIS_FAILED")}
        image="/assets/studydy/failure-confused.png"
        title="教材處理失敗"
        tone="failure"
      />
      <code className="failure-code">{run.error_code}</code>
    </section>
  );

  if (!materialRunHasUsableMap(run)) return (
    <StateView
      action={<button className="primary-button" type="button" onClick={() => writeRoute({ name: "home" })}><Icon name="arrow-left" />改用其他教材</button>}
      description="這份教材沒有產生可安全顯示的概念，因此沒有發布知識地圖。請改用包含清楚教學內容的 PDF。"
      image="/assets/studydy/empty-disappointed.png"
      title="目前沒有可開啟的知識地圖"
      tone="empty"
    />
  );

  const binding = run.output_binding!;
  return (
    <section className="processing-page is-complete">
      <header className="processing-hero">
        <img src="/assets/studydy/success-jump.png" alt="" />
        <p className="eyebrow">Processing complete</p>
        <h1>{materialRunLabel(run.status)}</h1>
        <p>教材已完成來源與安全檢查，可以開啟知識地圖進行複核。</p>
      </header>
      <div className="processing-grid">
        <div className="processing-stack">
          <section className="surface processing-card material-result">
            <span className="file-kind"><Icon name="file" /></span>
            <div><h2>教材</h2><p>共處理 {binding.page_count} 頁</p></div>
            <span className="status-badge is-success"><Icon name="check" />處理完成</span>
          </section>
          <section className="surface processing-card result-summary">
            <h2>已發布內容</h2>
            <img src="/assets/studydy/processing-complete.png" alt="" />
            <ul>
              <li><Icon name="check" />可回查的概念與 Claim</li>
              <li><Icon name="check" />三種概念連結</li>
              <li><Icon name="check" />教材建議學習順序</li>
            </ul>
          </section>
        </div>
        <section className="surface processing-card">
          <h2>處理結果</h2>
          <div className="complete-progress"><strong>完成</strong><span><i /></span></div>
          <ol className="status-timeline">
            <li className="is-complete"><span><Icon name="check" /></span><div><strong>教材已接收</strong><p>檔案與處理作業完成綁定。</p></div></li>
            <li className="is-complete"><span><Icon name="check" /></span><div><strong>來源已保留</strong><p>每個重點都可回到原始 PDF 頁面。</p></div></li>
            <li className="is-complete"><span><Icon name="check" /></span><div><strong>知識地圖已發布</strong><p>{run.status === "partial" ? "部分頁面未安全納入，地圖會清楚標示。" : "可安全複核的內容已準備完成。"}</p></div></li>
          </ol>
        </section>
      </div>
      <div className="surface completion-bar">
        <span className="completion-icon"><Icon name="check" /></span>
        <div><strong>一切準備完成</strong><p>接著查看概念、連結、教材來源與建議順序。</p></div>
        <button className="primary-button" type="button" onClick={() => writeRoute({
          name: "knowledge-map",
          materialId: run.material_id,
          runId: run.run_id,
          mapRevision: binding.knowledge_map_revision,
        })}>開啟複核地圖<Icon name="chevron-right" /></button>
      </div>
    </section>
  );
}
