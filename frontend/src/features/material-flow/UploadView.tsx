import { useRef, useState } from "react";

import { errorMessage, type StudydyApiClient } from "../../api/client";
import { writeRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";
import { formatFileSize, validatePdfFile } from "./material-flow";

export function UploadView({ apiClient }: { apiClient: StudydyApiClient }) {
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
      const run = await apiClient.createMaterialRun({
        schema: "material-processing-create/v2",
        material_id: material.material_id,
        source_artifact_id: material.source_artifact_id,
      }, runKey.current);
      writeRoute({ name: "material-run", materialId: run.material_id, runId: run.run_id });
    } catch (error) {
      setMessage(errorMessage(error));
      setIsSubmitting(false);
    }
  };

  return (
    <section className="upload-page" aria-labelledby="upload-title">
      <header className="upload-hero">
        <img src="/assets/studydy/upload-guide.png" alt="" />
        <div>
          <p className="eyebrow">PDF 教材</p>
          <h1 id="upload-title">上傳學習資料</h1>
          <p>上傳完整 PDF，Studydy 會整理可回查的概念、關係與教材建議學習順序。</p>
        </div>
      </header>

      <div className="upload-layout">
        <section className="surface upload-card" aria-label="上傳 PDF 教材">
          <div className="section-heading">
            <span className="step-number">1</span>
            <div>
              <h2>選擇 PDF 教材</h2>
              <p>系統處理完整教材，並保留可回查的來源頁面。</p>
            </div>
          </div>

          <label className={`file-drop${file ? " has-file" : ""}${isSubmitting ? " is-disabled" : ""}`}>
            <input
              type="file"
              accept="application/pdf"
              aria-label="選擇 PDF 教材"
              aria-describedby={message ? "upload-error" : undefined}
              aria-invalid={message ? true : undefined}
              disabled={isSubmitting}
              onChange={(event) => {
                const next = event.currentTarget.files?.[0] ?? null;
                uploadKey.current = crypto.randomUUID();
                runKey.current = crypto.randomUUID();
                setFile(next);
                setMessage(validatePdfFile(next));
              }}
            />
            <span className="file-drop__icon"><Icon name="upload" size={28} /></span>
            <strong>{file ? "更換 PDF 教材" : "將 PDF 拖放到此處"}</strong>
            <span>或點擊選擇檔案 · 最大 100 MiB</span>
          </label>

          {file && (
            <div className="chosen-file" aria-label="已選擇的檔案">
              <span className="file-kind"><Icon name="file" /></span>
              <div>
                <strong>{file.name}</strong>
                <small>{formatFileSize(file.size)} · {message ? "需要修正" : "準備上傳"}</small>
              </div>
              <button
                className="text-button"
                disabled={isSubmitting}
                type="button"
                onClick={() => {
                  setFile(null);
                  setMessage(null);
                  uploadKey.current = crypto.randomUUID();
                  runKey.current = crypto.randomUUID();
                }}
              >移除</button>
            </div>
          )}

          {message && <p className="form-error" id="upload-error" role="alert">{message}</p>}
          <button className="primary-button full-button" type="button" disabled={isSubmitting} onClick={submit}>
            <Icon name="upload" size={18} />
            {isSubmitting ? "正在建立處理作業…" : "上傳並分析完整教材"}
          </button>
          <p className="privacy-note"><Icon name="lock" size={14} /> 教材只交由本機 Studydy 流程處理</p>
        </section>

        <aside className="upload-aside" aria-label="教材處理說明">
          <section className="surface guide-card">
            <h2>接下來會發生什麼？</h2>
            <ol>
              <li><span>1</span><div><strong>安全接收教材</strong><p>只接受 PDF，並建立獨立處理作業。</p></div></li>
              <li><span>2</span><div><strong>整理概念與證據</strong><p>只發布能回到原始 PDF 頁面的內容。</p></div></li>
              <li><span>3</span><div><strong>建立知識地圖</strong><p>顯示概念連結與教材建議學習順序。</p></div></li>
            </ol>
            <img src="/assets/studydy/welcome-wave.png" alt="" />
          </section>
          <section className="surface requirements-card">
            <h2>檔案需求</h2>
            <p><strong>格式</strong><span>僅支援 PDF</span></p>
            <p><strong>檔案大小</strong><span>上限 100 MiB</span></p>
          </section>
        </aside>
      </div>
    </section>
  );
}
