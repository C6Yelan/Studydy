import { useEffect, useId, useRef, useState } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { PodcastSummary } from "../../api/contracts";
import { InlineNameEditor } from "../material-flow/InlineNameEditor";
import { useDismissibleMenu } from "../material-flow/useDismissibleMenu";

export function PodcastManagement({ item, apiClient, onChanged, onDeleted }: {
  item: PodcastSummary; apiClient: StudydyApiClient;
  onChanged: () => void; onDeleted: () => void;
}) {
  const [mode, setMode] = useState<"rename" | "delete" | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const menu = useRef<HTMLDetailsElement>(null);
  const opener = useRef<HTMLElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const interacted = useRef(false);
  const active = useRef(false), inFlight = useRef(false);
  const titleId = useId();
  useDismissibleMenu(menu, opener);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    if (!interacted.current) return;
    if (mode === "rename") { input.current?.focus(); input.current?.select(); }
    else if (mode === "delete") dialog.current?.showModal();
    else opener.current?.focus({ preventScroll: true });
  }, [mode]);
  const closeRename = () => { if (!inFlight.current) { setError(null); setMode(null); } };
  const choose = (next: "rename" | "delete") => {
    if (menu.current) menu.current.open = false;
    interacted.current = true;
    setName(item.name); setError(null); setMode(next);
  };
  const validName = name.trim().length > 0 && Array.from(name.trim()).length <= 200 && !/[\p{Cc}\p{Cs}]/u.test(name);
  const submit = async () => {
    if (!mode || inFlight.current || (mode === "rename" && !validName)) return;
    inFlight.current = true; setBusy(true); setError(null);
    try {
      if (mode === "rename") {
        await apiClient.podcastAction(item.podcast_id, { schema: "podcast-action/v1", action: "rename", name: name.trim(), expected_version: item.version });
        if (active.current) { setMode(null); onChanged(); }
      } else {
        await apiClient.deletePodcast(item.podcast_id);
        if (active.current) { dialog.current?.close(); onDeleted(); }
      }
    } catch (failure) {
      if (active.current) { setError(errorMessage(failure)); onChanged(); }
    } finally { inFlight.current = false; if (active.current) setBusy(false); }
  };
  return <>
    <details ref={menu} className="material-management-menu podcast-management" name="podcast-management" hidden={mode === "rename"}>
      <summary ref={opener} role="button" tabIndex={0} aria-label={`管理 Podcast「${item.name}」`}>⋯</summary>
      <div><button type="button" onClick={() => choose("rename")}>重新命名</button>
        <button type="button" className="material-delete-action" onClick={() => choose("delete")}>刪除 Podcast</button></div>
    </details>
    {mode !== "rename" && <h2 title={item.name}>{item.name}</h2>}
    {mode === "rename" && <InlineNameEditor label="Podcast 名稱" name={name} busy={busy} error={error} inputRef={input}
      onChange={setName} onSave={() => void submit()} onCancel={closeRename} />}
    {mode === "delete" && <dialog ref={dialog} className="cards-dialog" aria-labelledby={titleId}
      onCancel={e => { if (inFlight.current) e.preventDefault(); }}
      onClose={() => setMode(null)}>
      <form onSubmit={e => { e.preventDefault(); void submit(); }}>
        <h2 id={titleId}>刪除這份 Podcast？</h2>
        <p>「{item.name}」將從 Podcast 列表移除，音訊也會刪除。原教材與學習紀錄會保留。</p>
        {error && <p className="form-error" role="alert">{error}</p>}
        <div className="state-actions"><button type="button" className="secondary-button" autoFocus disabled={busy} onClick={() => dialog.current?.close()}>取消</button>
          <button type="submit" className="primary-button cards-danger" disabled={busy}>{busy ? "正在刪除…" : "確認刪除 Podcast"}</button></div>
      </form>
    </dialog>}
  </>;
}
