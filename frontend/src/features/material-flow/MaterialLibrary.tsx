import { useEffect, useRef, useState, type ReactNode } from "react";

import { errorMessage, type StudydyApiClient } from "../../api/client";
import type {
  MaterialLibraryItem,
  MaterialStructureLink,
  StudySessionLink,
} from "../../api/contracts";
import { writeRoute, type AppRoute } from "../../app/routes";
import { RouteLink } from "../../ui/RouteLink";
import { Icon } from "../../ui/Icon";
import { IslandArt, islandPalette } from "../../ui/art/Island";
import { StateView } from "../../ui/StateView";
import { CollectionHeader } from "../../ui/CollectionHeader";
import { EmptyState } from "../../ui/EmptyState";
import { MaterialManagement, pointerStartedWithMenuOpen } from "./MaterialManagement";
import { formatFileSize } from "./material-flow";

type LibraryTopic = {topic_id:string; request:string; proposal:{title:string}|null; status:string; version:number;
  research:{status:string; material_id:string|null}|null};
const topicStages:Record<string,string> = {searching:'正在搜尋來源', selecting:'等待選取來源', acquiring:'正在取得來源',
  normalizing:'正在轉換來源', ready:'正在準備教材分析', submitted:'已送入教材分析', failed:'準備未完成', cancelled:'已停止，可接續處理'};
const topicWorking = (topic:LibraryTopic) => ['searching','acquiring','normalizing','ready'].includes(topic.research?.status ?? '');

function isProcessing(status: string | undefined) {
  return status === "pending" || status === "running";
}

function openStructure(item: MaterialLibraryItem, structure: MaterialStructureLink) {
  writeRoute({
    name: "knowledge-map",
    materialId: item.material_id,
    runId: structure.run_id,
    structureRevision: structure.knowledge_structure_revision,
  });
}

function openStudy(item: MaterialLibraryItem, session: StudySessionLink) {
  writeRoute({
    name: "study-session",
    materialId: item.material_id,
    runId: session.run_id,
    structureRevision: session.knowledge_structure_revision,
    studySessionId: session.study_session_id,
  });
}

export function MaterialLibrary({ apiClient }: { apiClient: StudydyApiClient }) {
  const [items, setItems] = useState<MaterialLibraryItem[] | null>(null);
  const [topics, setTopics] = useState<LibraryTopic[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [cancellingTopicId, setCancellingTopicId] = useState<string|null>(null);
  const [startingMaterialId, setStartingMaterialId] = useState<string | null>(null);
  const startCurrentStudy = async (item: MaterialLibraryItem, structure: MaterialStructureLink) => {
    if (startingMaterialId) return;
    setStartingMaterialId(item.material_id);
    try {
      const session = await apiClient.createStudySession({
        schema: "study-session-create/v1",
        material_id: item.material_id,
        knowledge_structure_revision: structure.knowledge_structure_revision,
      });
      writeRoute({
        name: "study-session",
        materialId: item.material_id,
        runId: structure.run_id,
        structureRevision: structure.knowledge_structure_revision,
        studySessionId: session.study_session_id,
      });
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setStartingMaterialId(null);
    }
  };
  const heading = useRef<HTMLHeadingElement>(null);
  const pendingRemovals = useRef(new Set<string>());
  const mutationVersion = useRef(0);
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const read = async () => {
      const version = mutationVersion.current;
      try {
        const [library, topicList] = await Promise.all([apiClient.listMaterials(), apiClient.studyTools<{topics:LibraryTopic[]}>('/v1/topics')]);
        const materials = library.materials;
        if (cancelled) return;
        if (version !== mutationVersion.current) {
          timer = window.setTimeout(read, 3000);
          return;
        }
        setItems(materials);
        setTopics(topicList.topics.filter(topic => topic.status === "approved"));
        setMessage(null);
        for (const id of pendingRemovals.current) {
          if (!materials.some((item) => item.material_id === id))
            pendingRemovals.current.delete(id);
        }
        if (
          pendingRemovals.current.size > 0 || topicList.topics.some(topicWorking) ||
          materials.some(
            (item) =>
              isProcessing(item.latest_attempt?.status) || isProcessing(item.source?.status),
          )
        ) {
          timer = window.setTimeout(read, 3000);
        }
      } catch (error) {
        if (!cancelled) {
          if (version === mutationVersion.current) setMessage(errorMessage(error));
          else timer = window.setTimeout(read, 3000);
        }
      }
    };
    void read();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [apiClient, reload]);

  const libraryClass = "material-library is-collection material-bookshelf";
  if (message || items === null) {
    const state = message ? (
      <StateView
        title="無法讀取教材"
        description={message}
        tone="failure"
        action={
          <>
            <button
              className="primary-button"
              type="button"
              onClick={() => {
                setMessage(null);
                setItems(null);
                setReload((value) => value + 1);
              }}
            >
              重新讀取
            </button>
            <button
              className="secondary-button"
              type="button"
              onClick={() => writeRoute({ name: "materials" })}
            >
              返回教材庫
            </button>
          </>
        }
      />
    ) : (
      <StateView
        title="正在讀取教材庫"
        description="正在載入你的教材與已發布結果。"
        tone="loading"
        live
        skeleton="library"
      />
    );
    return <section className={libraryClass}>{state}</section>;
  }
  const preparingTopics = topics.filter(topic => topic.research && !topic.research.material_id);
  const total = items.length + preparingTopics.length;
  const restoreLibraryFocus = () => heading.current?.focus({ preventScroll: true });
  const handleRenamed = (updated: MaterialLibraryItem) => {
    mutationVersion.current++;
    setItems(
      (previous) =>
        previous?.map((saved) => (saved.material_id === updated.material_id ? updated : saved)) ??
        null,
    );
  };
  return (
    <section className={libraryClass}>
      <CollectionHeader
        title="我的教材"
        subtitle={total === 0 ? "上傳教材後，可在這裡查看處理結果並接續學習。" : <>
          {/* 沒有頁內搜尋列，教材數量併入說明行。 */}
          <b>已保存 {items.length} 份教材{preparingTopics.length ? `，${preparingTopics.length} 份準備中` : ""}</b> · 每份教材都是一座島，從知識地圖開始探索與複習。
        </>}
        headingRef={heading}
        actions={total > 0 && <>
          <button className="secondary-button" type="button" onClick={() => writeRoute({ name: "topics" })}>
            <Icon name="sparkle" size={18} />
            從主題建立
          </button>
          <button className="primary-button" type="button" onClick={() => writeRoute({ name: "upload" })}>
            <Icon name="upload" size={18} />
            上傳教材
          </button>
        </>}
      />
      {total === 0 && (
        <EmptyState
          label="空教材引導"
          title="尚未有學習教材"
          description="先上傳第一份 PDF，讓 Studydy 陪你展開學習。"
          action={<button className="primary-button" type="button" onClick={() => writeRoute({ name: "upload" })}>
            <Icon name="upload" size={18} />
            上傳第一份教材
          </button>}
        />
      )}
      <div className="library-grid">
        {preparingTopics.map(topic => <article className="surface library-item" key={topic.topic_id} aria-label={topic.proposal?.title ?? topic.request}>
          <span className="library-island is-building" aria-hidden="true"><IslandArt palette={islandPalette(topic.topic_id)} landmarks={false}/></span>
          <h2>{topic.proposal?.title ?? topic.request}</h2>
          <p className="library-metadata">從主題建立 · 準備中的教材</p>
          <p className="library-state">{topicStages[topic.research!.status] ?? '等待更新'}</p>
          <div className="state-actions"><button className="primary-button" onClick={()=>writeRoute({name:'topic',topicId:topic.topic_id})}>
            {topic.research?.status === 'selecting' ? '選取來源' : '查看進度'}
          </button><button className="text-button" disabled={cancellingTopicId !== null} onClick={async()=>{
            if(!window.confirm('取消建立這份教材並停止來源準備？'))return;
            setCancellingTopicId(topic.topic_id);
            try { await apiClient.studyTools(`/v1/topics/${topic.topic_id}/actions`, {action:'cancel',expected_version:topic.version}); setReload(value=>value+1); }
            catch(error) { setMessage(errorMessage(error)); }
            finally { setCancellingTopicId(null); }
          }}>取消建立</button></div>
        </article>)}
        {items.map((item) => {
          const latest = item.latest_attempt;
          const topic = topics.find(topic => topic.research?.material_id === item.material_id);
          const available = item.available_structures;
          const structure =
            available.find((value) => value.knowledge_structure_revision === item.head_revision) ??
            available[0];
          const savedSession =
            structure &&
            item.study_sessions.find(
              (state) =>
                state.run_id === structure.run_id &&
                state.knowledge_structure_revision === structure.knowledge_structure_revision,
            );
          const isProcessingRun = isProcessing(latest?.status);
          const studyAction = savedSession ? (
            <button
              className="primary-button"
              type="button"
              onClick={() => openStudy(item, savedSession)}
            >
              {savedSession.status === "completed" ? "查看學習成果" : "繼續學習"}
            </button>
          ) : (
            structure?.base_revision &&
            item.study_sessions.length > 0 && (
              <button
                className="primary-button"
                disabled={startingMaterialId !== null}
                onClick={() => void startCurrentStudy(item, structure)}
              >
                {startingMaterialId === item.material_id ? "正在接續學習…" : "接續更新後的學習"}
              </button>
            )
          );
          const deleting = pendingRemovals.current.has(item.material_id);
          // 刪除中的卡片：fieldset 停用不涵蓋連結，改顯示停用的按鈕。
          const entry = (className: string, route: AppRoute, children: ReactNode) => deleting
            ? <button className={className} type="button" disabled>{children}</button>
            : <RouteLink className={className} route={route}>{children}</RouteLink>;
          const mapAction = structure && entry(
            !studyAction ? "primary-button" : "secondary-button",
            { name: "knowledge-map", materialId: item.material_id, runId: structure.run_id, structureRevision: structure.knowledge_structure_revision },
            <><Icon name="map" size={16} />開啟知識地圖</>,
          );
          // 點島嶼或標題：有地圖開地圖，處理中看進度，其餘到來源頁建立地圖。
          const openPrimary = () => {
            if (structure) openStructure(item, structure);
            else if (latest && (isProcessingRun || latest.status === "failed")) writeRoute({ name: "material-run", materialId: item.material_id, runId: latest.run_id });
            else writeRoute(topic ? { name: "topic", topicId: topic.topic_id } : { name: "material-sources", materialId: item.material_id });
          };
          return (
            <article
              className={`surface library-item${deleting ? " is-deleting" : ""}`}
              key={item.material_id}
              aria-label={item.display_name}
            >
              <span className={`library-island${structure ? "" : " is-building"}${deleting ? "" : " is-clickable"}`} aria-hidden="true" onClick={deleting ? undefined : () => { if (!pointerStartedWithMenuOpen()) openPrimary(); }}>
                <IslandArt palette={islandPalette(item.material_id)} landmarks={!!structure} />
              </span>
              <MaterialManagement
                onOpen={openPrimary}
                item={item}
                apiClient={apiClient}
                deleting={deleting}
                onRenamed={handleRenamed}
                onDeleted={(state) => {
                  mutationVersion.current++;
                  if (state === "removed") {
                    pendingRemovals.current.delete(item.material_id);
                    setItems(
                      (previous) =>
                        previous?.filter((saved) => saved.material_id !== item.material_id) ?? null,
                    );
                    if (items.length === 1) heading.current?.focus({ preventScroll: true });
                    else restoreLibraryFocus();
                  } else pendingRemovals.current.add(item.material_id);
                  setReload((value) => value + 1);
                }}
              />
              <p className="library-metadata">
                {new Date(item.created_at).toLocaleDateString()} ·{" "}
                {(item.source_count ?? 1) > 1
                  ? `${item.source_count} 個檔案`
                  : formatFileSize(item.size_bytes)}
              </p>
              {topic && !latest && <p className="library-state">{topicStages[topic.research!.status] ?? '準備中'}</p>}
              {isProcessingRun ? (
                <p className="library-state">正在建立知識地圖…</p>
              ) : (
                latest?.status === "failed" && (
                  <p className="library-state is-failed">知識地圖建立失敗</p>
                )
              )}
              <fieldset className={`state-actions library-actions${studyAction ? " has-study" : ""}`} disabled={deleting}>
                {studyAction}
                {mapAction}
                {latest && (isProcessingRun || latest.status === "failed") ? (
                  <button
                    className={structure ? "text-button" : "primary-button"}
                    type="button"
                    onClick={() =>
                      writeRoute({
                        name: "material-run",
                        materialId: item.material_id,
                        runId: latest.run_id,
                      })
                    }
                  >
                    {isProcessingRun ? "查看進度" : "查看問題"}
                  </button>
                ) : (
                  !structure && (
                    <button
                      className="primary-button"
                      type="button"
                      onClick={() =>
                        writeRoute(topic ? {name:"topic",topicId:topic.topic_id} : { name: "material-sources", materialId: item.material_id })
                      }
                    >
                      {topic ? "查看進度" : "建立知識地圖"}
                    </button>
                  )
                )}
                {structure && <>{entry("secondary-button library-link", { name: "material-content", materialId: item.material_id, kind: "concept-cards" }, <><Icon name="cards" size={16} />概念卡</>)}
                  {entry("secondary-button library-link", { name: "material-content", materialId: item.material_id, kind: "podcasts" }, <><Icon name="headphones" size={16} />Podcast</>)}</>}
              </fieldset>
            </article>
          );
        })}
      </div>
    </section>
  );
}
