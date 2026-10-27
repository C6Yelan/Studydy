import { useEffect, useRef, useState, type ReactNode } from "react";
import type { SourceCapabilities } from "../../api/contracts";
import { Icon } from "../../ui/Icon";
import { YoutubeSourceInput } from "./QueuedSourceParts";
import { formatFileSize } from "./material-flow";

export function SourceUploadPicker({capabilities,disabled,hasFiles,inputLabel,error,errorId,onFiles,onSelectionError,onYoutube,children}:{
  capabilities:SourceCapabilities|null; disabled:boolean; hasFiles:boolean; inputLabel:string; error:string; errorId:string;
  onFiles:(files:FileList|null)=>void|Promise<void>; onSelectionError:(message:string)=>void; onYoutube:(url:string)=>void; children?:ReactNode;
}) {
  const formats = capabilities?.formats ?? [], formatsReady = capabilities !== null;
  const [isDragging,setIsDragging] = useState(false);
  const dragDepth=useRef(0),fileInput=useRef<HTMLInputElement>(null);
  const clearDrag=()=>{dragDepth.current=0;setIsDragging(false);};
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
  useEffect(()=>{if(disabled)clearDrag();},[disabled]);
  return <>
        <label
          className={`file-drop${hasFiles ? " has-file" : ""}${disabled || !formatsReady ? " is-disabled" : ""}${isDragging ? " is-dragging" : ""}`}
          onDragEnter={(event) => {
            event.preventDefault();
            if (!disabled && formatsReady && event.dataTransfer.types.includes("Files")) {
              dragDepth.current++;
              setIsDragging(true);
            }
          }}
          onDragOver={(event) => {
            event.preventDefault();
            event.dataTransfer.dropEffect =
              !disabled && formatsReady && event.dataTransfer.types.includes("Files") ? "copy" : "none";
          }}
          onDragLeave={() => {
            dragDepth.current = Math.max(0, dragDepth.current - 1);
            if (!dragDepth.current) setIsDragging(false);
          }}
          onDrop={(event) => {
            event.preventDefault();
            clearDrag();
            if (disabled || !formatsReady) return;
            const folder = Array.from(event.dataTransfer.items).some(
              (item) => item.webkitGetAsEntry?.()?.isDirectory,
            );
            if (folder || !event.dataTransfer.types.includes("Files"))
              onSelectionError(
                folder ? "不接受資料夾，請選擇教材檔案。" : "請拖曳教材檔案，不接受文字或網址。",
              );
            else void onFiles(event.dataTransfer.files);
          }}
        >
          <input
            ref={fileInput}
            type="file"
            multiple
            accept={formats.map((format) => format.extension).join(",")}
            aria-label={inputLabel}
            aria-describedby={error ? errorId : undefined}
            disabled={disabled || !formatsReady}
            onChange={(event) => {
              void onFiles(event.currentTarget.files);
              // 選取已同步複製到待處理陣列；清空原生值，移除後重選同檔也會觸發 change。
              event.currentTarget.value = "";
            }}
          />
          <span className="file-drop-icon">
            <Icon name="upload" size={26} />
          </span>
          <strong>
            {hasFiles ? "拖放或點擊以加入更多教材" : "將教材拖放到此處，或點擊選取"}
          </strong>
          <span>{formatsReady ? uploadLimitSummary(capabilities!) : "正在載入上傳限制"}</span>
        </label>
        {error && (
          <p id={errorId} className="form-error" role="alert">
            {error}
          </p>
        )}
    {children}
    {capabilities?.youtube && <YoutubeSourceInput disabled={disabled} onAdd={onYoutube}/>}
  </>;
}

/** 依實際能力列出可選格式與上限，避免畫面與後端限制不一致。 */
function uploadLimitSummary(capabilities: SourceCapabilities): string {
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
