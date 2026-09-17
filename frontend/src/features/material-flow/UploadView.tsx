import { useEffect, useRef, useState } from "react";

import { errorMessage, type StudydyApiClient } from "../../api/client";
import { writeRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";
import {
  formatFileSize,
  validatePdfFile,
  validatePdfSelection,
} from "./material-flow";

export function UploadView({ apiClient }: { apiClient: StudydyApiClient }) {
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [phase, setPhase] = useState<"idle" | "uploading" | "creating-run">("idle");
  const isSubmitting = phase !== "idle";
  const receipt = useRef<Awaited<ReturnType<StudydyApiClient["createMaterial"]>> | null>(null);
  const submitting = useRef(false);
  const [isDragging, setIsDragging] = useState(false);
  const dragDepth = useRef(0);
  const fileInput = useRef<HTMLInputElement>(null);
  const uploadKey = useRef(crypto.randomUUID());
  const runKey = useRef(crypto.randomUUID());

  const clearDrag = () => { dragDepth.current = 0; setIsDragging(false); };
  useEffect(() => {
    const cancel = (event: KeyboardEvent) => { if (event.key === "Escape") clearDrag(); };
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
    receipt.current = null;
    uploadKey.current = crypto.randomUUID();
    runKey.current = crypto.randomUUID();
    const selection = validatePdfSelection(files);
    const selectedFile = selection.message === null ? selection.file : null;
    setFile(selectedFile);
    setFileError(selection.message);
    setSubmitError(null);
    if (selectedFile === null && fileInput.current) fileInput.current.value = "";
  };

  const submit = async () => {
    if (submitting.current) return;
    const validation = validatePdfFile(file);
    if (validation || !file) {
      setFileError(file === null ? fileError ?? validation : validation);
      setSubmitError(null);
      return;
    }
    submitting.current = true;
    clearDrag();
    setPhase(receipt.current ? "creating-run" : "uploading");
    setFileError(null);
    setSubmitError(null);
    try {
      // 上傳已成功時保留回執；建立任務重試不重新上傳 PDF。
      const material = receipt.current ?? await apiClient.createMaterial(file, uploadKey.current, file.name);
      receipt.current = material;
      setPhase("creating-run");
      const run = await apiClient.createMaterialRun({
        schema: "material-processing-create/v1",
        material_id: material.material_id,
        source_artifact_id: material.source_artifact_id,
      }, runKey.current);
      writeRoute({ name: "material-run", materialId: run.material_id, runId: run.run_id });
    } catch (error) {
      setSubmitError(errorMessage(error));
      submitting.current = false;
      setPhase("idle");
    }
  };

  return (
    <section className="upload-page task-page" aria-labelledby="upload-title">
      <header className="upload-hero">
        <img src="/assets/studydy/upload-guide.png" alt="" />
        <div>
          <p className="eyebrow">PDF 教材</p>
          <h1 id="upload-title">上傳教材</h1>
          <p>上傳 PDF 教材，Studydy 會整理概念、關係與建議學習順序，並保留可回查的來源。</p>
        </div>
      </header>

      <div className="upload-layout">
        <section className="surface upload-card" aria-label="上傳 PDF 教材">
          <div className="section-heading">
            <div>
              <h2>選擇 PDF 教材</h2>
              <p>選擇一份 PDF 教材，Studydy 會保留可回查的來源頁面。</p>
            </div>
          </div>

          <label
            className={`file-drop${file ? " has-file" : ""}${isSubmitting ? " is-disabled" : ""}${isDragging ? " is-dragging" : ""}`}
            onDragEnter={(event) => {
              event.preventDefault();
              if (!submitting.current && event.dataTransfer.types.includes("Files")) {
                dragDepth.current++;
                setIsDragging(true);
              }
            }}
            onDragOver={(event) => {
              event.preventDefault();
              event.dataTransfer.dropEffect = !submitting.current && event.dataTransfer.types.includes("Files") ? "copy" : "none";
            }}
            onDragLeave={() => {
              dragDepth.current = Math.max(0, dragDepth.current - 1);
              if (dragDepth.current === 0) setIsDragging(false);
            }}
            onDrop={(event) => {
              event.preventDefault();
              clearDrag();
              if (submitting.current) return;
              const folder = Array.from(event.dataTransfer.items).some(item => item.webkitGetAsEntry?.()?.isDirectory);
              if (folder || !event.dataTransfer.types.includes("Files")) {
                chooseFiles(null);
                setFileError(folder ? "不接受資料夾，請選擇一份 PDF。" : "請拖曳一份 PDF 檔案，不接受文字或網址。");
              } else chooseFiles(event.dataTransfer.files);
            }}
          >
            <input
              ref={fileInput}
              type="file"
              accept="application/pdf"
              aria-label="選擇 PDF 教材"
              aria-describedby={fileError ? "upload-file-error" : undefined}
              aria-invalid={fileError ? true : undefined}
              disabled={isSubmitting}
              onClick={(event) => { event.currentTarget.value = ""; }}
              onChange={(event) => { if (event.currentTarget.files?.length) chooseFiles(event.currentTarget.files); }}
            />
            <span className="file-drop__icon"><Icon name="upload" size={28} /></span>
            <strong>{file ? "拖放或點擊以更換 PDF" : "將 PDF 拖放到此處"}</strong>
            {!file && <span>或點擊選擇 PDF · 最大 100 MiB</span>}
          </label>

          {file && (
            <div className="chosen-file" aria-label="已選擇的檔案">
              <span className="file-kind"><Icon name="file" /></span>
              <div>
                <strong>{file.name}</strong>
                <small role="status">{formatFileSize(file.size)} · {phase === "uploading" ? "上傳中" : phase === "creating-run" ? "已上傳，正在建立處理任務" : receipt.current ? "已上傳，可重試建立處理任務" : "準備上傳"}</small>
              </div>
              <button
                className="text-button"
                disabled={isSubmitting}
                type="button"
                onClick={() => {
                  receipt.current = null;
                  clearDrag();
                  setFile(null);
                  setFileError(null);
                  setSubmitError(null);
                  if (fileInput.current) fileInput.current.value = "";
                  uploadKey.current = crypto.randomUUID();
                  runKey.current = crypto.randomUUID();
                }}
              >移除</button>
            </div>
          )}

          {fileError && <p className="form-error" id="upload-file-error" role="alert">{fileError}</p>}
          {submitError && <p className="form-error" id="upload-submit-error" role="alert">{submitError}</p>}
          <button className="primary-button full-button" type="button" disabled={!file || isSubmitting} onClick={submit}>
            <Icon name="upload" size={18} />
            {phase === "uploading" ? "正在上傳…" : phase === "creating-run" ? "正在建立處理任務…" : receipt.current ? "重試建立處理任務" : "上傳並開始分析"}
          </button>
          <p className="privacy-note"><Icon name="lock" size={14} /> 教材由 Studydy 處理，語意分析使用配置的 AI 服務</p>
        </section>

        <aside className="upload-aside" aria-label="教材處理說明">
          <section className="surface guide-card">
            <h2>接下來會發生什麼？</h2>
            <ol>
              <li><span>1</span><div><strong>上傳教材</strong><p>只接受 PDF，並建立獨立處理作業。</p></div></li>
              <li><span>2</span><div><strong>整理概念與來源</strong><p>保留可回查的 PDF 來源頁面。</p></div></li>
              <li><span>3</span><div><strong>建立知識地圖</strong><p>整理概念關係與建議學習順序。</p></div></li>
            </ol>
            <section className="upload-requirements" aria-label="檔案需求">
              <h3>檔案需求</h3>
              <p>PDF（.pdf）· 最大 100 MiB</p>
            </section>
          </section>
        </aside>
      </div>
    </section>
  );
}
