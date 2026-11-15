import { useEffect, useRef, useState } from "react";
import { SourceUploadPicker } from "./SourceUploadPicker";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { SourceCapabilities } from "../../api/contracts";
import { writeRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";
import { BackLink } from "../../ui/BackLink";
import { Breadcrumb } from "../../ui/Breadcrumb";
import { FlowFooter, FlowHeader, LearningSteps, uploadSteps } from "./Flow";
import { uploadLimitRows } from "./SourceUploadPicker";
import { formatFileSize } from "./material-flow";
import { QueuedImages } from "./QueuedSourceParts";
import {
  queueFiles,
  queuedName,
  queuedSize,
  uploadQueued,
  type QueuedSource,
} from "./source-uploads";

export function UploadView({ apiClient, materialId }: { apiClient: StudydyApiClient; materialId?: string }) {
  const [capabilities, setCapabilities] = useState<SourceCapabilities | null>(null);
  const [capabilityError, setCapabilityError] = useState<string | null>(null);
  const [queue, setQueue] = useState<QueuedSource[]>([]);
  const [selectionError, setSelectionError] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false);
  const draft = useRef<{ key: string; name: string; id: string | null } | null>(null);
  useEffect(() => {
    let cancelled = false;
    void apiClient.sourceCapabilities().then(
      (value) => {
        if (!cancelled) setCapabilities(value);
      },
      () => {
        if (!cancelled) {
          setCapabilityError("無法載入上傳限制，請重新整理後再試。");
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [apiClient]);
  const chooseFiles = async (files: FileList | null) => {
    if (submitting.current || !files?.length || !capabilities) return;
    const { queue: next, rejected } = await queueFiles(Array.from(files), capabilities, queue);
    // 拒絕的檔案不入列，也不覆蓋合法候選既有的上傳失敗資訊。
    setSelectionError(rejected.join(" "));
    if (next.length !== queue.length || next.some((item, index) => item !== queue[index])) {
      setQueue(next);
      setError(null);
    }
  };
  const updateItem = (key: string, change: Partial<QueuedSource>) =>
    setQueue((previous) =>
      previous.map((value) => (value.key === key ? { ...value, ...change } : value)),
    );
  const submit = async () => {
    if (submitting.current || !queue.length) return;
    submitting.current = true;
    setBusy(true);
    setError(null);
    try {
      // 保留 draft 與逐檔的同一意圖；回應遺失或部分失敗時不重建已成功的來源。
      draft.current ??= {
        key: crypto.randomUUID(),
        name: queue[0].kind === "youtube" ? "YouTube 影片" : (queue[0].kind === "images" ? queue[0].images![0].name : queue[0].file!.name).replace(/\.[^.]+$/, "").slice(0, 40),
        id: materialId ?? null,
      };
      const intent = draft.current;
      intent.id ??= (await apiClient.createDraft(intent.name, intent.key)).material_id;
      let failed = false;
      for (const item of queue.filter((item) => item.status !== "uploaded")) {
        updateItem(item.key, { status: "uploading", error: null, progress: null });
        try {
          await uploadQueued(
            apiClient,
            intent.id,
            item,
            capabilities!,
            (progress) => updateItem(item.key, { progress }),
            (packed) => updateItem(item.key, { packed }),
          );
          updateItem(item.key, { status: "uploaded", error: null, progress: null });
        } catch (failure) {
          failed = true;
          updateItem(item.key, { status: "failed", error: errorMessage(failure), progress: null });
        }
      }
      if (!failed) writeRoute({ name: "material-sources", materialId: intent.id });
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  };
  const hasNonPdf = queue.some(
    (item) => item.kind !== "file" || !item.file!.name.toLowerCase().endsWith(".pdf"),
  );
  const uploadLabel = busy
    ? "正在上傳…"
    : queue.every((item) => item.status === "uploaded") && queue.length
      ? "繼續確認來源"
      : queue.some((item) => item.status === "failed")
        ? "重試未完成的上傳"
        : "上傳並確認來源";
  return (
    <section className="flow-page upload-page task-page" aria-labelledby="upload-title">
      {materialId && <Breadcrumb current="新增來源" parent={{ label: "原始檔案", ariaLabel: "返回原始檔案", onClick: () => writeRoute({ name: "material-sources", materialId }) }} />}
      <FlowHeader
        headingId="upload-title"
        title={materialId ? "幫這座島加新材料" : "新增一座島"}
        description={materialId ? "選擇文件、圖片、音訊、影片或 YouTube 影片，確認來源後再開始分析。" : "放入文件、圖片、音訊、影片或 YouTube 影片，確認後就開始建立知識地圖。"}
        back={materialId ? undefined : <BackLink label="回到我的群島" route={{ name: "materials" }} />}
      />
      <LearningSteps labels={materialId ? [...uploadSteps.slice(0, 2), "更新知識地圖"] : uploadSteps} current={0} />
      <div className="upload-layout">
        <div className="upload-main">
          <SourceUploadPicker capabilities={capabilities} disabled={busy} hasFiles={queue.length > 0}
            inputLabel={materialId ? "選擇新增教材" : "選擇教材檔案"} error={selectionError} errorId={materialId ? "source-selection-error" : "upload-selection-error"}
            onFiles={chooseFiles} onSelectionError={setSelectionError}
            onYoutube={url => {setQueue(previous => [...previous, {key:crypto.randomUUID(),kind:"youtube",status:"pending",error:null,progress:null,url}]);setError(null);}}>
            {queue.length > 0 && (
            <ul className="chosen-files" aria-label="待上傳教材">
            {queue.map((item) => (
              <li className={`chosen-file is-${item.status}`} aria-label={`已選擇 ${queuedName(item)}`} key={item.key}>
                <span className="chosen-file-icon">
                  <Icon name={item.status === "uploaded" ? "check" : item.status === "failed" ? "warning" : "file"} />
                </span>
                <div className="chosen-file-body">
                  <strong title={queuedName(item)}>{queuedName(item)}</strong>
                  <small role="status">
                    {item.kind === "youtube" ? "YouTube 影片" : formatFileSize(queuedSize(item))} ·{" "}
                    {item.status === "uploading" && item.progress !== null
                      ? `上傳中 ${Math.floor(item.progress * 100)}%`
                      : {
                          pending: "準備上傳",
                          uploading: "上傳中",
                          uploaded: "已上傳",
                          failed: "上傳失敗，可重試",
                        }[item.status]}
                  </small>
                  {item.kind === "images" && (
                    <QueuedImages
                      item={item}
                      disabled={busy || item.status === "uploaded"}
                      onChange={(next) =>
                        setQueue((previous) => previous.map((value) => (value.key === item.key ? next : value)))
                      }
                    />
                  )}
                  {item.error && (
                    <p className="form-error" role="alert">
                      {item.error}
                    </p>
                  )}
                </div>
                <button
                  className="flow-icon-button"
                  type="button"
                  disabled={busy || item.status === "uploaded"}
                  aria-label={`移除 ${queuedName(item)}`}
                  title="移除"
                  onClick={() => {
                    setQueue((previous) => previous.filter((value) => value.key !== item.key));
                    setError(null);
                  }}
                >
                  <Icon name="close" size={18} />
                </button>
              </li>
            ))}
          </ul>
            )}
          </SourceUploadPicker>
          {capabilityError && (
            <p className="flow-hint" role="status">
              {capabilityError}
            </p>
          )}
          {hasNonPdf && (
            <p className="flow-notice">非 PDF 教材會先轉換為 PDF；音訊、影片與 YouTube 會轉成逐字稿。請在下一步確認轉換內容。</p>
          )}
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
        </div>
        <aside className="upload-side">
          <section className="upload-side-card" aria-labelledby="upload-types-title">
            <h2 id="upload-types-title">可以放哪些檔案</h2>
            {capabilities ? <div className="upload-types">{uploadLimitRows(capabilities).map(([label, names, note]) =>
              <div key={label}><b>{label}</b><span>{names}{note && <small> · {note}</small>}</span></div>)}</div>
              : <p className="flow-hint">正在載入上傳限制</p>}
          </section>
          <section className="upload-side-card" aria-labelledby="upload-next-title">
            <h2 id="upload-next-title">接下來會發生什麼</h2>
            <ol className="upload-next">
              <li><b>2</b><span>確認教材<small>預覽轉換後的 PDF，調整多份教材的順序</small></span></li>
              <li><b>3</b><span>{materialId ? "更新知識地圖" : "建立知識地圖"}<small>辨識文字、保留頁碼，整理概念、關係與建議順序</small></span></li>
            </ol>
          </section>
        </aside>
      </div>
      <FlowFooter
        className="upload-footer"
        secondary={
          queue.length > 0 ? (
            <span>
              共 {queue.length} 份 · {formatFileSize(queue.reduce((total, item) => total + queuedSize(item), 0))}
            </span>
          ) : (
            <span className="upload-disabled-hint">先選擇或拖入教材檔案，按鈕就能使用。</span>
          )
        }
        primary={
          <button className="primary-button" disabled={!queue.length || busy} onClick={() => void submit()}>
            <Icon name="upload" size={18} />
            {uploadLabel}
          </button>
        }
      />
    </section>
  );
}
