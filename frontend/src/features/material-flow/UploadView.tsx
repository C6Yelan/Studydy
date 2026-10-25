import { useEffect, useRef, useState } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { SourceCapabilities } from "../../api/contracts";
import { writeRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";
import { FlowFooter, FlowHeader, FlowStage, LearningSteps, uploadSteps } from "./Flow";
import { formatFileSize } from "./material-flow";
import { QueuedImages, YoutubeSourceInput } from "./QueuedSourceParts";
import {
  queueFiles,
  queuedName,
  queuedSize,
  uploadQueued,
  type QueuedSource,
} from "./source-uploads";

export function UploadView({ apiClient }: { apiClient: StudydyApiClient }) {
  const [capabilities, setCapabilities] = useState<SourceCapabilities | null>(null);
  const formats = capabilities?.formats ?? [];
  const formatsReady = capabilities !== null;
  const [capabilityError, setCapabilityError] = useState<string | null>(null);
  const [queue, setQueue] = useState<QueuedSource[]>([]);
  const [selectionError, setSelectionError] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false);
  const draft = useRef<{ key: string; name: string; id: string | null } | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const dragDepth = useRef(0);
  const fileInput = useRef<HTMLInputElement>(null);
  const clearDrag = () => {
    dragDepth.current = 0;
    setIsDragging(false);
  };
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
  useEffect(() => {
    const cancel = (event: KeyboardEvent) => {
      if (event.key === "Escape") clearDrag();
    };
    const input = fileInput.current;
    input?.addEventListener("cancel", clearDrag);
    window.addEventListener("keydown", cancel);
    window.addEventListener("drop", clearDrag);
    window.addEventListener("dragend", clearDrag);
    window.addEventListener("blur", clearDrag);
    return () => {
      input?.removeEventListener("cancel", clearDrag);
      window.removeEventListener("keydown", cancel);
      window.removeEventListener("drop", clearDrag);
      window.removeEventListener("dragend", clearDrag);
      window.removeEventListener("blur", clearDrag);
    };
  }, []);
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
    clearDrag();
    try {
      // 保留 draft 與逐檔的同一意圖；回應遺失或部分失敗時不重建已成功的來源。
      draft.current ??= {
        key: crypto.randomUUID(),
        name: queue[0].kind === "youtube" ? "YouTube 影片" : queuedName(queue[0]),
        id: null,
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
      <FlowHeader
        headingId="upload-title"
        title="上傳教材"
        description="選擇文件、圖片、音訊、影片或 YouTube 影片，確認來源後再開始分析。"
        image="/assets/studydy/upload-guide.png"
      />
      <LearningSteps labels={uploadSteps} current={0} />
      <FlowStage
        icon="upload"
        title="選擇教材檔案"
        description="各份檔案分開保留；多張圖片會合成一份、依順序成為各頁；音訊與影片會在本機轉成逐字稿。"
        label="上傳教材檔案"
        footer={
          <FlowFooter
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
        }
      >
        <div className="upload-stage-body">
        <label
          className={`file-drop${queue.length ? " has-file" : ""}${busy || !formatsReady ? " is-disabled" : ""}${isDragging ? " is-dragging" : ""}`}
          onDragEnter={(event) => {
            event.preventDefault();
            if (!submitting.current && formatsReady && event.dataTransfer.types.includes("Files")) {
              dragDepth.current++;
              setIsDragging(true);
            }
          }}
          onDragOver={(event) => {
            event.preventDefault();
            event.dataTransfer.dropEffect =
              !submitting.current && formatsReady && event.dataTransfer.types.includes("Files") ? "copy" : "none";
          }}
          onDragLeave={() => {
            dragDepth.current = Math.max(0, dragDepth.current - 1);
            if (!dragDepth.current) setIsDragging(false);
          }}
          onDrop={(event) => {
            event.preventDefault();
            clearDrag();
            if (submitting.current || !formatsReady) return;
            const folder = Array.from(event.dataTransfer.items).some(
              (item) => item.webkitGetAsEntry?.()?.isDirectory,
            );
            if (folder || !event.dataTransfer.types.includes("Files"))
              setSelectionError(
                folder ? "不接受資料夾，請選擇教材檔案。" : "請拖曳教材檔案，不接受文字或網址。",
              );
            else void chooseFiles(event.dataTransfer.files);
          }}
        >
          <input
            ref={fileInput}
            type="file"
            multiple
            accept={formats.map((format) => format.extension).join(",")}
            aria-label="選擇教材檔案"
            aria-describedby={selectionError ? "upload-selection-error" : undefined}
            disabled={busy || !formatsReady}
            onChange={(event) => {
              void chooseFiles(event.currentTarget.files);
              // 選取已同步複製到待處理陣列；清空原生值，移除後重選同檔也會觸發 change。
              event.currentTarget.value = "";
            }}
          />
          <span className="file-drop-icon">
            <Icon name="upload" size={26} />
          </span>
          <strong>
            {queue.length ? "拖放或點擊以加入更多教材" : "將教材拖放到此處，或點擊選取"}
          </strong>
          <span>{formatsReady ? uploadLimitSummary(capabilities!) : "正在載入上傳限制"}</span>
        </label>
        {selectionError && (
          <p id="upload-selection-error" className="form-error" role="alert">
            {selectionError}
          </p>
        )}
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
        {capabilities?.youtube && (
          <YoutubeSourceInput
            disabled={busy}
            onAdd={(url) => {
              setQueue((previous) => [
                ...previous,
                { key: crypto.randomUUID(), kind: "youtube", status: "pending", error: null, progress: null, url },
              ]);
              setError(null);
            }}
          />
        )}
        {capabilityError && (
          <p className="flow-hint" role="status">
            {capabilityError}
          </p>
        )}
        {hasNonPdf && (
          <p className="flow-notice">非 PDF 教材會先轉換為 PDF；音訊、影片與 YouTube 會轉成約 2 分鐘一段的逐字稿。請在下一步確認轉換內容。</p>
        )}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        </div>
        <ol className="flow-steps-preview" aria-label="上傳後的處理">
          <li>
            <span><Icon name="file" size={18} /></span>
            <div>
              <strong>確認教材</strong>
              <p>預覽轉換後的 PDF，調整多份教材的順序。</p>
            </div>
          </li>
          <li>
            <span><Icon name="process" size={18} /></span>
            <div>
              <strong>整理內容與來源</strong>
              <p>需要時辨識圖片中的文字，保留可回查的頁碼。</p>
            </div>
          </li>
          <li>
            <span><Icon name="map" size={18} /></span>
            <div>
              <strong>建立知識地圖</strong>
              <p>整理重要概念、概念關係與建議學習順序。</p>
            </div>
          </li>
        </ol>
      </FlowStage>
    </section>
  );
}

/** 依實際能力列出可選格式與上限，避免畫面與後端限制不一致。 */
export function uploadLimitSummary(capabilities: SourceCapabilities): string {
  const groups: [string, string][] = [
    ["document", "文件"],
    ["image", "圖片"],
    ["audio", "音訊"],
    ["video", "影片"],
  ];
  const parts = groups.flatMap(([kind, label]) => {
    const formats = capabilities.formats.filter((format) => format.kind === kind);
    if (!formats.length) return [];
    const names = [...new Set(formats.map((format) => format.extension.slice(1).toUpperCase().replace("JPEG", "JPG")))].join("、");
    const max = formatFileSize(Math.max(...formats.map((format) => format.max_bytes)));
    const note = kind === "image" ? `一次最多 ${capabilities.max_images} 張、合計 ${max}` : `每份最大 ${max}`;
    return [`${label} ${names}（${note}）`];
  });
  const hours = capabilities.max_media_seconds / 3600;
  const media = capabilities.formats.some((format) => format.kind === "audio" || format.kind === "video");
  return [...parts, ...(media ? [`音訊／影片最長 ${hours} 小時`] : []), "可選多份"].join(" · ");
}
