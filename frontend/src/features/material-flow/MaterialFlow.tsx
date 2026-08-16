import { useEffect, useRef, useState, type DragEvent, type SubmitEvent } from "react";

import { ApiClientError, StudydyApiClient } from "../../api/client";
import type {
  KnowledgeMapView,
  LearningResourceResultView,
  MaterialProcessingRunView,
  MaterialSubject,
  MaterialView,
} from "../../api/contracts";
import { writeRoute, type AppRoute } from "../../app/routes";
import KnowledgeMap from "../knowledge-map/App";
import { AssessmentRoute, LearningStateRoute } from "../learning-flow/LearningFlow";
import {
  automaticPollIntervalMs,
  automaticPollLimit,
  formatFileSize,
  materialFailureMessage,
  materialRunLabel,
  materialRunRequestTimeoutMs,
  readRunSubject,
  saveRunSubject,
  validatePdfFile,
} from "./material-flow";

type UploadIntentKeys = { upload: string; run: string };

type ActiveUploadIntent = {
  keys: UploadIntentKeys;
  pdf: File;
  subject: MaterialSubject;
};

type UploadState =
  | { status: "idle" }
  | { status: "uploading" }
  | { status: "starting"; material: MaterialView }
  | { status: "failed"; message: string; keys: UploadIntentKeys; canRetry: boolean };

type RunLoadState =
  | { status: "loading" }
  | { status: "ready"; run: MaterialProcessingRunView; pollingStopped: boolean }
  | { status: "failed"; message: string; canRetry: boolean };

type StudyLoadState =
  | { status: "loading" }
  | {
      status: "ready";
      map: KnowledgeMapView;
      resources: LearningResourceResultView;
      sourceArtifactId: string;
    }
  | { status: "failed"; message: string };

const subjectOptions: { value: MaterialSubject; title: string; detail: string }[] = [
  { value: "data_structures", title: "資料結構", detail: "Data Structures" },
  { value: "economics", title: "經濟學", detail: "Economics" },
];

function errorMessage(error: unknown): string {
  return error instanceof ApiClientError ? error.message : "目前無法完成教材處理，請稍後再試。";
}

function statusLabel(value: string): string {
  const labels: Record<string, string> = {
    pending: "尚未開始",
    running: "處理中",
    succeeded: "已完成",
    partial: "部分完成",
    failed: "失敗",
    accepted: "已確認",
    needs_review: "待複核",
    unsupported: "不支援",
    retain: "保留",
    review: "需複核",
    reject: "不採用",
  };
  return labels[value] ?? value;
}

function openPdfInNewTab(pdf: Blob): void {
  const pdfUrl = URL.createObjectURL(pdf);
  const link = document.createElement("a");
  link.href = pdfUrl;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(pdfUrl), 60_000);
}

function UploadIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
      <path d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5M5 15.5v2.25A2.25 2.25 0 007.25 20h9.5A2.25 2.25 0 0019 17.75V15.5" />
    </svg>
  );
}

function HomeUpload({ apiClient }: { apiClient: StudydyApiClient }) {
  const [subject, setSubject] = useState<MaterialSubject | null>(null);
  const [pdf, setPdf] = useState<File | null>(null);
  const [fileIssue, setFileIssue] = useState<string | null>(null);
  const [upload, setUpload] = useState<UploadState>({ status: "idle" });
  const activeUploadIntent = useRef<ActiveUploadIntent | null>(null);

  useEffect(() => () => {
    activeUploadIntent.current = null;
  }, []);

  const chooseFile = (nextFile: File | null) => {
    if (activeUploadIntent.current) return;
    const issue = validatePdfFile(nextFile);
    setFileIssue(issue);
    setPdf(issue ? null : nextFile);
    if (!issue) setUpload({ status: "idle" });
  };

  const dropFile = (event: DragEvent<HTMLLabelElement>) => {
    event.preventDefault();
    chooseFile(event.dataTransfer.files.item(0));
  };

  const beginProcessing = async (keys: UploadIntentKeys) => {
    if (activeUploadIntent.current) return;
    const selectedPdf = pdf;
    const selectedSubject = subject;
    if (!selectedPdf) {
      setFileIssue("請先選擇 PDF 教材。");
      return;
    }
    if (!selectedSubject) {
      setUpload({ status: "failed", message: "請先選擇科目。", keys, canRetry: false });
      return;
    }

    const intent: ActiveUploadIntent = {
      keys,
      pdf: selectedPdf,
      subject: selectedSubject,
    };
    activeUploadIntent.current = intent;
    const isCurrentIntent = () => activeUploadIntent.current === intent;

    try {
      setUpload({ status: "uploading" });
      const material = await apiClient.createMaterial(intent.pdf, intent.keys.upload);
      if (!isCurrentIntent()) return;
      setUpload({ status: "starting", material });
      const run = await apiClient.createMaterialRun(
        {
          schema: "material-processing-create/v1",
          material_id: material.material_id,
          source_artifact_id: material.source_artifact_id,
          subject: intent.subject,
        },
        intent.keys.run,
      );
      if (!isCurrentIntent()) return;
      saveRunSubject(window.sessionStorage, run.run_id, intent.subject);
      writeRoute({ name: "material-run", materialId: material.material_id, runId: run.run_id });
    } catch (error) {
      if (!isCurrentIntent()) return;
      const knownError = error instanceof ApiClientError ? error : null;
      setUpload({
        status: "failed",
        message: errorMessage(error),
        keys: intent.keys,
        canRetry: knownError?.retryable ?? true,
      });
    } finally {
      if (isCurrentIntent()) activeUploadIntent.current = null;
    }
  };

  const submit = (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (activeUploadIntent.current) return;
    // 新的送出動作使用新的識別鍵；連線失敗後的重試則沿用原鍵，避免重複建立教材或作業。
    void beginProcessing({ upload: crypto.randomUUID(), run: crypto.randomUUID() });
  };

  const isSubmitting = upload.status === "uploading" || upload.status === "starting";

  return (
    <div className="upload-layout">
      <div className="upload-main">
        <section className="upload-hero">
          <img
            src="/assets/studydy/upload-guide.png"
            alt="Studydy 助手拿著 PDF，提示在此選擇教材"
          />
          <div>
            <p className="eyebrow">開始建立你的知識地圖</p>
            <h1>上傳學習資料</h1>
            <p>選擇科目與 PDF 教材，Studydy 會依真實處理狀態整理概念與學習路徑。</p>
          </div>
        </section>

        <form className="surface upload-form" onSubmit={submit} noValidate>
          <fieldset disabled={isSubmitting}>
            <legend><span className="step-number">1</span> 選擇科目</legend>
            <p className="field-help">目前支援資料結構與經濟學。</p>
            <div className="subject-options">
              {subjectOptions.map((option) => (
                <label className={`subject-option${subject === option.value ? " is-selected" : ""}`} key={option.value}>
                  <input
                    type="radio"
                    name="subject"
                    value={option.value}
                    checked={subject === option.value}
                    onChange={() => {
                      if (activeUploadIntent.current) return;
                      setSubject(option.value);
                      if (upload.status === "failed") setUpload({ status: "idle" });
                    }}
                  />
                  <span className="subject-mark" aria-hidden="true">{option.value === "data_structures" ? "⌘" : "∑"}</span>
                  <span><strong>{option.title}</strong><small>{option.detail}</small></span>
                </label>
              ))}
            </div>
          </fieldset>

          <fieldset disabled={isSubmitting}>
            <legend><span className="step-number">2</span> 選擇 PDF 教材</legend>
            <label
              className={`file-drop${pdf ? " has-file" : ""}${isSubmitting ? " is-disabled" : ""}`}
              aria-disabled={isSubmitting}
              onDragOver={(event) => event.preventDefault()}
              onDrop={dropFile}
            >
              <input
                type="file"
                aria-label="選擇 PDF 教材"
                accept="application/pdf,.pdf"
                onChange={(event) => chooseFile(event.currentTarget.files?.item(0) ?? null)}
              />
              <UploadIcon />
              {pdf ? (
                <span className="chosen-file">
                  <strong>{pdf.name}</strong>
                  <small>{formatFileSize(pdf.size)} · application/pdf</small>
                </span>
              ) : (
                <span>
                  <strong>將 PDF 拖放到這裡，或按此選擇檔案</strong>
                  <small>只接受 application/pdf，檔案上限 100 MiB</small>
                </span>
              )}
            </label>
            {fileIssue && <p className="field-error" role="alert">{fileIssue}</p>}
          </fieldset>

          {upload.status === "failed" && (
            <div className="inline-alert" role="alert">
              <span>{upload.message}</span>
              {pdf && subject && upload.canRetry && (
                <button type="button" className="text-button" onClick={() => void beginProcessing(upload.keys)}>
                  重試連線
                </button>
              )}
            </div>
          )}

          <button className="primary-button full-button" type="submit" disabled={isSubmitting}>
            {upload.status === "uploading" && "正在上傳並建立教材…"}
            {upload.status === "starting" && "教材已建立，正在啟動處理…"}
            {!isSubmitting && "上傳並開始處理"}
          </button>
          <p className="privacy-note">教材只會用於目前的 Studydy 學習工作階段。</p>
        </form>
      </div>

      <aside className="upload-aside" aria-label="教材處理說明">
        <section className="surface guide-card">
          <h2>接下來會發生什麼？</h2>
          <ol>
            <li><span>1</span><div><strong>上傳教材</strong><p>建立教材並記錄原始 PDF。</p></div></li>
            <li><span>2</span><div><strong>啟動分析</strong><p>開始整理教材內容與概念。</p></div></li>
            <li><span>3</span><div><strong>確認結果</strong><p>顯示成功、部分完成或失敗與真實原因。</p></div></li>
          </ol>
        </section>
        <section className="surface requirements-card">
          <h2>檔案需求</h2>
          <p><strong>格式</strong><span>application/pdf</span></p>
          <p><strong>檔案大小</strong><span>最多 100 MiB</span></p>
        </section>
      </aside>
    </div>
  );
}

function ProcessingView({ run, pollingStopped, refresh }: {
  run: MaterialProcessingRunView;
  pollingStopped: boolean;
  refresh: () => void;
}) {
  return (
    <section className="state-page" aria-live="polite">
      <img className="state-illustration" src="/assets/studydy/processing-laptop.png" alt="" />
      <p className="eyebrow">教材處理狀態</p>
      <h1>{run.status === "pending" ? "教材已排入處理" : "正在建立你的知識地圖"}</h1>
      <p>頁面會依後端回報自動更新，不會顯示推測的百分比或步驟。</p>
      <div className="surface status-card">
        <div>
          <span className={`status-dot is-${run.status}`} aria-hidden="true" />
          <div>
            <span className="status-code">目前狀態：{materialRunLabel(run.status)}</span>
            <strong>{materialRunLabel(run.status)}</strong>
          </div>
        </div>
        <dl className="identity-list">
          <div><dt>教材編號</dt><dd><code>{run.material_id}</code></dd></div>
          <div><dt>處理作業編號</dt><dd><code>{run.run_id}</code></dd></div>
          <div><dt>最近更新</dt><dd>{new Date(run.updated_at).toLocaleString("zh-TW")}</dd></div>
        </dl>
      </div>
      {pollingStopped && (
        <div className="inline-alert" role="status">
          <span>自動更新已達安全上限；處理可能仍在進行。</span>
          <button className="text-button" type="button" onClick={refresh}>重新查詢狀態</button>
        </div>
      )}
    </section>
  );
}

function OutcomeView({ run }: { run: MaterialProcessingRunView }) {
  const binding = run.output_binding!;
  const revisions = [
    ["教材輸出", binding.study_material_output_revision],
    ["資源目錄", binding.catalog_revision],
    ["學習資源", binding.learning_resource_result_revision],
    ["知識地圖", binding.knowledge_map_revision],
    ["學習路徑", binding.learning_path_revision],
    ["評量", binding.assessment_revision],
  ];
  const providerCounts = [
    ["頁面結構", binding.provider_call_counts.page_structure],
    ["視覺對齊複核", binding.provider_call_counts.visual_alignment_adjudication],
    ["概念候選", binding.provider_call_counts.concept_candidate],
    ["概念內容", binding.provider_call_counts.concept_content],
  ];

  return (
    <section className="state-page outcome-page">
      <img className="state-illustration" src="/assets/studydy/success-thumbs-up.png" alt="" />
      <span className={`outcome-badge is-${run.status}`}>{materialRunLabel(run.status)}</span>
      <h1>{run.status === "succeeded" ? "教材處理完成" : "教材部分完成，需要複核"}</h1>
      <p>以下是這次教材處理的實際結果。</p>

      <div className="outcome-grid">
        <section className="surface outcome-card">
          <h2>處理判定</h2>
          <dl className="fact-list">
            <div><dt>處理狀態</dt><dd>{statusLabel(binding.processing)}</dd></div>
            <div><dt>內容品質</dt><dd>{statusLabel(binding.quality)}</dd></div>
            <div><dt>使用判定</dt><dd>{statusLabel(binding.decision)}</dd></div>
            <div><dt>原因代碼</dt><dd><code>{binding.reason_code}</code></dd></div>
            <div><dt>環境</dt><dd>{binding.development_only ? "開發環境" : "—"}</dd></div>
          </dl>
        </section>

        <section className="surface outcome-card">
          <h2>分析使用次數</h2>
          <dl className="call-counts">
            {providerCounts.map(([label, count]) => <div key={label}><dt>{label}</dt><dd>{count}</dd></div>)}
            <div className="total"><dt>總呼叫次數</dt><dd>{binding.provider_call_counts.total}</dd></div>
          </dl>
        </section>
      </div>

      <section className="surface revisions-card">
        <h2>結果版本</h2>
        <dl>
          {revisions.map(([label, revision]) => (
            <div key={label}><dt>{label}</dt><dd><code>{revision}</code></dd></div>
          ))}
        </dl>
      </section>
      <div className="button-row">
        <button className="primary-button" type="button" onClick={() => writeRoute({
          name: "assessment",
          materialId: run.material_id,
          runId: run.run_id,
          assessmentRevision: binding.assessment_revision,
        })}>
          開始學習評量
        </button>
        <button className="primary-button" type="button" onClick={() => writeRoute({
          name: "knowledge-map",
          materialId: run.material_id,
          runId: run.run_id,
          mapRevision: binding.knowledge_map_revision,
          pathRevision: binding.learning_path_revision,
        })}>
          開啟知識地圖與學習資源
        </button>
        <button className="secondary-button" type="button" onClick={() => writeRoute({ name: "home" })}>
          上傳另一份教材
        </button>
      </div>
    </section>
  );
}

function FailureView({ apiClient, run }: { apiClient: StudydyApiClient; run: MaterialProcessingRunView }) {
  const [retryMessage, setRetryMessage] = useState<string | null>(null);
  const [isRetrying, setIsRetrying] = useState(false);
  const retryKey = useRef<string | null>(null);
  const isRetryingRef = useRef(false);
  const isMounted = useRef(true);
  const subject = readRunSubject(window.sessionStorage, run.run_id);

  useEffect(() => {
    isMounted.current = true;
    return () => {
      isMounted.current = false;
    };
  }, []);

  const retryRun = async () => {
    if (!subject || isRetryingRef.current) return;
    isRetryingRef.current = true;
    // 重新處理會沿用已上傳的原始 PDF 建立新作業；同一次連線重試仍沿用識別鍵。
    if (!retryKey.current) retryKey.current = crypto.randomUUID();
    setIsRetrying(true);
    setRetryMessage(null);
    try {
      const nextRun = await apiClient.createMaterialRun(
        {
          schema: "material-processing-create/v1",
          material_id: run.material_id,
          source_artifact_id: run.source_artifact_id,
          subject,
        },
        retryKey.current,
      );
      if (!isMounted.current) return;
      saveRunSubject(window.sessionStorage, nextRun.run_id, subject);
      retryKey.current = null;
      writeRoute({ name: "material-run", materialId: run.material_id, runId: nextRun.run_id });
    } catch (error) {
      if (!isMounted.current) return;
      setRetryMessage(errorMessage(error));
      setIsRetrying(false);
      isRetryingRef.current = false;
    }
  };

  return (
    <section className="state-page failure-page">
      <img className="state-illustration" src="/assets/studydy/failure-confused.png" alt="" />
      <span className="outcome-badge is-failed">{materialRunLabel(run.status)}</span>
      <h1>教材處理失敗</h1>
      <p>{materialFailureMessage(run.error_code!)}</p>
      <div className="surface failure-card">
        <dl className="fact-list">
          <div><dt>原因代碼</dt><dd><code>{run.error_code}</code></dd></div>
          <div><dt>教材編號</dt><dd><code>{run.material_id}</code></dd></div>
          <div><dt>原始檔案編號</dt><dd><code>{run.source_artifact_id}</code></dd></div>
          <div><dt>處理作業編號</dt><dd><code>{run.run_id}</code></dd></div>
        </dl>
        {subject ? (
          <button className="primary-button" type="button" disabled={isRetrying} onClick={() => void retryRun()}>
            {isRetrying ? "正在建立新的處理作業…" : "使用原教材重新處理"}
          </button>
        ) : (
          <p className="recovery-note">此分頁缺少原科目資訊，無法安全建立新的處理作業。請返回上傳頁重新選擇教材。</p>
        )}
        {retryMessage && <p className="field-error" role="alert">{retryMessage}</p>}
      </div>
      <button className="text-button" type="button" onClick={() => writeRoute({ name: "home" })}>返回上傳頁</button>
    </section>
  );
}

function MaterialRun({ apiClient, route }: {
  apiClient: StudydyApiClient;
  route: Extract<AppRoute, { name: "material-run" }>;
}) {
  const [refreshVersion, setRefreshVersion] = useState(0);
  const [runLoad, setRunLoad] = useState<RunLoadState>({ status: "loading" });

  // 每次進入頁面或手動重新查詢都建立獨立的輪詢週期，離開時會清除計時器並取消尚未完成的請求。
  useEffect(() => {
    let isCancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let activeRequest: AbortController | null = null;
    let pollCount = 0;

    const readRun = async () => {
      const request = new AbortController();
      activeRequest = request;
      const requestTimeout = setTimeout(() => request.abort(), materialRunRequestTimeoutMs);
      try {
        const run = await apiClient.getMaterialRun(route.runId, request.signal);
        if (isCancelled) return;
        if (run.material_id !== route.materialId) {
          setRunLoad({ status: "failed", message: "網址與教材資料不一致。", canRetry: false });
          return;
        }
        const isProcessing = run.status === "pending" || run.status === "running";
        pollCount += 1;
        // 達到上限只會停止前端自動查詢，不會中止後端仍在執行的處理作業。
        const pollingStopped = isProcessing && pollCount >= automaticPollLimit;
        setRunLoad({ status: "ready", run, pollingStopped });
        if (isProcessing && !pollingStopped) {
          timer = setTimeout(() => void readRun(), automaticPollIntervalMs);
        }
      } catch (error) {
        if (isCancelled) return;
        const knownError = error instanceof ApiClientError ? error : null;
        setRunLoad({
          status: "failed",
          message: errorMessage(error),
          canRetry: knownError?.retryable ?? true,
        });
      } finally {
        clearTimeout(requestTimeout);
        if (activeRequest === request) activeRequest = null;
      }
    };

    setRunLoad({ status: "loading" });
    void readRun();
    return () => {
      isCancelled = true;
      if (timer) clearTimeout(timer);
      activeRequest?.abort();
    };
  }, [apiClient, refreshVersion, route.materialId, route.runId]);

  if (runLoad.status === "loading") {
    return <section className="state-page"><div className="loading-ring" /><h1>正在讀取教材狀態</h1></section>;
  }
  if (runLoad.status === "failed") {
    return (
      <section className="state-page failure-page" role="alert">
        <img className="state-illustration" src="/assets/studydy/failure-confused.png" alt="" />
        <h1>無法讀取教材狀態</h1>
        <p>{runLoad.message}</p>
        <div className="button-row">
          {runLoad.canRetry && <button className="primary-button" type="button" onClick={() => setRefreshVersion((value) => value + 1)}>重新查詢</button>}
          <button className="secondary-button" type="button" onClick={() => writeRoute({ name: "home" })}>返回上傳頁</button>
        </div>
      </section>
    );
  }
  if (runLoad.run.status === "pending" || runLoad.run.status === "running") {
    return <ProcessingView run={runLoad.run} pollingStopped={runLoad.pollingStopped} refresh={() => setRefreshVersion((value) => value + 1)} />;
  }
  if (runLoad.run.status === "failed") return <FailureView apiClient={apiClient} run={runLoad.run} />;
  return <OutcomeView run={runLoad.run} />;
}

function KnowledgeMapRoute({ apiClient, route }: {
  apiClient: StudydyApiClient;
  route: Extract<AppRoute, { name: "knowledge-map" }>;
}) {
  const [retryVersion, setRetryVersion] = useState(0);
  const [studyLoad, setStudyLoad] = useState<StudyLoadState>({ status: "loading" });

  // 顯示前先核對處理作業、各輸出版本與概念編號；任何不一致都停止載入，避免混用不同教材的內容。
  useEffect(() => {
    let isCancelled = false;

    const loadStudyView = async () => {
      setStudyLoad({ status: "loading" });
      try {
        const run = await apiClient.getMaterialRun(route.runId);
        if (isCancelled) return;
        const binding = run.output_binding;
        const hasMatchingRun = run.material_id === route.materialId
          && (run.status === "succeeded" || run.status === "partial")
          && binding !== null;
        if (!hasMatchingRun) {
          setStudyLoad({ status: "failed", message: "此網址與教材處理結果不一致，無法安全載入知識地圖。" });
          return;
        }
        if (
          binding.knowledge_map_revision !== route.mapRevision
          || binding.learning_path_revision !== route.pathRevision
        ) {
          setStudyLoad({ status: "failed", message: "網址中的知識地圖版本與這次處理結果不一致。" });
          return;
        }

        const [map, resources] = await Promise.all([
          apiClient.getKnowledgeMap({
            materialId: route.materialId,
            runId: route.runId,
            mapRevision: route.mapRevision,
            pathRevision: route.pathRevision,
          }),
          apiClient.getLearningResourceResult({
            materialId: route.materialId,
            runId: route.runId,
            resultRevision: binding.learning_resource_result_revision,
          }),
        ]);
        if (isCancelled) return;
        const hasMatchingOutputs = map.knowledge_map_revision === route.mapRevision
          && map.learning_path_revision === route.pathRevision
          && resources.result_revision === binding.learning_resource_result_revision
          && resources.source_study_material_output_revision === binding.study_material_output_revision
          && resources.catalog_revision === binding.catalog_revision
          && resources.run_id === route.runId;
        const hasUsableOutputs = map.status.processing !== "failed"
          && map.status.decision !== "reject"
          && resources.processing !== "failed"
          && resources.decision !== "reject";
        const conceptIds = new Set(map.concepts.map((concept) => concept.id));
        const hasKnownResourceConcepts = resources.resources.every((resource) => conceptIds.has(resource.concept_id));
        if (!hasMatchingOutputs || !hasUsableOutputs || !hasKnownResourceConcepts) {
          setStudyLoad({ status: "failed", message: "收到的學習內容版本或概念資料不一致，已停止顯示。" });
          return;
        }
        setStudyLoad({
          status: "ready",
          map,
          resources,
          sourceArtifactId: run.source_artifact_id,
        });
      } catch (error) {
        if (isCancelled) return;
        setStudyLoad({ status: "failed", message: errorMessage(error) });
      }
    };

    void loadStudyView();
    return () => { isCancelled = true; };
  }, [apiClient, retryVersion, route.materialId, route.mapRevision, route.pathRevision, route.runId]);

  const returnToRun = () => writeRoute({
    name: "material-run",
    materialId: route.materialId,
    runId: route.runId,
  });

  if (studyLoad.status === "loading") {
    return (
      <section className="state-page" aria-live="polite">
        <div className="loading-ring" />
        <h1>正在讀取知識地圖</h1>
        <p>正在確認教材與學習內容版本，完成後即會顯示。</p>
      </section>
    );
  }
  if (studyLoad.status === "failed") {
    return (
      <section className="state-page failure-page" role="alert">
        <img className="state-illustration" src="/assets/studydy/failure-confused.png" alt="" />
        <h1>無法載入學習內容</h1>
        <p>{studyLoad.message}</p>
        <div className="button-row">
          <button className="primary-button" type="button" onClick={() => setRetryVersion((value) => value + 1)}>重新載入</button>
          <button className="secondary-button" type="button" onClick={returnToRun}>返回處理結果</button>
        </div>
      </section>
    );
  }
  return (
    <KnowledgeMap
      view={studyLoad.map}
      resourceResult={studyLoad.resources}
      onOpenSourcePdf={async (signal) => {
        const pdf = await apiClient.getSourceArtifact(studyLoad.sourceArtifactId, signal);
        if (!signal.aborted) openPdfInNewTab(pdf);
      }}
      onBack={returnToRun}
    />
  );
}

export function MaterialFlow({ apiClient, route, learningStateWasReplayed }: {
  apiClient: StudydyApiClient;
  route: AppRoute;
  learningStateWasReplayed: boolean;
}) {
  if (route.name === "home") return <HomeUpload apiClient={apiClient} />;
  if (route.name === "material-run") return <MaterialRun apiClient={apiClient} route={route} />;
  if (route.name === "knowledge-map") return <KnowledgeMapRoute apiClient={apiClient} route={route} />;
  if (route.name === "assessment") return <AssessmentRoute apiClient={apiClient} route={route} />;
  return <LearningStateRoute apiClient={apiClient} route={route} wasReplayed={learningStateWasReplayed} />;
}
