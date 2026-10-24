import { useEffect, useState } from "react";
import { moveImage, removeImage, youtubeProblem, type QueuedSource } from "./source-uploads";

/** 圖片組：依清單順序成為第 1、2、3… 頁，可調整順序或移除單張。 */
export function QueuedImages({
  item,
  disabled,
  onChange,
}: {
  item: QueuedSource;
  disabled: boolean;
  onChange: (next: QueuedSource) => void;
}) {
  const images = item.images ?? [];
  const [previews, setPreviews] = useState<string[]>([]);
  useEffect(() => {
    const urls = images.map((image) => URL.createObjectURL(image));
    setPreviews(urls);
    return () => urls.forEach((url) => URL.revokeObjectURL(url));
  }, [images]);
  return (
    <ol className="queued-images" aria-label="圖片順序（第幾張即第幾頁）">
      {images.map((image, index) => (
        <li key={`${image.name}-${index}`}>
          {previews[index] && <img src={previews[index]} alt="" />}
          <span className="queued-image-name" title={image.name}>
            第 {index + 1} 頁 · {image.name}
          </span>
          <span className="source-reorder">
            <button
              type="button"
              disabled={disabled || index === 0}
              aria-label={`上移 ${image.name}`}
              onClick={() => onChange(moveImage(item, index, -1))}
            >
              ↑
            </button>
            <button
              type="button"
              disabled={disabled || index === images.length - 1}
              aria-label={`下移 ${image.name}`}
              onClick={() => onChange(moveImage(item, index, 1))}
            >
              ↓
            </button>
            <button
              type="button"
              disabled={disabled || images.length === 1}
              aria-label={`移除圖片 ${image.name}`}
              onClick={() => onChange(removeImage(item, index))}
            >
              ×
            </button>
          </span>
        </li>
      ))}
    </ol>
  );
}

/** 貼上單支 YouTube 影片網址；播放清單與明顯不是 YouTube 的網址先在這裡擋下。 */
export function YoutubeSourceInput({
  disabled,
  onAdd,
}: {
  disabled: boolean;
  onAdd: (url: string) => void;
}) {
  const [url, setUrl] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  return (
    <form
      className="youtube-source-input"
      onSubmit={(event) => {
        event.preventDefault();
        const value = url.trim();
        const reason = value ? youtubeProblem(value) : "請貼上 YouTube 影片網址。";
        setProblem(reason);
        if (reason) return;
        onAdd(value);
        setUrl("");
      }}
    >
      <label>
        <span>YouTube 影片網址</span>
        <input
          type="url"
          inputMode="url"
          placeholder="https://www.youtube.com/watch?v=…"
          value={url}
          disabled={disabled}
          aria-describedby={problem ? "youtube-source-problem" : undefined}
          onChange={(event) => setUrl(event.target.value)}
        />
      </label>
      <button className="secondary-button" type="submit" disabled={disabled}>
        加入影片
      </button>
      {problem && (
        <p id="youtube-source-problem" className="form-error" role="alert">
          {problem}
        </p>
      )}
    </form>
  );
}
