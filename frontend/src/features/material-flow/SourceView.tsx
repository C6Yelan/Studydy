import { useEffect, useRef, useState } from "react";
import { InlineConfirm } from "../../ui/InlineConfirm";
import { ApiClientError, errorMessage, isNotFound, type StudydyApiClient } from "../../api/client";
import { NotFoundView, materialNotFound } from "../../ui/NotFoundView";
import type { SourceListView, MaterialLibraryItem, FormatCapability } from "../../api/contracts";
import { writeRoute } from "../../app/routes";
import { BackLink } from "../../ui/BackLink";
import { StateView } from "../../ui/StateView";
import { rememberMaterial } from "./material-memory";
import { MaterialContentNav } from "./MaterialContentNav";
import { MaterialRemoveControl } from "./MaterialRemoveControl";
import { automaticPollIntervalMs, formatFileSize, validateSourceFile } from "./material-flow";
import { FlowFooter, FlowHeader, FlowStage, LearningSteps, uploadSteps } from "./Flow";
import { Icon } from "../../ui/Icon";

const SOURCE_PREVIEW_COUNT = 10;

type QueuedFile = {
  file: File;
  key: string;
  mediaType: string;
  error: string | null;
  status: "pending" | "uploading" | "failed" | "uploaded";
};

export function SourceView({
  apiClient,
  materialId,
}: {
  apiClient: StudydyApiClient;
  materialId: string;
}) {
  const [sourceList, setSourceList] = useState<SourceListView | null>(null);
  const sourceListRef = useRef<SourceListView | null>(null);
  sourceListRef.current = sourceList;
  const [material, setMaterial] = useState<MaterialLibraryItem | null>(null);
  const [sourceQuery,setSourceQuery]=useState('');
  const [expandedGroups, setExpandedGroups] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [removing, setRemoving] = useState(false);
  const discardAccepted = useRef(false);
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false);
  const [reload, setReload] = useState(0);
  const [selectionError, setSelectionError] = useState("");
  const heading = useRef<HTMLHeadingElement>(null);
  const [queue, setQueue] = useState<QueuedFile[]>([]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const seenSources = useRef(new Set<string>());
  const uploadedKeys = useRef(new Set<string>());
  const intent = useRef({ signature: "", key: crypto.randomUUID() });
  const [formats, setFormats] = useState<FormatCapability[]>([]);
  const [formatsReady, setFormatsReady] = useState(false);
  const [capabilityError, setCapabilityError] = useState("");
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
          setCapabilityError("無法載入上傳限制，請重新整理後再試。");
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [apiClient]);
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const read = async () => {
      // 只清除這次 GET 開始前已上傳成功的項目，避免舊輪詢回應提前移除本機佇列項目。
      const refreshedKeys = new Set(uploadedKeys.current);
      try {
        const [sources, item] = await Promise.all([
          apiClient.getSources(materialId),
          apiClient.getMaterial(materialId),
        ]);
        if (cancelled) return;
        if ([...uploadedKeys.current].some((key) => !refreshedKeys.has(key))) {
          timer = window.setTimeout(read, automaticPollIntervalMs);
          return;
        }
        const staged = sources.sources.filter((source) => !source.included);
        const added = staged
          .filter((source) => !seenSources.current.has(source.normalization_id))
          .map((source) => source.normalization_id);
        setSelectedIds((previous) => [
          ...previous.filter((id) => staged.some((source) => source.normalization_id === id)),
          ...added,
        ]);
        seenSources.current = new Set(sources.sources.map((source) => source.normalization_id));
        setSourceList(sources);
        rememberMaterial(apiClient, item);
        setMaterial(item);
        setQueue((previous) => previous.filter((item) => !refreshedKeys.has(item.key)));
        for (const key of refreshedKeys) uploadedKeys.current.delete(key);
        if (sources.discard_requested) {
          discardAccepted.current = true;
          setRemoving(true);
        }
        if (
          discardAccepted.current ||
          sources.sources.some(
            (source) => source.status === "pending" || source.status === "running",
          ) ||
          item.latest_attempt?.status === "running" ||
          item.latest_attempt?.status === "pending"
        )
          timer = window.setTimeout(read, automaticPollIntervalMs);
      } catch (failure) {
        if (cancelled) return;
        if (
          discardAccepted.current &&
          failure instanceof ApiClientError &&
          failure.reasonCode === "RESOURCE_NOT_FOUND"
        ) {
          writeRoute({ name: "materials" });
          return;
        }
        // 第一次就讀不到：教材不存在或不屬於此帳號，不顯示確認／刪除流程。
        if (isNotFound(failure) && !sourceListRef.current) {
          setNotFound(true);
          return;
        }
        setError(errorMessage(failure));
      }
    };
    void read();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [apiClient, materialId, reload]);
  const currentStructure =
    material?.available_structures.find(
      (item) => item.knowledge_structure_revision === material.head_revision,
    ) ?? material?.available_structures[0];
  const baseRevision = currentStructure?.knowledge_structure_revision ?? null;
  const run = material?.latest_attempt;
  const hasActiveRun = run?.status === "pending" || run?.status === "running";
  const staged = sourceList?.sources.filter((source) => !source.included) ?? [];
  const selectedSources = selectedIds.flatMap((id) =>
    staged.filter((source) => source.normalization_id === id),
  );
  const displayedSources = currentStructure ? (sourceList?.sources ?? []) : selectedSources;
  const visibleSources=displayedSources.filter(source=>!currentStructure||source.original_name.toLocaleLowerCase().includes(sourceQuery.trim().toLocaleLowerCase()));
  const adjustingSources=!currentStructure||hasActiveRun||staged.length>0||queue.length>0;
  const sourcesReady =
    selectedSources.length > 0 && selectedSources.every((source) => source.status === "ready");
  const hasUploads = currentStructure ? staged.length > 0 : (sourceList?.sources.length ?? 0) > 0;
  const flowStarted =
    !!run &&
    run.status !== "failed" &&
    run.status !== "cancelled" &&
    (!currentStructure || !!run.base_revision);
  const flowComplete = flowStarted && (run?.status === "succeeded" || run?.status === "partial");
  const perform = async (action: () => Promise<void>) => {
    if (submitting.current || discardAccepted.current) return;
    submitting.current = true;
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  };
  const start = () =>
    perform(async () => {
      if (!sourcesReady || hasActiveRun || queue.length > 0) return;
      const ids = selectedSources.map((source) => source.normalization_id);
      const signature = JSON.stringify([baseRevision, ids]);
      if (intent.current.signature !== signature)
        intent.current = { signature, key: crypto.randomUUID() };
      const created = await apiClient.createRevision(
        materialId,
        ids,
        intent.current.key,
        baseRevision,
      );
      writeRoute({ name: "material-run", materialId, runId: created.run_id });
    });
  // 重新檢核：沿用已保存地圖與來源產生新版本（後端 /review）；舊版卡組與進度保留，由學生自行移到新版。
  const recheckIntent = useRef<{ base: string; key: string } | null>(null);
  const [confirmingRecheck, setConfirmingRecheck] = useState(false);
  const recheck = () =>
    perform(async () => {
      if (!baseRevision || hasActiveRun) return;
      if (recheckIntent.current?.base !== baseRevision)
        recheckIntent.current = { base: baseRevision, key: crypto.randomUUID() };
      const created = await apiClient.reviewMaterial(materialId, baseRevision, recheckIntent.current.key);
      writeRoute({ name: "material-run", materialId, runId: created.run_id });
    });
  const chooseFiles = (files: FileList | null) => {
    if (!files?.length || submitting.current || discardAccepted.current) return;
    if (!formatsReady) {
      setSelectionError("請待支援格式載入後重新選擇教材。");
      return;
    }
    const newFiles: QueuedFile[] = [];
    const rejected: string[] = [];
    for (const file of Array.from(files)) {
      const problem = validateSourceFile(file, formats);
      if (problem) rejected.push(`「${file.name}」：${problem}`);
      else
        newFiles.push({
          file,
          key: crypto.randomUUID(),
          mediaType: formats.find((format) => file.name.toLowerCase().endsWith(format.extension))!
            .media_type,
          status: "pending",
          error: null,
        });
    }
    setSelectionError(rejected.join(" "));
    if (newFiles.length) setQueue((previous) => [...previous, ...newFiles]);
  };
  const updateQueuedFile = (
    key: string,
    status: QueuedFile["status"],
    error: string | null = null,
  ) =>
    setQueue((previous) =>
      previous.map((item) => (item.key === key ? { ...item, status, error } : item)),
    );
  const upload = () =>
    perform(async () => {
      for (const item of queue.filter((item) => item.status !== "uploaded")) {
        updateQueuedFile(item.key, "uploading");
        try {
          await apiClient.uploadSource(materialId, item.file, item.mediaType, item.key);
          uploadedKeys.current.add(item.key);
          updateQueuedFile(item.key, "uploaded");
        } catch (failure) {
          updateQueuedFile(item.key, "failed", errorMessage(failure));
        }
      }
      setReload((value) => value + 1);
    });
  const moveSource = (index: number, offset: -1 | 1) =>
    setSelectedIds((previous) => {
      const next = [...previous];
      [next[index], next[index + offset]] = [next[index + offset], next[index]];
      return next;
    });
  if (notFound) return <NotFoundView {...materialNotFound} />;
  // 第一次讀取就失敗：只顯示錯誤與重新讀取，不顯示確認流程與刪除教材。
  if (!sourceList && error)
    return (
      <StateView
        title="無法讀取教材來源"
        description={error}
        tone="failure"
        action={<>
          <button className="primary-button" type="button" onClick={() => { setError(null); setReload((value) => value + 1); }}>重新讀取</button>
          <button className="secondary-button" type="button" onClick={() => writeRoute({ name: "materials" })}>回到我的教材</button>
        </>}
      />
    );
  if (!sourceList && !error)
    return (
      <StateView
        title="正在讀取教材轉換狀態"
        description="已上傳的原檔會保留，重新整理不會重複轉換。"
        tone="loading"
        live
        skeleton="page"
      />
    );
  const waiting = selectedSources.filter(
    (source) => source.status === "pending" || source.status === "running",
  ).length;
  const failed = selectedSources.filter((source) => source.status === "failed").length;
  const prepared = selectedSources.filter((source) => source.status === "ready").length;
  const pendingUploads = queue.filter((item) => item.status === "pending").length;
  const failedUploads = queue.filter((item) => item.status === "failed").length;
  const uploading = queue.filter((item) => item.status === "uploading").length;
  const uploaded = queue.filter((item) => item.status === "uploaded").length;
  const uploadNeeded = pendingUploads + failedUploads + uploading > 0;
  const summary =
    queue.length === 0 && sourcesReady
      ? `${selectedSources.length} 份教材 · 共 ${selectedSources.reduce((sum, source) => sum + (source.page_count ?? 0), 0)} 頁`
      : selectedSources.length === 0 && queue.length === 0
        ? "0 份教材"
        : [
            `${prepared} 份${failed ? "可用" : "已準備"}`,
            waiting > 0 && `${waiting} 份正在轉換`,
            failed > 0 && `${failed} 份轉換失敗`,
            pendingUploads > 0 && `${pendingUploads} 份待上傳`,
            uploading > 0 && `${uploading} 份上傳中`,
            failedUploads > 0 && `${failedUploads} 份上傳失敗`,
            uploaded > 0 && `${uploaded} 份已上傳`,
          ]
            .filter(Boolean)
            .join(" · ");
  const sourcePicker = (
    <label className={currentStructure ? "secondary-button source-add-control" : "source-add-control"}>
      <Icon name="plus" size={16} />
      新增來源
      <input
        type="file"
        multiple
        accept={formats.map((format) => format.extension).join(",")}
        aria-label="選擇新增教材"
        aria-describedby={selectionError ? "source-selection-error" : undefined}
        disabled={busy || !formatsReady}
        onChange={(event) => {
          chooseFiles(event.currentTarget.files);
          event.currentTarget.value = "";
        }}
      />
    </label>
  );
  const pageCount = displayedSources.reduce((sum, source) => sum + (source.page_count ?? 0), 0);
  const allReady = queue.length === 0 && displayedSources.length > 0 && displayedSources.every((source) => source.status === "ready");
  const groups = (currentStructure ? ["upload", "research"] as const : ["all"] as const).filter(group =>
    // 只有一種來源時不顯示空的另一組；兩組都空時保留「使用者上傳」說明。
    group === "all" || group === "upload" && queue.length > 0 || displayedSources.some(source => (source.origin ?? "upload") === group)
    || group === "upload" && displayedSources.length === 0);
  const sourceRows = groups.map(group => {
    const groupSources = visibleSources.filter(source => group === "all" || (source.origin ?? "upload") === group);
    // 來源很多時（千頁教材有數十份）先顯示前 10 份；搜尋時一律顯示全部符合項目。
    const collapsed = !!currentStructure && !sourceQuery.trim() && !expandedGroups.includes(group) && groupSources.length > SOURCE_PREVIEW_COUNT;
    return <section key={group} className="source-group" aria-label={group === "research" ? "網路補充學習資源" : "使用者上傳"}>
      {currentStructure && <h3 className="source-group-title">{group === "research" ? "網路補充學習資源" : "使用者上傳"}<small>{groupSources.length} 份</small></h3>}
      <ol className="source-list">
        {(collapsed ? groupSources.slice(0, SOURCE_PREVIEW_COUNT) : groupSources).map((source, index) => (
          <li className={`source-row${source.status === "failed" ? " is-failed" : ""}`} key={source.source_id} aria-label={source.original_name}>
            <span className="source-number">{currentStructure ? displayedSources.indexOf(source) + 1 : index + 1}</span>
            <div className="source-content">
              <strong className="source-name" title={source.original_name}>{source.original_name}</strong>
              <div className="source-metadata">
                {source.page_count !== null && <span>{source.page_count} 頁</span>}
                {source.analysis_mode === "selected" && source.analysis_page_count != null && <span>依學習需求選讀 {source.analysis_page_count} 頁中的相關段落</span>}
                {currentStructure && <span className={source.included ? "is-included" : undefined}>{source.included ? "目前地圖使用中" : "尚未納入目前地圖"}</span>}
              </div>
              {source.status !== "ready" && (
                <p className={source.status === "failed" ? "form-error" : "source-progress"} role="status">
                  {source.status === "pending" ? "等待轉換" : source.status === "running" ? "正在轉換…" : "轉換失敗"}
                </p>
              )}
              {source.error_code && (
                <details className="processing-technical source-technical">
                  <summary>查看錯誤資訊</summary>
                  <code>{source.error_code}</code>
                </details>
              )}
            </div>
            <div className="source-row-actions">
              {source.normalized_artifact_id && (
                <a
                  className="text-button"
                  href={removing ? undefined : `/v1/artifacts/${encodeURIComponent(source.normalized_artifact_id)}`}
                  aria-disabled={removing || undefined}
                  tabIndex={removing ? -1 : undefined}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  預覽 PDF
                </a>
              )}
              <a
                className="text-button source-download"
                href={removing ? undefined : `/v1/artifacts/${source.original_artifact_id}/download`}
                aria-disabled={removing || undefined}
                tabIndex={removing ? -1 : undefined}
                target="_blank"
                rel="noopener noreferrer"
              >
                下載原檔
              </a>
              {!source.included && !removing && (
                <>
                  {currentStructure && (
                    <label className="source-include">
                      <input
                        type="checkbox"
                        checked={selectedIds.includes(source.normalization_id)}
                        disabled={busy || hasActiveRun}
                        onChange={(event) =>
                          setSelectedIds((previous) =>
                            event.target.checked
                              ? [...previous, source.normalization_id]
                              : previous.filter((id) => id !== source.normalization_id),
                          )
                        }
                      />
                      加入這次更新
                    </label>
                  )}
                  {source.status === "failed" && (
                    <button
                      className="secondary-button"
                      disabled={busy}
                      onClick={() =>
                        void perform(async () => {
                          await apiClient.retryNormalization(materialId, source.normalization_id);
                          setReload((value) => value + 1);
                        })
                      }
                    >
                      重試轉換
                    </button>
                  )}
                  <button
                    className="text-button source-remove"
                    type="button"
                    disabled={busy || hasActiveRun || source.status === "running"}
                    onClick={() =>
                      void perform(async () => {
                        await apiClient.removeStagedSource(materialId, source.source_id);
                        setReload((value) => value + 1);
                        heading.current?.focus({ preventScroll: true });
                      })
                    }
                  >
                    移除
                  </button>
                </>
              )}
              {!currentStructure && !source.included && !removing && selectedSources.length > 1 && (
                <span className="source-reorder">
                  <button
                    type="button"
                    disabled={busy || hasActiveRun || index === 0}
                    aria-label={`上移 ${source.original_name}`}
                    onClick={() => moveSource(index, -1)}
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    disabled={busy || hasActiveRun || index === selectedSources.length - 1}
                    aria-label={`下移 ${source.original_name}`}
                    onClick={() => moveSource(index, 1)}
                  >
                    ↓
                  </button>
                </span>
              )}
            </div>
          </li>
        ))}
        {group !== "research" && queue.map((item, index) => (
          <li className={`source-row source-upload-row is-${item.status}`} key={item.key} aria-label={item.file.name}>
            <span className="source-number">{displayedSources.length + index + 1}</span>
            <div className="source-content">
              <strong className="source-name" title={item.file.name}>{item.file.name}</strong>
              <div className="source-metadata">
                <span>{formatFileSize(item.file.size)}</span>
                <span className={`source-upload-status is-${item.status}`} role="status">
                  {{ pending: "待上傳", uploading: "上傳中…", failed: "上傳失敗", uploaded: "已上傳" }[item.status]}
                </span>
              </div>
              {item.error && (
                <p className="form-error" role="alert">
                  {item.error}
                </p>
              )}
            </div>
            {!removing && (
              <div className="source-row-actions">
                <button
                  className="text-button source-remove"
                  disabled={busy || item.status === "uploaded"}
                  onClick={() => setQueue((previous) => previous.filter((value) => value.key !== item.key))}
                >
                  移除
                </button>
              </div>
            )}
          </li>
        ))}
      </ol>
      {collapsed && <button className="secondary-button source-show-all" type="button" onClick={() => setExpandedGroups(previous => [...previous, group])}>顯示全部 {groupSources.length} 份</button>}
      {currentStructure && !sourceQuery.trim() && groupSources.length === 0 && queue.length === 0 && <p className="source-group-empty">尚無{group === "research" ? "網路補充學習資源" : "上傳教材"}。</p>}
    </section>;
  });
  const listFooter = !removing && adjustingSources && (
    <FlowFooter
      className="source-list-footer"
      secondary={<>
        <p className="source-summary">
          {currentStructure ? "本次新增 " : ""}
          {summary}
        </p>
        {!hasActiveRun && !uploadNeeded && uploaded > 0 && <p role="status">正在更新教材來源…</p>}
        {!hasActiveRun && queue.length === 0 && waiting > 0 && <p role="status">等待教材轉換完成</p>}
        {failed > 0 && (
          <p className="form-error" role="status">
            請重試轉換、移除{currentStructure ? "或取消勾選" : ""}失敗的教材。
          </p>
        )}
        {run && !hasActiveRun && !currentStructure && (
          <button className="text-button" onClick={() => writeRoute({ name: "material-run", materialId, runId: run.run_id })}>
            查看處理結果
          </button>
        )}
      </>}
      primary={hasActiveRun && run ? (
        <button className="primary-button" onClick={() => writeRoute({ name: "material-run", materialId, runId: run.run_id })}>
          查看處理狀態
        </button>
      ) : uploadNeeded ? (
        <button className="primary-button" disabled={busy} onClick={() => void upload()}>
          {uploading ? "正在上傳…" : failedUploads ? "重試上傳" : "上傳新增教材"}
        </button>
      ) : (
        queue.length === 0 && sourcesReady && (
          <button className="primary-button" disabled={busy} onClick={() => void start()}>
            {busy ? "正在建立分析…" : currentStructure ? "確認新增並更新地圖" : "開始分析教材"}
            <Icon name="chevron-right" size={16} />
          </button>
        )
      )}
    />
  );
  const errorCard = error && (
    <div role="alert" className="surface source-request-error">
      <p className="form-error">{error}</p>
      <button className="secondary-button" onClick={() => { setError(null); setReload((value) => value + 1); }}>
        重新讀取
      </button>
    </div>
  );
  // 確認教材（尚未建立地圖）時沒有教材頁首，保留返回教材庫；教材分頁內已有頁首返回與分頁列，不再重複。
  const bottom = (
    <div className="flow-bottom source-footer">
      {!currentStructure && <BackLink label="返回教材庫" route={{ name: "materials" }} />}
      <MaterialRemoveControl
        inActionRow
        material={material}
        sources={sourceList?.sources}
        apiClient={apiClient}
        materialId={materialId}
        onAccepted={(state) => {
          if (state === "removed") writeRoute({ name: "materials" });
          else {
            discardAccepted.current = true;
            setRemoving(true);
            setReload((value) => value + 1);
          }
        }}
      />
    </div>
  );
  if (!currentStructure)
    return (
      <section className="flow-page source-page task-page" aria-labelledby="source-confirm-title">
        <FlowHeader
          headingId="source-confirm-title"
          title="確認教材"
          description="預覽教材內容、調整順序，確認後開始建立知識地圖。"
          image="/assets/studydy/upload-guide.png"
        />
        <LearningSteps labels={uploadSteps} current={flowComplete ? 3 : flowStarted ? 2 : 1} />
        {errorCard}
        <FlowStage
          className="source-list-card"
          label="教材來源"
          icon="file"
          title="教材來源"
          headingRef={heading}
          description={`${displayedSources.length + queue.length} 份${allReady ? ` · ${pageCount} 頁` : ""} · 依清單順序分析，可用箭頭調整。`}
          footer={listFooter}
        >
          {removing && <p role="status">正在刪除教材…</p>}
          {sourceRows}
          {!removing && !hasActiveRun && (
            <div className="source-add-area">
              {sourcePicker}
              <span className="flow-hint">可再加入其他檔案，一起建立同一份知識地圖。</span>
              {capabilityError && <p className="flow-hint" role="status">{capabilityError}</p>}
              {selectionError && <p id="source-selection-error" className="form-error" role="alert">{selectionError}</p>}
            </div>
          )}
        </FlowStage>
        {bottom}
      </section>
    );
  return (
    <section className="material-content source-page">
      <MaterialContentNav apiClient={apiClient} materialId={materialId} current="sources" materialName={material?.display_name} mapRoute={{ name: "knowledge-map", materialId, runId: currentStructure.run_id, structureRevision: currentStructure.knowledge_structure_revision }} />
      <div className="source-panel" role="tabpanel" id="material-panel-sources" aria-labelledby="material-tab-sources">
        <header className="source-panel-head">
          <div className="source-panel-title">
            {/* 教材分頁內沒有另外的頁面標題，清單標題即本頁標題。 */}
            <h1 ref={heading} tabIndex={-1}>教材來源</h1>
            <p>
              {displayedSources.length + queue.length} 份{allReady && ` · 全部來源共 ${pageCount} 頁`}
              <span className="source-inclusion-summary">
                目前地圖使用 {displayedSources.filter(source => source.included).length} 份
                {staged.length > 0 && ` · ${staged.length} 份尚未納入地圖`}
              </span>
            </p>
          </div>
          <div className="material-search-row">
            <form className="material-search" role="search" onSubmit={event => event.preventDefault()}>
              <input type="search" aria-label="搜尋教材來源" placeholder="搜尋來源檔案名稱…" value={sourceQuery} onChange={event => setSourceQuery(event.target.value)} />
            </form>
            {!removing && !hasActiveRun && sourcePicker}
          </div>
        </header>
        {capabilityError && <p className="flow-hint" role="status">{capabilityError}</p>}
        {selectionError && <p id="source-selection-error" className="form-error" role="alert">{selectionError}</p>}
        {errorCard}
        <section className="surface source-list-card" aria-label="教材來源">
          {removing && <p role="status">正在刪除教材…</p>}
          {sourceRows}
          {sourceQuery.trim() && visibleSources.length === 0 && <p className="cards-no-results">沒有符合的來源檔案。</p>}
          {listFooter}
        </section>
        {!removing && !adjustingSources && (
          <section className="surface source-recheck" aria-labelledby="source-recheck-title">
            <span className="flow-stage-icon" aria-hidden="true"><Icon name="refresh" size={22} /></span>
            <div>
              <h2 id="source-recheck-title">重新分析與重新檢核</h2>
              <p>重新檢核沿用目前的地圖與來源，重新檢查重點、合併重複概念並更新檢索，不重新辨識或下載來源。完成後產生新版本；舊版的卡組與學習進度會保留，可在教材頁按「移到新版」承接。</p>
              <p>要分析新的內容，請用「新增來源」加入教材後更新地圖。</p>
            </div>
            {confirmingRecheck
              ? <InlineConfirm message="重新檢核會用模型重新檢查目前的重點與概念，完成後產生新版本。" danger={false}
                confirmLabel="開始檢核" busyLabel="正在建立檢核…" busy={busy}
                onCancel={() => setConfirmingRecheck(false)} onConfirm={() => void recheck()} />
              : <button className="secondary-button" type="button" data-inline-confirm-trigger disabled={busy} onClick={() => setConfirmingRecheck(true)}>重新檢核</button>}
          </section>
        )}
        {bottom}
      </div>
    </section>
  );
}
