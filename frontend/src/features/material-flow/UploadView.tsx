import { useEffect, useRef, useState } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { FormatCapability } from "../../api/contracts";
import { writeRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";
import { formatFileSize, validateSourceFile } from "./material-flow";

type QueuedFile = { file: File; key: string; status: "pending" | "uploading" | "uploaded" | "failed"; error: string | null };

export function UploadView({ apiClient }: { apiClient: StudydyApiClient }) {
  const [formats, setFormats] = useState<FormatCapability[]>([{ extension: ".pdf", media_type: "application/pdf", max_bytes: 104857600 }]);
  const [capabilityError, setCapabilityError] = useState<string | null>(null);
  const [queue, setQueue] = useState<QueuedFile[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false);
  const draft = useRef<{ key: string; name: string; id: string | null } | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const dragDepth = useRef(0);
  const fileInput = useRef<HTMLInputElement>(null);
  const clearDrag = () => { dragDepth.current = 0; setIsDragging(false); };
  useEffect(() => {
    let cancelled = false;
    void apiClient.sourceCapabilities().then(value => { if (!cancelled) setFormats(value.formats); }, () => {
      if (!cancelled) setCapabilityError("其他格式目前無法載入，仍可上傳 PDF。");
    });
    return () => { cancelled = true; };
  }, [apiClient]);
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
    if (submitting.current || !files?.length) return;
    const additions = Array.from(files).map(file => ({ file, key: crypto.randomUUID(), status: "pending" as const, error: null }));
    setQueue(previous => [...previous, ...additions]);
    setError(null);
  };
  const invalid = queue.some(item => validateSourceFile(item.file, formats));
  const submit = async () => {
    if (submitting.current || !queue.length || invalid) return;
    submitting.current = true; setBusy(true); setError(null); clearDrag();
    try {
      // 保留 draft 與逐檔的同一意圖；回應遺失或部分失敗時不重建已成功的來源。
      draft.current ??= { key: crypto.randomUUID(), name: queue[0].file.name, id: null };
      const intent = draft.current;
      intent.id ??= (await apiClient.createDraft(intent.name, intent.key)).material_id;
      let failed = false;
      for (const item of queue.filter(item => item.status !== "uploaded")) {
        setQueue(previous => previous.map(value => value.key === item.key ? { ...value, status: "uploading", error: null } : value));
        try {
          const format = formats.find(value => item.file.name.toLowerCase().endsWith(value.extension))!;
          await apiClient.uploadSource(intent.id, item.file, format.media_type, item.key);
          setQueue(previous => previous.map(value => value.key === item.key ? { ...value, status: "uploaded", error: null } : value));
        } catch (failure) {
          failed = true;
          setQueue(previous => previous.map(value => value.key === item.key ? { ...value, status: "failed", error: errorMessage(failure) } : value));
        }
      }
      if (!failed) writeRoute({ name: "material-sources", materialId: intent.id });
    } catch (failure) { setError(errorMessage(failure)); }
    finally { submitting.current = false; setBusy(false); }
  };
  const otherFormats = formats.filter(format => format.extension !== ".pdf");
  return <section className="upload-page task-page" aria-labelledby="upload-title">
    <header className="upload-hero"><img src="/assets/studydy/upload-guide.png" alt="" /><div>
      <p className="eyebrow">學習教材</p><h1 id="upload-title">上傳教材</h1>
      <p>選擇一份或多份教材，一起整理概念、關係與學習順序，並保留各份來源。</p>
    </div></header>
    <div className="upload-layout"><section className="surface upload-card" aria-label="上傳教材檔案">
      <div className="section-heading"><div><h2>選擇教材</h2><p>可混合支援的格式，上傳後先預覽並確認分析清單。</p></div></div>
      <label className={`file-drop${queue.length ? " has-file" : ""}${busy ? " is-disabled" : ""}${isDragging ? " is-dragging" : ""}`}
        onDragEnter={event => { event.preventDefault(); if (!submitting.current && event.dataTransfer.types.includes("Files")) { dragDepth.current++; setIsDragging(true); } }}
        onDragOver={event => { event.preventDefault(); event.dataTransfer.dropEffect = !submitting.current && event.dataTransfer.types.includes("Files") ? "copy" : "none"; }}
        onDragLeave={() => { dragDepth.current = Math.max(0, dragDepth.current - 1); if (!dragDepth.current) setIsDragging(false); }}
        onDrop={event => {
          event.preventDefault(); clearDrag(); if (submitting.current) return;
          const folder = Array.from(event.dataTransfer.items).some(item => item.webkitGetAsEntry?.()?.isDirectory);
          if (folder || !event.dataTransfer.types.includes("Files")) setError(folder ? "不接受資料夾，請選擇教材檔案。" : "請拖曳教材檔案，不接受文字或網址。");
          else chooseFiles(event.dataTransfer.files);
        }}>
        <input ref={fileInput} type="file" multiple accept={formats.map(format => format.extension).join(",")} aria-label="選擇教材檔案" disabled={busy}
          onClick={event => { event.currentTarget.value = ""; }} onChange={event => chooseFiles(event.currentTarget.files)} />
        <span className="file-drop__icon"><Icon name="upload" size={28} /></span>
        <strong>{queue.length ? "拖放或點擊以加入更多教材" : "將教材拖放到此處（建議 PDF）"}</strong>
        {!queue.length && <span>可選擇多份檔案 · 每份最大 100 MiB</span>}
      </label>
      {queue.map(item => {
        const problem = validateSourceFile(item.file, formats) ?? item.error;
        return <div className="chosen-file" aria-label={`已選擇 ${item.file.name}`} key={item.key}>
          <span className="file-kind"><Icon name="file" /></span><div><strong>{item.file.name}</strong>
            <small role="status">{formatFileSize(item.file.size)} · {{ pending: "準備上傳", uploading: "上傳中", uploaded: "已上傳", failed: "上傳失敗，可重試" }[item.status]}</small>
            {problem && <p className="form-error" role="alert">{problem}</p>}
          </div>
          <button className="text-button" disabled={busy || item.status === "uploaded"} aria-label={`移除 ${item.file.name}`} onClick={() => { setQueue(previous => previous.filter(value => value.key !== item.key)); setError(null); }}>移除</button>
        </div>;
      })}
      {queue.length > 0 && <p className="conversion-note">共 {queue.length} 份 · {formatFileSize(queue.reduce((total, item) => total + item.file.size, 0))}。已上傳檔案可在下一頁預覽、排序或移除。</p>}
      {capabilityError && <p className="conversion-note" role="status">{capabilityError}</p>}
      {otherFormats.length > 0 && <p className="conversion-note">建議優先上傳 PDF。其他支援格式（{otherFormats.map(format => format.extension.slice(1).toUpperCase()).join("、")}）會自動轉為 PDF，轉換品質不保證，請檢查轉換後內容。</p>}
      {error && <p className="form-error" role="alert">{error}</p>}
      <button className="primary-button full-button" disabled={!queue.length || invalid || busy} onClick={() => void submit()}><Icon name="upload" size={18} />{busy ? "正在上傳…" : queue.every(item => item.status === "uploaded") && queue.length ? "繼續確認來源" : queue.some(item => item.status === "failed") ? "重試未完成的上傳" : "上傳並確認來源"}</button>
      <p className="privacy-note"><Icon name="lock" size={14} />教材由 Studydy 處理，確認來源後才會使用配置的 AI 服務分析</p>
    </section><aside className="upload-aside" aria-label="教材處理說明"><section className="surface guide-card">
      <h2>接下來會發生什麼？</h2><ol>
        <li><span>1</span><div><strong>上傳教材</strong><p>各份檔案分開保存，顯示上傳與轉換狀態。</p></div></li>
        <li><span>2</span><div><strong>確認來源</strong><p>預覽 PDF、調整順序，再確認要分析的清單。</p></div></li>
        <li><span>3</span><div><strong>建立知識地圖</strong><p>將選定教材整理成一張地圖，之後可繼續追加。</p></div></li>
      </ol><section className="upload-requirements" aria-label="檔案需求"><h3>檔案需求</h3><p>{formats.map(format => format.extension.slice(1).toUpperCase()).join("、")} · 每份最大 100 MiB</p></section>
    </section></aside></div>
  </section>;
}
