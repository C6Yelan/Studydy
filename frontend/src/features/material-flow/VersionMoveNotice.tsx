import { useStudyStart } from "../study-session/useStudyStart";
import { useEffect, useRef, useState } from "react";
import { ApiClientError, errorMessage, type StudydyApiClient } from "../../api/client";
import type { VersionMovePreview, VersionMoveResult, VersionMoveUnmapped, VersionMoveReturn, StudySessionLink } from "../../api/contracts";
import { Modal } from "../../app/HeaderTools";
import { writeRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";
import { rememberedMaterial } from "./material-memory";
import "./version-move.css";

const reasons: Record<VersionMoveUnmapped["reason"], string> = {
  split: "內容在更新後分散到多個概念",
  ambiguous: "更新後有多個同名概念，無法確定是哪一個",
  not_found: "更新後找不到對應的概念",
  merged_unfinished: "更新後把它和尚未完成的概念合併，需在更新後完成",
};
const basis = { evidence: "依相同原文", label: "依相同名稱" } as const;

function UnmappedList({ title, items }: { title: string; items: VersionMoveUnmapped[] }) {
  if (!items.length) return null;
  return <div className="version-move-unmapped">
    <p>{title}</p>
    <ul>{items.map((item) => <li key={item.concept_id}><strong>{item.label}</strong><span>{reasons[item.reason]}</span></li>)}</ul>
  </div>;
}

// 預覽也包含自動承接與上一入口；沒有待移項目時仍可顯示完成與返回。
export function VersionMoveNotice({ apiClient, materialId, compact = false }: { apiClient: StudydyApiClient; materialId: string; compact?: boolean }) {
  const launch = useStudyStart(apiClient, materialId);
  const material = rememberedMaterial(apiClient, materialId);
  const head = material?.head_revision ?? null;
  const [preview, setPreview] = useState<VersionMovePreview | null>(null);
  const [reload, setReload] = useState(0);
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [includeStudy, setIncludeStudy] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<VersionMoveResult | null>(null);
  const previewRead = useRef(0);
  const intent = useRef<{ signature: string; key: string } | null>(null);
  const [previousEntries, setPreviousEntries] = useState<{
    value: VersionMoveReturn; study: StudySessionLink | null; cards: { id: string; name: string }[];
  } | null>(null);
  useEffect(() => {
    setPreview(null);
    setPreviousEntries(null);
    if (!head) return;
    let cancelled = false;
    const reading = ++previewRead.current;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      try {
        const value = await apiClient.getVersionMove(materialId);
        if (cancelled || reading !== previewRead.current) return;
        setPreview(value);
        setSelected(new Set(value.card_sets.filter((item) => item.movable).map((item) => item.card_set_id)));
        setIncludeStudy(value.study?.movable === true);
        if (value.automatic_move?.status === "pending") timer = setTimeout(() => void load(), 2000);
      } catch { if (!cancelled && reading === previewRead.current) setPreview(null); }
    };
    void load();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [apiClient, materialId, head, reload]);

  const pending = !!preview && (
    !!preview.preview_digest && (preview.card_sets.length > 0 || preview.study !== null)
    || !!preview.study && !preview.study.movable && preview.study.completed_count > 0
  );
  const automaticPending = preview?.automatic_move?.status === "pending";
  const automaticDone = preview?.automatic_move?.status === "completed";
  const showNotice = !!preview && (pending || preview.automatic_move !== null || preview.previous !== null)
    && (compact || !preview.notice_dismissed);
  const dismiss = async () => {
    setBusy(true); setError(null);
    try {
      const saved = await apiClient.setVersionMoveNotice(materialId, true);
      previewRead.current++;
      setPreview(current => current ? { ...current, notice_dismissed: saved.dismissed } : current);
      setReload(value => value + 1);
    } catch (failure) { setError(errorMessage(failure)); }
    finally { setBusy(false); }
  };
  const returnPrevious = async () => {
    if (!preview?.previous || busy) return;
    setBusy(true); setError(null);
    try {
      const value = await apiClient.returnToPreviousEntries(materialId, preview.previous.move_id);
      const [fresh, sets] = await Promise.all([
        value.study_session_id ? apiClient.getMaterial(materialId) : Promise.resolve(null),
        value.card_set_ids.length ? apiClient.listCardSets() : Promise.resolve(null),
      ]);
      setPreviousEntries({ value,
        study: fresh?.study_sessions.find(item => item.study_session_id === value.study_session_id) ?? null,
        cards: value.card_set_ids.map((id, index) => ({ id, name: sets?.card_sets.find(item => item.card_set_id === id)?.name ?? `卡組 ${index + 1}` })),
      });
    } catch (failure) {
      setError(errorMessage(failure));
      if (failure instanceof ApiClientError && ["VERSION_MOVE_PREVIEW_STALE", "VERSION_MOVE_PREVIOUS_UNAVAILABLE"].includes(failure.reasonCode ?? "")) setReload(value => value + 1);
    } finally { setBusy(false); }
  };
  if (!preview && !result) return null;
  const close = () => {
    setOpen(false);
    // 承接後重新載入，讓卡組、地圖與首頁讀到更新後資料。
    if (result) window.location.reload();
  };
  const confirm = async () => {
    if (!preview?.preview_digest || !preview.to_revision || busy) return;
    const body = {
      schema: "version-move-create/v1" as const,
      to_revision: preview.to_revision,
      card_set_ids: preview.card_sets.filter((item) => selected.has(item.card_set_id)).map((item) => item.card_set_id),
      include_study: includeStudy && preview.study?.movable === true,
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
  const chosen = selected.size > 0 || (includeStudy && preview?.study?.movable === true);
  const headStructure = material?.available_structures.find((item) => item.knowledge_structure_revision === head);
  return <>

    {launch.error && <p role="alert" className="form-error">{launch.error}</p>}
    {showNotice && <section className={compact ? "version-move-entry" : "version-move-notice version-move-banner"} aria-label="教材已更新">
      {!compact && <span className="version-move-badge"><Icon name="sparkle" size={16}/>教材已更新</span>}
      <div className="version-move-description">
        <p>{automaticPending ? "正在一起更新卡組和學習進度…" : pending ? "卡組與學習進度還在更新前的內容"
          : automaticDone ? "卡組和學習進度已一起更新" : preview?.automatic_move?.status === "failed" ? "卡組與學習進度未能自動更新。" : "可以開啟更新前的卡組與學習紀錄。"}</p>
        {!compact && !!preview?.blocking_reasons.length && <p className="version-move-note">{preview.blocking_reasons.map(reason => ({
          unmapped_cards: `${reason.count} 張卡無法完整帶過去`,
          unmapped_progress: `${reason.count} 個已完成概念無法直接保留完成狀態`,
          assessment_in_progress: `${reason.count} 個題組仍在進行中`,
          source_unavailable: `${reason.count} 項更新前內容目前無法核對`,
        })[reason.code]).join("；")}。更新前可先查看明細。</p>}
        {!compact && pending && preview?.automatic_move?.status === "failed" && <p className="version-move-note">自動更新未完成，請查看預覽後再試。</p>}
      </div>
      {pending && !automaticPending && <>
        {compact && <span aria-hidden="true">·</span>}
        <button className={compact ? "version-move-entry-action" : "secondary-button"} type="button" disabled={busy} onClick={() => { setError(null); setOpen(true); }}>更新</button>
      </>}
      {preview?.previous && !automaticPending && <button className={compact ? "version-move-entry-action" : "secondary-button"} type="button" disabled={busy} onClick={() => void returnPrevious()}>回到更新前內容</button>}
      {!compact && <button className="version-move-dismiss" type="button" aria-label="關閉教材更新提醒" disabled={busy} onClick={() => void dismiss()}><Icon name="close" size={18}/></button>}
    </section>}
    {error && !open && <p className="form-error" role="alert">{error}</p>}
    <Modal label="回到更新前的內容" open={previousEntries !== null} onClose={() => setPreviousEntries(null)} className="version-move-dialog">
      <header className="version-move-head"><h2>回到更新前的內容</h2><button className="header-dialog-close" type="button" aria-label="關閉更新前入口" onClick={() => setPreviousEntries(null)}>×</button></header>
      {previousEntries && <div className="version-move-body">
        <p>選擇要開啟的卡組或學習。教材維持目前內容，更新後的作答與標記也會保留。</p>
        {previousEntries.cards.map(card => <button className="secondary-button version-move-return-entry" type="button" key={card.id} onClick={() => { setPreviousEntries(null); writeRoute({ name: "card-set", materialId, cardSetId: card.id }); }}><Icon name="cards" size={18}/><span>{card.name}</span></button>)}
        {previousEntries.study && <button className="secondary-button" type="button" onClick={() => {
          const study = previousEntries.study!;
          setPreviousEntries(null);
          writeRoute({ name: "study-session", materialId, runId: study.run_id, structureRevision: study.knowledge_structure_revision, studySessionId: study.study_session_id });
        }}><Icon name="learning" size={18}/>更新前的學習</button>}
        {previousEntries.value.study_session_id && !previousEntries.study && <p>先前的學習入口已無法開啟。</p>}
      </div>}
    </Modal>
    <Modal label="更新卡組與學習進度" open={open} onClose={close} className="version-move-dialog">
      <header className="version-move-head">
        <h2>{result ? "卡組與學習進度已更新" : "更新卡組與學習進度"}</h2>
        <button className="header-dialog-close" type="button" aria-label="關閉更新預覽" onClick={close}>×</button>
      </header>
      {result ? <div className="version-move-body">
        <p>{[result.card_sets.length ? `已建立 ${result.card_sets.length} 個更新後卡組` : null, result.study_session_id ? "更新後學習已承接可對應的進度" : null].filter(Boolean).join("，")}。更新前的卡組與學習紀錄仍保留。</p>
        {result.card_sets.some((item) => item.status === "pending") && <p className="version-move-note">部分卡面正在依更新後內容重新製作，完成後就能繼續複習。</p>}
        <div className="version-move-actions">
          {result.card_sets.length === 1 && <button className="secondary-button" type="button" onClick={() => { setOpen(false); writeRoute({ name: "card-set", cardSetId: result.card_sets[0].card_set_id, materialId }); }}>查看更新後卡組</button>}
          {result.study_session_id && headStructure && <button className="secondary-button" type="button" disabled={launch.busy} onClick={() => { setOpen(false); void launch.start({ materialId, runId: headStructure.run_id, structureRevision: result.to_revision }); }}>前往更新後學習</button>}
          <button className="primary-button" type="button" onClick={close}>完成</button>
        </div>
      </div> : preview && <>
      <div className="version-move-body">
        <p>可對應的概念會承接到更新後，找不到對應的會列在下方。更新前的卡組與學習紀錄都會保留，不會被改寫。</p>
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
                {item.movable ? <small>{item.target_count} 張卡將更新{item.carried_reviews ? `，沿用 ${item.carried_reviews} 張的學習標記` : ""}</small> : <small>沒有可對應的概念，無法更新</small>}
              </span>
            </label>
            {item.movable && regenerate > 0 && <p className="version-move-note">{regenerate} 張卡的內容在更新後有更動，會依更新後重新製作卡面，完成前不列入需要加強。</p>}
            <UnmappedList title={`這 ${item.unmapped.length} 張卡在更新後找不到對應概念，不會移動：`} items={item.unmapped}/>
            {item.mapped.length > 0 && <details className="version-move-details">
              <summary>查看概念對應</summary>
              <ul>{item.mapped.map((row) => <li key={row.from_concept_id}>{row.from_label}<Icon name="chevron-right" size={14}/>{row.to_label}<small>{basis[row.basis]}</small></li>)}</ul>
            </details>}
          </fieldset>;
        })}
        {preview.study && <fieldset className="version-move-item" disabled={busy}>
          <label className="version-move-choice">
            <input type="checkbox" disabled={!preview.study.movable} checked={includeStudy} onChange={(event) => setIncludeStudy(event.target.checked)}/>
            <span><strong>學習進度</strong>
              <small>更新前已完成 {preview.study.completed_count} 個概念；可承接 {preview.study.carried.reduce((count, item) => count + item.from_labels.length, 0)} 個舊概念，對應更新後 {preview.study.carried.length} 個概念</small>
            </span>
          </label>
          {preview.study.carried.length > 0 && <details className="version-move-details">
            <summary>查看承接的概念</summary>
            <ul>{preview.study.carried.map((row) => <li key={row.concept_id}>{row.from_labels.join("、")}<Icon name="chevron-right" size={14}/>{row.label}</li>)}</ul>
          </details>}
          {preview.study.already_completed.length > 0 && <details className="version-move-details">
            <summary>{preview.study.already_completed.reduce((count, item) => count + item.from_labels.length, 0)} 個舊概念對應到更新後原本已完成的 {preview.study.already_completed.length} 個概念，維持完成</summary>
            <ul>{preview.study.already_completed.map((row) => <li key={row.concept_id}>{row.from_labels.join("、")}<Icon name="chevron-right" size={14}/>{row.label}</li>)}</ul>
          </details>}
          <UnmappedList title={`這 ${preview.study.not_carried.length} 個已完成的概念不會在更新後標示完成：`} items={preview.study.not_carried}/>
          {preview.study.movable && preview.study.current_concept && <p className="version-move-note">會從「{preview.study.current_concept.label}」接著學習。</p>}
          <p className="version-move-note">內容沒有改寫的重點，原本的作答紀錄在更新後仍會計入。</p>
        </fieldset>}
        {preview.kept_podcasts > 0 && <p className="version-move-note">{preview.kept_podcasts} 集 Podcast 維持更新前內容，不會移動。</p>}
        {error && <p className="form-error" role="alert">{error}</p>}
      </div>
        <div className="version-move-actions version-move-confirm-actions">
          <button className="secondary-button" type="button" onClick={close} disabled={busy}>稍後再說</button>
          <button className="primary-button" type="button" disabled={!chosen || busy} onClick={() => void confirm()}>{busy ? "正在更新…" : "確認更新"}</button>
        </div>
      </>}
    </Modal>
  </>;
}
