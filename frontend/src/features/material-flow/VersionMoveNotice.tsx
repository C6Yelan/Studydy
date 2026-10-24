import { useStudyStart } from "../study-session/StartingLevel";
import { useEffect, useRef, useState } from "react";
import { ApiClientError, errorMessage, type StudydyApiClient } from "../../api/client";
import type { VersionMovePreview, VersionMoveResult, VersionMoveUnmapped } from "../../api/contracts";
import { Modal } from "../../app/HeaderTools";
import { writeRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";
import { rememberedMaterial } from "./material-memory";
import "./version-move.css";

const reasons: Record<VersionMoveUnmapped["reason"], string> = {
  split: "內容在新版分散到多個概念",
  ambiguous: "新版有多個同名概念，無法確定是哪一個",
  not_found: "新版找不到對應的概念",
  merged_unfinished: "新版把它和尚未完成的概念合併，需在新版完成",
};
const basis = { evidence: "依相同原文", label: "依相同名稱" } as const;

function UnmappedList({ title, items }: { title: string; items: VersionMoveUnmapped[] }) {
  if (!items.length) return null;
  return <div className="version-move-unmapped">
    <p>{title}</p>
    <ul>{items.map((item) => <li key={item.concept_id}><strong>{item.label}</strong><span>{reasons[item.reason]}</span></li>)}</ul>
  </div>;
}

/**
 * 教材有新版本、而學生的卡組或學習進度還在舊版時，於教材頁顯示「有新版本」。
 * 只有學生確認後才承接；可對應的概念承接，其餘列出。只有教材保留舊版本時才查詢預覽。
 */
export function VersionMoveNotice({ apiClient, materialId }: { apiClient: StudydyApiClient; materialId: string }) {
  const launch = useStudyStart(apiClient, materialId);
  const material = rememberedMaterial(apiClient, materialId);
  const head = material?.head_revision ?? null;
  const hasOlder = !!head && !!material?.available_structures.some((item) => item.knowledge_structure_revision !== head);
  const [preview, setPreview] = useState<VersionMovePreview | null>(null);
  const [reload, setReload] = useState(0);
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [includeStudy, setIncludeStudy] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<VersionMoveResult | null>(null);
  const intent = useRef<{ signature: string; key: string } | null>(null);
  useEffect(() => {
    if (!hasOlder) { setPreview(null); return; }
    let cancelled = false;
    // 讀取失敗只少顯示提示，不影響教材頁。
    void apiClient.getVersionMove(materialId).then((value) => {
      if (cancelled) return;
      setPreview(value);
      setSelected(new Set(value.card_sets.filter((item) => item.movable).map((item) => item.card_set_id)));
      setIncludeStudy(value.study !== null);
    }, () => { if (!cancelled) setPreview(null); });
    return () => { cancelled = true; };
  }, [apiClient, materialId, hasOlder, head, reload]);

  const pending = !!preview?.preview_digest && (preview.card_sets.length > 0 || preview.study !== null);
  if (!pending && !result) return null;
  const close = () => {
    setOpen(false);
    // 承接後重新載入，讓卡組、地圖與首頁讀到新版資料。
    if (result) window.location.reload();
  };
  const confirm = async () => {
    if (!preview?.preview_digest || !preview.to_revision || busy) return;
    const body = {
      schema: "version-move-create/v1" as const,
      to_revision: preview.to_revision,
      card_set_ids: preview.card_sets.filter((item) => selected.has(item.card_set_id)).map((item) => item.card_set_id),
      include_study: includeStudy && preview.study !== null,
      preview_digest: preview.preview_digest,
    };
    // 同一次確認的重送沿用同一個鍵；改了選項才換新鍵。
    const signature = JSON.stringify(body);
    if (intent.current?.signature !== signature) intent.current = { signature, key: crypto.randomUUID() };
    setBusy(true);
    setError(null);
    try {
      setResult(await apiClient.moveToNewVersion(materialId, body, intent.current.key));
    } catch (failure) {
      setError(errorMessage(failure));
      if (failure instanceof ApiClientError && failure.reasonCode === "VERSION_MOVE_STALE") setReload((value) => value + 1);
    } finally {
      setBusy(false);
    }
  };
  const cardCount = preview?.card_sets.length ?? 0;
  const parts = [cardCount ? `${cardCount} 個卡組` : null, preview?.study ? "學習進度" : null].filter(Boolean).join("與");
  const chosen = selected.size > 0 || (includeStudy && !!preview?.study);
  const headStructure = material?.available_structures.find((item) => item.knowledge_structure_revision === head);
  return <>
    {launch.dialog}
    {launch.error && <p role="alert" className="form-error">{launch.error}</p>}
    {pending && <section className="version-move-notice" aria-label="有新版本">
      <span className="version-move-badge"><Icon name="sparkle" size={16}/>有新版本</span>
      <p>這份教材已更新，產生了新版本。你的{/^\d/.test(parts) ? " " : ""}{parts}還在舊版，確認後才會移到新版。</p>
      <button className="primary-button" type="button" onClick={() => { setError(null); setOpen(true); }}>移到新版</button>
    </section>}
    <Modal label="移到新版" open={open} onClose={close} className="version-move-dialog">
      <header className="version-move-head">
        <h2>{result ? "已移到新版" : "移到新版"}</h2>
        <button className="header-dialog-close" type="button" aria-label="關閉移到新版" onClick={close}>×</button>
      </header>
      {result ? <div className="version-move-body">
        <p>{[result.card_sets.length ? `已建立 ${result.card_sets.length} 個新版卡組` : null, result.study_session_id ? "新版學習已承接可對應的進度" : null].filter(Boolean).join("，")}。舊版的卡組與學習紀錄仍保留。</p>
        {result.card_sets.some((item) => item.status === "pending") && <p className="version-move-note">部分卡面正在依新版內容重新製作，完成後就能繼續複習。</p>}
        <div className="version-move-actions">
          {result.card_sets.length === 1 && <button className="secondary-button" type="button" onClick={() => { setOpen(false); writeRoute({ name: "card-set", cardSetId: result.card_sets[0].card_set_id, materialId }); }}>查看新版卡組</button>}
          {result.study_session_id && headStructure && <button className="secondary-button" type="button" disabled={launch.busy} onClick={() => { setOpen(false); void launch.start({ materialId, runId: headStructure.run_id, structureRevision: result.to_revision }); }}>前往新版學習</button>}
          <button className="primary-button" type="button" onClick={close}>完成</button>
        </div>
      </div> : preview && <div className="version-move-body">
        <p>可對應的概念會承接到新版，找不到對應的會列在下方。舊版的卡組與學習紀錄都會保留，不會被改寫。</p>
        {preview.card_sets.map((item) => {
          const regenerate = item.target_count - item.reused_designs;
          return <fieldset className="version-move-item" key={item.card_set_id} disabled={!item.movable || busy}>
            <label className="version-move-choice">
              <input type="checkbox" checked={selected.has(item.card_set_id)} onChange={(event) => setSelected((current) => {
                const next = new Set(current);
                if (event.target.checked) next.add(item.card_set_id); else next.delete(item.card_set_id);
                return next;
              })}/>
              <span><strong>卡組「{item.name}」</strong>
                {item.movable ? <small>{item.target_count} 張卡移到新版{item.carried_reviews ? `，沿用 ${item.carried_reviews} 張的學習標記` : ""}</small> : <small>沒有可對應的概念，無法移到新版</small>}
              </span>
            </label>
            {item.movable && regenerate > 0 && <p className="version-move-note">{regenerate} 張卡的內容在新版有更動，會依新版重新製作卡面，完成前不列入需要加強。</p>}
            <UnmappedList title={`這 ${item.unmapped.length} 張卡在新版找不到對應概念，不會移動：`} items={item.unmapped}/>
            {item.mapped.length > 0 && <details className="version-move-details">
              <summary>查看概念對應</summary>
              <ul>{item.mapped.map((row) => <li key={row.from_concept_id}>{row.from_label}<Icon name="chevron-right" size={14}/>{row.to_label}<small>{basis[row.basis]}</small></li>)}</ul>
            </details>}
          </fieldset>;
        })}
        {preview.study && <fieldset className="version-move-item" disabled={busy}>
          <label className="version-move-choice">
            <input type="checkbox" checked={includeStudy} onChange={(event) => setIncludeStudy(event.target.checked)}/>
            <span><strong>學習進度</strong>
              <small>{preview.study.carried.length
                ? `舊版已完成 ${preview.study.completed_count} 個概念，其中 ${preview.study.carried.length} 個在新版標示為已完成`
                : preview.study.completed_count ? `舊版已完成 ${preview.study.completed_count} 個概念，沒有可直接標示完成的概念` : "在新版接著學習"}</small>
            </span>
          </label>
          {preview.study.carried.length > 0 && <details className="version-move-details">
            <summary>查看承接的概念</summary>
            <ul>{preview.study.carried.map((row) => <li key={row.concept_id}>{row.from_labels.join("、")}<Icon name="chevron-right" size={14}/>{row.label}</li>)}</ul>
          </details>}
          <UnmappedList title={`這 ${preview.study.not_carried.length} 個已完成的概念不會在新版標示完成：`} items={preview.study.not_carried}/>
          {preview.study.current_concept && <p className="version-move-note">會從「{preview.study.current_concept.label}」接著學習。</p>}
          <p className="version-move-note">內容沒有改寫的重點，原本的作答紀錄在新版仍會計入。</p>
        </fieldset>}
        {preview.kept_podcasts > 0 && <p className="version-move-note">{preview.kept_podcasts} 集 Podcast 維持舊版內容，不會移動。</p>}
        {error && <p className="form-error" role="alert">{error}</p>}
        <div className="version-move-actions">
          <button className="secondary-button" type="button" onClick={close} disabled={busy}>稍後再說</button>
          <button className="primary-button" type="button" disabled={!chosen || busy} onClick={() => void confirm()}>{busy ? "正在移到新版…" : "確認移到新版"}</button>
        </div>
      </div>}
    </Modal>
  </>;
}
