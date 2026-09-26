import { useEffect, useRef, useState } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { FormatCapability } from "../../api/contracts";
import { writeRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";
import { formatFileSize, validateSourceFile } from "./material-flow";

type QueuedFile = {
  file: File;
  key: string;
  status: "pending" | "uploading" | "uploaded" | "failed";
  error: string | null;
};

export function UploadView({ apiClient }: { apiClient: StudydyApiClient }) {
  const [formats, setFormats] = useState<FormatCapability[]>([
    { extension: ".pdf", media_type: "application/pdf", max_bytes: 104857600 },
  ]);
  const [formatsReady, setFormatsReady] = useState(false);
  const [capabilityError, setCapabilityError] = useState<string | null>(null);
  const [queue, setQueue] = useState<QueuedFile[]>([]);
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
        if (!cancelled) {
          setFormats(value.formats);
          setFormatsReady(true);
        }
      },
      () => {
        if (!cancelled) {
          setCapabilityError("其他格式目前無法載入，仍可上傳 PDF。");
          setFormatsReady(true);
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
  const chooseFiles = (files: FileList | null) => {
    if (submitting.current || !files?.length) return;
    if (!formatsReady) {
      setSelectionError("請待支援格式載入後重新選擇教材。");
      return;
    }
    const additions: QueuedFile[] = [];
    const rejected: string[] = [];
    for (const file of Array.from(files)) {
      const problem = validateSourceFile(file, formats);
      if (problem) rejected.push(`「${file.name}」：${problem}`);
      else additions.push({ file, key: crypto.randomUUID(), status: "pending", error: null });
    }
    // 拒絕的檔案不入列，也不覆蓋合法候選既有的上傳失敗資訊。
    setSelectionError(rejected.join(" "));
    if (additions.length) {
      setQueue((previous) => [...previous, ...additions]);
      setError(null);
    }
  };
  const submit = async () => {
    if (submitting.current || !queue.length) return;
    submitting.current = true;
    setBusy(true);
    setError(null);
    clearDrag();
    try {
      // 保留 draft 與逐檔的同一意圖；回應遺失或部分失敗時不重建已成功的來源。
      draft.current ??= { key: crypto.randomUUID(), name: queue[0].file.name, id: null };
      const intent = draft.current;
      intent.id ??= (await apiClient.createDraft(intent.name, intent.key)).material_id;
      let failed = false;
      for (const item of queue.filter((item) => item.status !== "uploaded")) {
        setQueue((previous) =>
          previous.map((value) =>
            value.key === item.key ? { ...value, status: "uploading", error: null } : value,
          ),
        );
        try {
          const format = formats.find((value) =>
            item.file.name.toLowerCase().endsWith(value.extension),
          )!;
          await apiClient.uploadSource(intent.id, item.file, format.media_type, item.key);
          setQueue((previous) =>
            previous.map((value) =>
              value.key === item.key ? { ...value, status: "uploaded", error: null } : value,
            ),
          );
        } catch (failure) {
          failed = true;
          setQueue((previous) =>
            previous.map((value) =>
              value.key === item.key
                ? { ...value, status: "failed", error: errorMessage(failure) }
                : value,
            ),
          );
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
  const hasNonPdf = queue.some(({ file }) =>
    formats.some(
      (format) => format.extension !== ".pdf" && file.name.toLowerCase().endsWith(format.extension),
    ),
  );
  return (
    <section className="upload-page task-page" aria-labelledby="upload-title">
      <header className="upload-hero">
        <img src="/assets/studydy/upload-guide.png" alt="" />
        <div>
          <p className="eyebrow">學習教材</p>
          <h1 id="upload-title">上傳教材</h1>
          <p>選擇一份或多份教材，確認來源後再開始分析。</p>
        </div>
      </header>
      <div className="upload-layout">
        <section className="surface upload-card" aria-label="上傳教材檔案">
          <label
            className={`file-drop${queue.length ? " has-file" : ""}${busy || !formatsReady ? " is-disabled" : ""}${isDragging ? " is-dragging" : ""}`}
            onDragEnter={(event) => {
              event.preventDefault();
              if (!submitting.current && event.dataTransfer.types.includes("Files")) {
                dragDepth.current++;
                setIsDragging(true);
              }
            }}
            onDragOver={(event) => {
              event.preventDefault();
              event.dataTransfer.dropEffect =
                !submitting.current && event.dataTransfer.types.includes("Files") ? "copy" : "none";
            }}
            onDragLeave={() => {
              dragDepth.current = Math.max(0, dragDepth.current - 1);
              if (!dragDepth.current) setIsDragging(false);
            }}
            onDrop={(event) => {
              event.preventDefault();
              clearDrag();
              if (submitting.current) return;
              const folder = Array.from(event.dataTransfer.items).some(
                (item) => item.webkitGetAsEntry?.()?.isDirectory,
              );
              if (folder || !event.dataTransfer.types.includes("Files"))
                setSelectionError(
                  folder ? "不接受資料夾，請選擇教材檔案。" : "請拖曳教材檔案，不接受文字或網址。",
                );
              else chooseFiles(event.dataTransfer.files);
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
              onClick={(event) => {
                event.currentTarget.value = "";
              }}
              onChange={(event) => chooseFiles(event.currentTarget.files)}
            />
            <span className="file-drop__icon">
              <Icon name="upload" size={28} />
            </span>
            <strong>
              {queue.length ? "拖放或點擊以加入更多教材" : "將教材拖放到此處，或點擊選取"}
            </strong>
            <span>
              {formats.map((format) => format.extension.slice(1).toUpperCase()).join("、")} ·
              可選多份 · 每份最大 100 MiB
            </span>
          </label>
          {selectionError && (
            <p id="upload-selection-error" className="form-error" role="alert">
              {selectionError}
            </p>
          )}
          {queue.map((item) => {
            return (
              <div className="chosen-file" aria-label={`已選擇 ${item.file.name}`} key={item.key}>
                <span className="file-kind">
                  <Icon name="file" />
                </span>
                <div>
                  <strong>{item.file.name}</strong>
                  <small role="status">
                    {formatFileSize(item.file.size)} ·{" "}
                    {
                      {
                        pending: "準備上傳",
                        uploading: "上傳中",
                        uploaded: "已上傳",
                        failed: "上傳失敗，可重試",
                      }[item.status]
                    }
                  </small>
                  {item.error && (
                    <p className="form-error" role="alert">
                      {item.error}
                    </p>
                  )}
                </div>
                <button
                  className="text-button"
                  disabled={busy || item.status === "uploaded"}
                  aria-label={`移除 ${item.file.name}`}
                  onClick={() => {
                    setQueue((previous) => previous.filter((value) => value.key !== item.key));
                    setError(null);
                  }}
                >
                  移除
                </button>
              </div>
            );
          })}
          {queue.length > 0 && (
            <p className="conversion-note">
              共 {queue.length} 份 ·{" "}
              {formatFileSize(queue.reduce((total, item) => total + item.file.size, 0))}
            </p>
          )}
          {capabilityError && (
            <p className="conversion-note" role="status">
              {capabilityError}
            </p>
          )}
          {hasNonPdf && (
            <p className="conversion-note">非 PDF 教材會先轉換為 PDF，請在下一步確認轉換內容。</p>
          )}
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
          <button
            className="primary-button full-button"
            disabled={!queue.length || busy}
            onClick={() => void submit()}
          >
            <Icon name="upload" size={18} />
            {busy
              ? "正在上傳…"
              : queue.every((item) => item.status === "uploaded") && queue.length
                ? "繼續確認來源"
                : queue.some((item) => item.status === "failed")
                  ? "重試未完成的上傳"
                  : "上傳並確認來源"}
          </button>
        </section>
        <aside className="upload-aside" aria-label="教材處理說明">
          <section className="surface guide-card">
            <h2>Studydy 如何處理教材</h2>
            <ol>
              <li>
                <span>1</span>
                <div>
                  <strong>準備教材</strong>
                  <p>各份來源分開保留；非 PDF 教材會先轉換為可分析的 PDF。</p>
                </div>
              </li>
              <li>
                <span>2</span>
                <div>
                  <strong>整理內容與來源</strong>
                  <p>讀取教材內容，需要時辨識圖片中的文字，並保留可回查的原始來源。</p>
                </div>
              </li>
              <li>
                <span>3</span>
                <div>
                  <strong>建立知識結構</strong>
                  <p>從教材整理重要概念、概念關係與建議學習順序。</p>
                </div>
              </li>
              <li>
                <span>4</span>
                <div>
                  <strong>建立知識地圖</strong>
                  <p>將整理結果呈現在知識地圖中，之後可探索概念並回查教材來源。</p>
                </div>
              </li>
            </ol>
          </section>
        </aside>
      </div>
    </section>
  );
}
