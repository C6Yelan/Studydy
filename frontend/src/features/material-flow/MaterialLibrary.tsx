import { useEffect, useRef, useState } from "react";

import { errorMessage, type StudydyApiClient } from "../../api/client";
import type {
  MaterialLibraryItem,
  MaterialStructureLink,
  StudySessionLink,
} from "../../api/contracts";
import { writeRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";
import { StateView } from "../../ui/StateView";
import { MaterialManagement } from "./MaterialManagement";
import { formatFileSize } from "./material-flow";

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
  const [message, setMessage] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [starting, setStarting] = useState<string | null>(null);
  const startCurrentStudy = async (item: MaterialLibraryItem, structure: MaterialStructureLink) => {
    if (starting) return;
    setStarting(item.material_id);
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
      setStarting(null);
    }
  };
  const [searchQuery, setSearchQuery] = useState("");
  const searchInput = useRef<HTMLInputElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const pendingRemovals = useRef(new Set<string>());
  const mutationVersion = useRef(0);
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const read = async () => {
      const version = mutationVersion.current;
      try {
        const materials = (await apiClient.listMaterials()).materials;
        if (cancelled) return;
        if (version !== mutationVersion.current) {
          timer = window.setTimeout(read, 3000);
          return;
        }
        setItems(materials);
        setMessage(null);
        for (const id of pendingRemovals.current) {
          if (!materials.some((item) => item.material_id === id))
            pendingRemovals.current.delete(id);
        }
        if (
          pendingRemovals.current.size > 0 ||
          materials.some(
            (item) =>
              item.latest_attempt?.status === "pending" ||
              item.latest_attempt?.status === "running" ||
              item.source?.status === "pending" ||
              item.source?.status === "running",
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

  const libraryClass = "material-library is-collection";
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
      />
    );
    return <section className={libraryClass}>{state}</section>;
  }
  const normalize = (text: string) => text.trim().replace(/\s+/gu, " ").toLocaleLowerCase();
  const query = normalize(searchQuery);
  const filteredItems = items.filter((item) => normalize(item.display_name).includes(query));
  const restoreLibraryFocus = () =>
    (searchInput.current ?? heading.current)?.focus({ preventScroll: true });
  return (
    <section className={libraryClass}>
      <header className="library-header">
        <div>
          <h1 ref={heading} tabIndex={-1}>
            我的教材
          </h1>
          <p className="library-subtitle">
            {items.length === 0
              ? "上傳教材後，可在這裡查看處理結果並接續學習。"
              : `已保存 ${items.length} 份教材，隨時接續你的學習。`}
          </p>
        </div>
        {items.length > 0 && (
          <div className="state-actions">
            <button
              className="primary-button"
              type="button"
              onClick={() => writeRoute({ name: "upload" })}
            >
              上傳教材
            </button>
          </div>
        )}
      </header>
      {items.length > 0 && (
        <form className="library-search" role="search" onSubmit={(event) => event.preventDefault()}>
          <input
            ref={searchInput}
            type="search"
            aria-label="搜尋教材名稱"
            placeholder="搜尋教材名稱…"
            value={searchQuery}
            onChange={(event) => setSearchQuery(event.target.value)}
            onKeyDown={(event) => {
              // Chromium 原生 search 會用 Escape 清空；僅阻止此預設行為，保留右側 ×。
              if (event.key === "Escape") event.preventDefault();
            }}
          />
        </form>
      )}
      {items.length > 0 && query && filteredItems.length === 0 && (
        <div className="library-search-empty" role="status">
          <h2>找不到符合「{query}」的教材</h2>
          <p>試試其他教材名稱。</p>
        </div>
      )}
      {items.length === 0 && (
        <section className="library-empty surface" aria-label="空教材引導">
          <div className="library-empty-illustration">
            <img src="/assets/studydy/empty-disappointed.png" alt="Studydy 坐在打開的空箱子旁" />
          </div>
          <h2>尚未有學習教材</h2>
          <p>先上傳第一份 PDF，讓 Studydy 陪你展開學習。</p>
          <button
            className="primary-button"
            type="button"
            onClick={() => writeRoute({ name: "upload" })}
          >
            <Icon name="upload" size={18} />
            上傳第一份教材
          </button>
        </section>
      )}
      <div className="library-grid">
        {filteredItems.map((item) => {
          const latest = item.latest_attempt;
          const available = item.available_structures;
          const structure =
            available.find((value) => value.knowledge_structure_revision === item.head_revision) ??
            available[0];
          const learningState =
            structure &&
            item.study_sessions.find(
              (state) =>
                state.run_id === structure.run_id &&
                state.knowledge_structure_revision === structure.knowledge_structure_revision,
            );
          const busyRun = latest?.status === "pending" || latest?.status === "running";
          const studyAction = learningState ? (
            <button
              className="primary-button"
              type="button"
              onClick={() => openStudy(item, learningState)}
            >
              {learningState.status === "completed" ? "查看學習成果" : "繼續學習"}
            </button>
          ) : (
            structure?.base_revision &&
            item.study_sessions.length > 0 && (
              <button
                className="primary-button"
                disabled={starting !== null}
                onClick={() => void startCurrentStudy(item, structure)}
              >
                {starting === item.material_id ? "正在接續學習…" : "接續更新後的學習"}
              </button>
            )
          );
          const mapAction = structure && (
            <button
              className={!studyAction ? "primary-button" : "secondary-button"}
              type="button"
              onClick={() => openStructure(item, structure)}
            >
              開啟知識地圖
            </button>
          );
          const deleting = pendingRemovals.current.has(item.material_id);
          return (
            <article
              className={`surface library-item${deleting ? " is-deleting" : ""}`}
              key={item.material_id}
              aria-label={item.display_name}
            >
              <span className="library-file-icon" aria-hidden="true">
                <Icon name="file" size={25} />
              </span>
              <MaterialManagement
                item={item}
                apiClient={apiClient}
                deleting={deleting}
                onRenamed={(updated) => {
                  mutationVersion.current++;
                  setItems(
                    (previous) =>
                      previous?.map((saved) =>
                        saved.material_id === updated.material_id ? updated : saved,
                      ) ?? null,
                  );
                  if (!normalize(updated.display_name).includes(query)) restoreLibraryFocus();
                }}
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
              {busyRun ? (
                <p className="library-state">正在建立知識地圖…</p>
              ) : (
                latest?.status === "failed" && (
                  <p className="library-state is-failed">知識地圖建立失敗</p>
                )
              )}
              <fieldset className="state-actions" disabled={deleting}>
                {studyAction}
                {mapAction}
                {busyRun ? (
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
                    查看進度
                  </button>
                ) : latest?.status === "failed" ? (
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
                    查看問題
                  </button>
                ) : (
                  !structure && (
                    <button
                      className="primary-button"
                      type="button"
                      onClick={() =>
                        writeRoute({ name: "material-sources", materialId: item.material_id })
                      }
                    >
                      建立知識地圖
                    </button>
                  )
                )}
              </fieldset>
            </article>
          );
        })}
      </div>
    </section>
  );
}
