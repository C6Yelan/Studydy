import { useEffect, useRef, useState } from "react";
import { ApiClientError, errorMessage, isNotFound, type StudydyApiClient } from "../../api/client";
import type { MaterialLibraryItem } from "../../api/contracts";
import { writeRoute } from "../../app/routes";
import { InlineConfirm } from "../../ui/InlineConfirm";
import { NotFoundView, materialNotFound } from "../../ui/NotFoundView";
import { RouteLink } from "../../ui/RouteLink";
import { StateView } from "../../ui/StateView";
import { FlowHeader } from "./Flow";
import { validName } from "./InlineNameEditor";
import { materialDeleteCopy } from "./material-delete-copy";
import { automaticPollIntervalMs } from "./material-flow";
import { rememberMaterial, rememberedMaterial } from "./material-memory";
import { sourceTotalsLabel } from "./source-summary";
import { VersionMoveNotice } from "./VersionMoveNotice";
import "./settings.css";

/** 教材設定：重新命名、來源與更新、刪除，沒有地圖的教材（確認來源中、分析失敗）也能用。 */
export function MaterialSettings({ apiClient, materialId }: { apiClient: StudydyApiClient; materialId: string }) {
  const [material, setMaterial] = useState<MaterialLibraryItem | null>(() => rememberedMaterial(apiClient, materialId));
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [reload, setReload] = useState(0);
  const [name, setName] = useState(material?.display_name ?? "");
  const [renaming, setRenaming] = useState(false);
  const [renameError, setRenameError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [removing, setRemoving] = useState(false);
  const mounted = useRef(true);
  const named = useRef(!!material);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const read = async () => {
      try {
        const item = await apiClient.getMaterial(materialId);
        if (cancelled) return;
        rememberMaterial(apiClient, item);
        setMaterial(item);
        setError(null);
        if (!named.current) { named.current = true; setName(item.display_name); }
        if (removing) timer = window.setTimeout(read, automaticPollIntervalMs);
      } catch (failure) {
        if (cancelled) return;
        // 刪除已受理：教材讀不到就代表刪完了，回我的群島。
        if (removing && failure instanceof ApiClientError && failure.reasonCode === "RESOURCE_NOT_FOUND") { writeRoute({ name: "materials" }); return; }
        if (!material && isNotFound(failure)) { setNotFound(true); return; }
        setError(errorMessage(failure));
      }
    };
    void read();
    return () => { cancelled = true; window.clearTimeout(timer); };
    // 只在換教材、重新讀取、進入刪除中時重讀；material 本身由這裡寫入。
  }, [apiClient, materialId, reload, removing]);

  if (notFound) return <NotFoundView {...materialNotFound} />;
  if (!material) return <section className="flow-page material-settings"><StateView title={error ? "無法讀取教材" : "正在讀取教材"} description={error ?? "正在載入教材設定。"} tone={error ? "failure" : "loading"} live={!error} skeleton="page"
    action={error ? <button type="button" className="secondary-button" onClick={() => setReload((value) => value + 1)}>重新讀取</button> : undefined} /></section>;

  const valid = validName(name);
  const changed = name.trim() !== material.display_name;
  const rename = async () => {
    if (renaming || !valid || !changed) return;
    setRenaming(true); setRenameError(null); setSaved(false);
    try {
      const updated = await apiClient.renameMaterial(materialId, name.trim());
      rememberMaterial(apiClient, updated);
      if (mounted.current) { setMaterial(updated); setName(updated.display_name); setSaved(true); }
    } catch (failure) {
      if (mounted.current) setRenameError(`無法重新命名教材。${errorMessage(failure)}`);
    } finally { if (mounted.current) setRenaming(false); }
  };
  const discard = async () => {
    if (deleting) return;
    setDeleting(true); setDeleteError(null);
    try {
      const result = await apiClient.discardMaterial(materialId);
      if (!mounted.current) return;
      if (result.state === "removed") writeRoute({ name: "materials" });
      else { setConfirming(false); setRemoving(true); }
    } catch (failure) {
      if (mounted.current) setDeleteError(`無法刪除教材。${errorMessage(failure)}`);
    } finally { if (mounted.current) setDeleting(false); }
  };
  const copy = materialDeleteCopy(material);
  const sourceCount = material.source_count ?? 1;
  const pages = material.source_totals?.page_count ?? 0;
  const maps = material.available_structures.length > 0;
  return <section className="flow-page material-settings">
    <FlowHeader title="教材設定" description="這座島的名稱、更新與刪除都在這裡。" />
    <div className="settings-grid">
      <section className="surface settings-panel" aria-labelledby="settings-name-title">
        <h2 id="settings-name-title">教材名稱</h2>
        <p>在「我的群島」和地圖頂端顯示的名稱。</p>
        <form className="settings-row" aria-label="重新命名教材" onSubmit={(event) => { event.preventDefault(); void rename(); }}>
          <input type="text" aria-label="教材名稱" maxLength={40} value={name} disabled={renaming || removing} onChange={(event) => { setName(event.target.value); setSaved(false); }} />
          <button className="primary-button" type="submit" disabled={renaming || removing || !valid || !changed}>{renaming ? "正在儲存…" : "儲存"}</button>
        </form>
        {!valid && <p className="form-error">名稱需為 1–40 個字，且不可包含控制字元。</p>}
        {saved && <p role="status" className="settings-saved">已儲存。</p>}
        {renameError && <p className="form-error" role="alert">{renameError}</p>}
        <dl className="settings-facts">
          <div><dt>來源</dt><dd>{sourceCount} 個檔案</dd></div>
          <div><dt>全部來源</dt><dd>{pages > 0 ? `${pages} 頁` : material.source_totals ? sourceTotalsLabel(material.source_totals) || "—" : "—"}</dd></div>
          <div><dt>建立日期</dt><dd>{new Date(material.created_at).toLocaleDateString("zh-TW", { timeZone: "Asia/Taipei" })}</dd></div>
        </dl>
      </section>
      <section className="surface settings-panel" aria-labelledby="settings-update-title">
        <h2 id="settings-update-title">來源與更新</h2>
        <VersionMoveNotice apiClient={apiClient} materialId={materialId} settings>
          <RouteLink className="secondary-button" route={{ name: "material-sources", materialId }}>查看原始檔案</RouteLink>
        </VersionMoveNotice>
        {!maps && <p className="settings-note">教材還沒有知識地圖，分析完成後才會有更新與學習進度。</p>}
      </section>
    </div>
    <section className="surface settings-panel is-danger" aria-labelledby="settings-delete-title">
      <h2 id="settings-delete-title">刪除教材</h2>
      {copy.notice && <p>{copy.notice}</p>}
      <p>{copy.scope}</p>
      {removing ? <p role="status">正在刪除…</p> : confirming
        ? <InlineConfirm message="確定刪除教材與相關資料？" label="刪除教材確認" cancelLabel="保留" confirmLabel="刪除" confirmAriaLabel="確認刪除" busyLabel="正在刪除…"
          busy={deleting} error={deleteError} onCancel={() => { setConfirming(false); setDeleteError(null); }} onConfirm={() => void discard()} />
        : <div><button className="secondary-button danger-button" type="button" data-inline-confirm-trigger onClick={() => setConfirming(true)}>刪除教材</button></div>}
    </section>
  </section>;
}
