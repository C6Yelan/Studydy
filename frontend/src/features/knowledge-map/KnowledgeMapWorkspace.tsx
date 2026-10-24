import { DifficultyBadge, DifficultyList, LearningDiagnosis, useConceptDifficulty } from "../learning-diagnosis/LearningDiagnosis";
import { MaterialContentNav } from "../material-flow/MaterialContentNav";
import type { AppRoute } from "../../app/routes";
import { SourceButton, sourceLinks } from "../../ui/SourceButton";
import { useEffect, useMemo, useRef, useState, type ReactNode, type KeyboardEvent } from "react";
import {
  Background,
  MarkerType,
  Handle,
  Position,
  Controls,
  ReactFlowProvider,
  useReactFlow,
  useNodesInitialized,
  useUpdateNodeInternals,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
  type NodeChange,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import type { StudydyApiClient } from "../../api/client";
import type {
  KnowledgeOutlineView,
  KnowledgeRegionsView,
  RelationType,
  MapProgressView,
  ConceptDifficultyView,
  StudySessionView,
} from "../../api/contracts";
import { useConceptContext } from "../material-flow/useConceptContext";
import { errorMessage } from "../../api/client";
import { ConceptContent } from "../../ui/ConceptContent";
import { Icon } from "../../ui/Icon";
import { EmptyState } from "../../ui/EmptyState";
import { IslandArt, islandColors, islandPalette, regionPalette } from "../../ui/art/Island";
import { StateView } from "../../ui/StateView";
import { learningNavigationItems, focusGraph, groupSlices, initialFocusConceptId, mapRegions, navigatorRange, NAVIGATOR_WINDOW, regionBasisText, regionColorIndex, type MapRegions, type NavigatorRange } from "./knowledge-map";

type Concept = KnowledgeOutlineView["concepts"][number];
type Mode = "focus" | "review";
type RestoreFocus = () => void;

// 五種關係畫成不同的橋：先備＝石橋、組成＝木橋、應用＝航線、例子＝踏石、對照＝對岸。
const relationStyles: Record<RelationType, { label: string; bridge: string; color: string }> = {
  prerequisite: { label: "先備", bridge: "石橋", color: "#2f6bff" },
  part_of: { label: "組成", bridge: "木橋", color: "#22a865" },
  application: { label: "應用", bridge: "航線", color: "#f08a00" },
  example: { label: "例子", bridge: "踏石", color: "#1aa3b8" },
  contrast: { label: "對照", bridge: "對岸", color: "#e0569a" },
};

const fitViewOptions = { padding: 0.18, minZoom: 0.001, maxZoom: 1.1 };

const learningLabels = {
  not_started: "尚未開始",
  in_progress: "進行中",
  needs_review: "需要加強",
  completed: "已完成",
} as const;

function LearningBadge({
  conceptId,
  progress,
}: {
  conceptId: string;
  progress: MapProgressView | null;
}) {
  const state = progress?.concept_states.find((item) => item.concept_id === conceptId);
  const label = state?.status !== "completed" && state?.assessable === false
    ? "此概念目前無法提供可靠檢測"
    : state ? learningLabels[state.status] : null;
  return state && label ? (
    <span className={`map-learning-badge is-${state.status}`}>{label}</span>
  ) : null;
}

function DetailPanel({
  label,
  focusKey,
  close,
  children,
}: {
  label: string;
  focusKey: string;
  close: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const panel = ref.current!;
    // 平板斷點（styles.css 共用斷點；與 knowledge-map/styles.css 的浮層詳情同一門檻）：詳情改用 modal 浮層。
    const mobile = window.matchMedia("(max-width: 1024px)");
    const update = () => {
      if (panel.open) panel.close();
      if (mobile.matches) panel.showModal();
      else panel.show();
    };
    update();
    mobile.addEventListener("change", update);
    return () => {
      mobile.removeEventListener("change", update);
      panel.close();
    };
  }, []);
  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      ref.current?.focus({ preventScroll: true });
      ref.current?.scrollTo(0, 0);
    });
    return () => cancelAnimationFrame(frame);
  }, [focusKey]);
  return (
    <dialog
      ref={ref}
      className="detail-panel"
      aria-label={label}
      tabIndex={-1}
      onCancel={(event) => {
        event.preventDefault();
        close();
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          close();
        }
      }}
      onClick={(event) => {
        const rect = event.currentTarget.getBoundingClientRect();
        if (
          event.target === event.currentTarget &&
          (event.clientX < rect.left ||
            event.clientX > rect.right ||
            event.clientY < rect.top ||
            event.clientY > rect.bottom)
        )
          close();
      }}
    >
      {children}
    </dialog>
  );
}

function nodeKeyboardAction(open: () => void, title: string) {
  return {
    title,
    onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      open();
    },
  };
}

function ConceptDetail({
  apiClient,
  concept,
  close,
  view,
  progress,
  studyAction,
  isLoadingProgress,
  progressMessage,
  region,
  difficulty,
}: {
  view: KnowledgeOutlineView;
  progress: MapProgressView | null;
  apiClient: StudydyApiClient;
  concept: Concept;
  region: string | null;
  difficulty: ConceptDifficultyView | null;
  close: () => void;
  studyAction: ReactNode;
  isLoadingProgress: boolean;
  progressMessage: string | null;
}) {
  const read = useConceptContext(apiClient, view.material_id, view.knowledge_structure_revision, [concept.concept_id]);
  const full = read.context?.concepts.find(c => c.concept_id === concept.concept_id);
  return (
    <DetailPanel label="概念詳情" focusKey={concept.concept_id} close={close}>
      <header>
        <div>
          <span className="detail-kicker">{region ? `教材概念 · ${region}` : "教材概念"}</span>
          <h2>{concept.label}</h2>
        </div>
        <button aria-label="關閉概念詳情" className="panel-close" type="button" onClick={close}>
          ×
        </button>
      </header>
      {progress ? <LearningBadge conceptId={concept.concept_id} progress={progress} /> : (
        <span className="map-learning-badge">{isLoadingProgress ? "讀取學習狀態…" : progressMessage ? "學習狀態暫時無法讀取" : "尚未開始"}</span>
      )}
      <DifficultyBadge concept={difficulty?.concepts.find(item => item.concept_id === concept.concept_id)} detail />
      <section>
        <h3>教材重點</h3>
        {read.error ? <p role="alert">{read.error}<button type="button" onClick={read.retry}>重新讀取</button></p>
          : full ? <ConceptContent claims={full.claims} apiClient={apiClient} sourceResolver={view.source_resolver} />
          : <p role="status">正在讀取概念與來源…</p>}
      </section>
      {studyAction}
    </DetailPanel>
  );
}

type ConceptHandle = {
  id: string;
  type: "source" | "target";
  position: Position.Left | Position.Right;
  offset: number;
};
type ConceptNode = Node<{ label: ReactNode; handles: ConceptHandle[] }, "concept">;

function ConceptMapNode({ data }: NodeProps<ConceptNode>) {
  // 節點尺寸由 ResizeObserver 量測；拓樸改變由 MapGraph 一次批次更新。
  return (
    <>
      {data.handles.map((handle) => (
        <Handle
          key={handle.id}
          id={handle.id}
          type={handle.type}
          position={handle.position}
          style={{ top: `${handle.offset}%` }}
        />
      ))}
      {data.label}
    </>
  );
}
const nodeTypes = { concept: ConceptMapNode };

function regionCountText(regions: MapRegions) {
  if (regions.basis !== "chapters") return `${regions.regions.length} 個區域`;
  return (regions.groups.length ? `${regions.groups.length} 個島群・` : "") + `${regions.regions.length} 個章節`;
}

/** 目前概念所在位置；兩層時寫出島群與章節。 */
function whereabouts(regions: MapRegions, conceptId: string) {
  const region = regions.regions[regions.byConcept.get(conceptId) ?? 0];
  const group = region?.group != null ? regions.groups.find((item) => item.index === region.group) : null;
  return (
    <>
      {group && <><b>{group.label}</b> › </>}
      <b>{region?.label}</b>
    </>
  );
}

/** 把範圍內的章節切片依島群合併；章節已依島群排列，同群相鄰。 */
function islandGroups<T extends { index: number }, S>(regions: MapRegions, slices: { group: T; slice: S }[]) {
  const result: { group: MapRegions["groups"][number]; slices: { group: T; slice: S }[] }[] = [];
  for (const item of slices) {
    const group = regions.groups.find((candidate) => candidate.index === regions.regions[item.group.index].group)!;
    if (result.at(-1)?.group !== group) result.push({ group, slices: [] });
    result.at(-1)!.slices.push(item);
  }
  return result;
}

function LearningNavigator({
  view,
  selectedConceptId,
  focusConcept,
  progress,
  regions,
}: {
  progress: MapProgressView | null;
  regions: MapRegions | null;
  view: KnowledgeOutlineView;
  selectedConceptId: string;
  focusConcept: (id: string) => void;
}) {
  const items = useMemo(() => learningNavigationItems(view), [view]);
  // 兩個以上區域才分組；組內維持建議學習順序。單次分桶，避免區域數×概念數的重複篩選。
  const groups = useMemo(() => {
    if (!regions || regions.regions.length <= 1) return null;
    const buckets = regions.regions.map((region) => ({ ...region, items: [] as typeof items }));
    for (const item of items) buckets[regions.byConcept.get(item.concept.concept_id)!]?.items.push(item);
    return buckets;
  }, [items, regions]);
  // 顯示順序（分組時依區域攤平）；千頁教材有數千個概念，一次畫出全部列會讓手機卡住數秒，
  // 因此只畫選取概念附近的一段，兩端可再展開；選取概念移出範圍時重新置中。
  const ordered = useMemo(() => groups ? groups.flatMap((group) => group.items) : items, [groups, items]);
  const positionOf = useMemo(() => new Map(ordered.map((item, index) => [item.concept.concept_id, index])), [ordered]);
  const selectedIndex = positionOf.get(selectedConceptId) ?? 0;
  const [range, setRange] = useState<NavigatorRange | null>(null);
  const shown = navigatorRange(range, selectedIndex, ordered.length);
  if (shown !== range && (range !== null || shown.end - shown.start < ordered.length)) setRange(shown);
  const states = useMemo(
    () => new Map(progress?.concept_states.map((state) => [state.concept_id, state])),
    [progress],
  );
  const firstStep = items[0]?.step;
  const currentStep = view.initial_learning_path.find(
    (step) => step.concept_id === progress?.current_concept_id,
  );
  const completed =
    progress?.concept_states.filter((state) => state.status === "completed").length ?? 0;
  const nextId =
    progress && ["advance", "review_prerequisite", "resume"].includes(progress.next_action.action)
      ? progress.next_action.target_concept_id
      : null;
  const list = useRef<HTMLDivElement>(null);
  const selectedRow = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const rail = list.current!;
    const reveal = () => {
      if (!selectedRow.current || !rail.clientHeight) return;
      const item = selectedRow.current.getBoundingClientRect();
      const bounds = rail.getBoundingClientRect();
      // 目前概念在可見範圍外時捲到清單中間，上下都看得到前後的概念。
      if (item.top < bounds.top || item.bottom > bounds.bottom)
        rail.scrollTop += item.top + item.height / 2 - (bounds.top + bounds.height / 2);
    };
    reveal();
    const observer = new ResizeObserver(reveal);
    observer.observe(rail);
    return () => observer.disconnect();
  }, [selectedConceptId]);
  const renderItem = ({ concept, step }: (typeof items)[number]) => {
    const selected = concept.concept_id === selectedConceptId;
    const learningCurrent = concept.concept_id === progress?.current_concept_id;
    const status = states.get(concept.concept_id)?.status;
    const next = concept.concept_id === nextId && !learningCurrent;
    const stateLabel =
      status === "completed" ? "已完成" : status === "needs_review" ? "需要加強" : "";
    const name = [
      `第 ${step.position} 個，${concept.label}`,
      learningCurrent && "目前學習",
      stateLabel,
      next && "下一步",
    ]
      .filter(Boolean)
      .join("；");
    return (
      <li key={concept.concept_id}>
        <button
          ref={selected ? selectedRow : undefined}
          type="button"
          aria-label={name}
          aria-current={selected ? "true" : undefined}
          className={[
            selected && "is-selected",
            learningCurrent && "is-learning-current",
            status === "completed" && "is-completed",
            status === "needs_review" && "is-needs-review",
            next && "is-next-suggested",
          ]
            .filter(Boolean)
            .join(" ")}
          onClick={() => focusConcept(concept.concept_id)}
        >
          <span className="navigator-position" aria-hidden="true">
            {step.position}
          </span>
          <span className="navigator-concept">
            <span className="navigator-label">{concept.label}</span>
            {(learningCurrent || next) && (
              <span className="navigator-notes">
                {learningCurrent ? (
                  <span className="navigator-current">目前學習</span>
                ) : (
                  <span>下一步</span>
                )}
              </span>
            )}
          </span>
          {stateLabel && (
            <span className="navigator-state" title={stateLabel} aria-hidden="true">
              <Icon name={status === "completed" ? "check" : "warning"} size={15} />
            </span>
          )}
        </button>
      </li>
    );
  };
  return (
    <nav className="focus-navigator surface" aria-label="學習導覽">
      <header>
        <h2>學習導覽</h2>
        <span>{groups ? `${regionCountText(regions!)} · ` : ""}{view.concepts.length} 個概念</span>
      </header>
      {(progress || firstStep) && (
        <p className="navigator-summary">
          {progress ? (
            <>
              {currentStep &&
                `第 ${currentStep.position} / ${view.initial_learning_path.length} 個 · `}
              已完成 {completed} 個
            </>
          ) : (
            `建議從第 ${firstStep!.position} 個概念開始`
          )}
        </p>
      )}
      {groups && <p className="navigator-basis">{regionBasisText(regions!)}</p>}
      {groups && regions!.groups.length > 0 && (
        // 兩層時章節很多（千頁教材約 460 章），清單只畫目前位置附近；用選單跳到任一章的第一個概念。
        <label className="navigator-jump">
          <span>跳到章節</span>
          <select
            value=""
            onChange={(event) => {
              const region = regions!.regions[Number(event.target.value)];
              const first = region && items.find((item) => regions!.byConcept.get(item.concept.concept_id) === region.index);
              if (first) focusConcept(first.concept.concept_id);
            }}
          >
            <option value="" disabled>選擇章節（{regions!.regions.length} 章）</option>
            {regions!.groups.map((group) => (
              <optgroup key={group.id} label={group.label}>
                {group.regionIndexes.map((index) => (
                  <option key={index} value={index}>{regions!.regions[index].label}</option>
                ))}
              </optgroup>
            ))}
          </select>
        </label>
      )}
      <div className="navigator-list" id="focus-concept-list" ref={list}>
        {shown.start > 0 && (
          <button type="button" className="navigator-more" onClick={() => setRange({ start: Math.max(0, shown.start - NAVIGATOR_WINDOW), end: shown.end })}>
            顯示前 {Math.min(NAVIGATOR_WINDOW, shown.start)} 個概念
          </button>
        )}
        {groups ? (
          regions!.groups.length ? (
            // 兩層：來源檔＝島群（h3），章節＝島（h4）；只畫目前範圍內的章節。
            <ul className="navigator-regions">
              {islandGroups(regions!, groupSlices(groups, shown)).map(({ group, slices }) => (
                <li key={group.id} className="navigator-region navigator-island-group">
                  <h3 title={group.label}>
                    <i style={{ background: islandColors(regionPalette(view.material_id, group.index)).c2 }} aria-hidden="true" />
                    <span>{group.label}</span>
                    <small>{group.regionIndexes.length} 章 · {group.conceptCount} 個</small>
                  </h3>
                  <ul className="navigator-chapters">
                    {slices.map(({ group: chapter, slice }) => (
                      <li key={chapter.id} className="navigator-region">
                        <h4 title={chapter.label}>
                          <span>{chapter.label}</span>
                          <small>{chapter.items.length} 個</small>
                        </h4>
                        <ul>{slice.map(renderItem)}</ul>
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          ) : (
          <ul className="navigator-regions">
            {groupSlices(groups, shown).map(({ group, slice }) => (
              <li key={group.id} className="navigator-region">
                <h3 title={group.label}>
                  <i style={{ background: islandColors(regionPalette(view.material_id, regionColorIndex(regions!, group.index))).c2 }} aria-hidden="true" />
                  <span>{group.label}</span>
                  <small>{group.items.length} 個</small>
                </h3>
                <ul>{slice.map(renderItem)}</ul>
              </li>
            ))}
          </ul>
          )
        ) : (
          <ul>{ordered.slice(shown.start, shown.end).map(renderItem)}</ul>
        )}
        {shown.end < ordered.length && (
          <button type="button" className="navigator-more" onClick={() => setRange({ start: shown.start, end: Math.min(ordered.length, shown.end + NAVIGATOR_WINDOW) })}>
            顯示後 {Math.min(NAVIGATOR_WINDOW, ordered.length - shown.end)} 個概念
          </button>
        )}
      </div>
    </nav>
  );
}

function MapGraph({
  apiClient,
  openConcept,
  selectedConceptId,
  view,
  openRelation,
  progress,
  selectedRelationId,
  detail,
  regions,
  toolbar,
  difficulty,
}: {
  apiClient: StudydyApiClient;
  detail: ReactNode;
  difficulty: ConceptDifficultyView | null;
  /** 浮在地圖左上的搜尋與地圖摘要，和「學習導覽」排成同一列。 */
  toolbar: ReactNode;
  regions: MapRegions | null;
  selectedRelationId: string | null;
  progress: MapProgressView | null;
  openConcept: (id: string, restoreFocus?: RestoreFocus) => void;
  selectedConceptId: string;
  view: KnowledgeOutlineView;
  openRelation: (id: string, restoreFocus?: RestoreFocus) => void;
}) {
  const conceptById = useMemo(
    () => new Map(view.concepts.map((concept) => [concept.concept_id, concept])),
    [view.concepts],
  );
  const selected = conceptById.get(selectedConceptId) ?? view.concepts[0];
  const highlightedRelation = view.relations.find(
    (relation) => relation.relation_id === selectedRelationId,
  );
  const graphElement = useRef<HTMLDivElement>(null);
  // 量測可能重建連線；恢復焦點時依穩定 ID 查找，避免保留已移除的 SVG。
  const restoreGraphFocus = (id: string) => {
    const element = graphElement.current?.querySelector<HTMLElement | SVGElement>(
      `[data-id="${CSS.escape(id)}"]`,
    );
    (element ?? navigatorToggle.current)?.focus({ preventScroll: true });
  };
  const [navigatorOpen, setNavigatorOpen] = useState(false);
  const navigatorToggle = useRef<HTMLButtonElement>(null);
  const graph = useReactFlow();
  const initialized = useNodesInitialized();
  // 受控 nodes 必須保留 React Flow 回報的尺寸，避免詳情更新後永遠未完成量測。
  const [measurements, setMeasurements] = useState<
    Record<string, { width: number; height: number }>
  >({});
  const measureNodes = (changes: NodeChange[]) => {
    setMeasurements((previous) => {
      let next = previous;
      for (const change of changes) {
        if (change.type !== "dimensions" || !change.dimensions) continue;
        const before = previous[change.id];
        if (
          before?.width === change.dimensions.width &&
          before?.height === change.dimensions.height
        )
          continue;
        if (next === previous) next = { ...previous };
        next[change.id] = change.dimensions;
      }
      return next;
    });
  };
  const [canvasReady, setCanvasReady] = useState(false);
  const fittedConcept = useRef<string | null>(null);
  const projection = useMemo(
    () => focusGraph(view, selected.concept_id),
    [view, selected.concept_id],
  );
  const layout = projection.nodes;
  const previewRead = useConceptContext(apiClient, view.material_id, view.knowledge_structure_revision, layout.map(node => node.id));

  const updateNodeInternals = useUpdateNodeInternals();
  const measuredLayout = useRef<typeof layout | null>(null);
  useEffect(() => {
    if (!initialized || measuredLayout.current === layout) return;
    measuredLayout.current = layout;
    // 只在換焦點／資料時更新連接點，不依尺寸回寫逐節點重測。
    updateNodeInternals(layout.map((node) => node.id));
  }, [layout, initialized, updateNodeInternals]);
  const { nodes, edges } = useMemo(() => {
    const layoutById = new Map(layout.map((node) => [node.id, node]));
    // 雙向關係共用連接點排序，讓平行連線的標籤與箭頭保持分離。
    const pairRelations = new Map<string, KnowledgeOutlineView["relations"]>();
    for (const relation of projection.relations) {
      const key = [relation.source_concept_id, relation.target_concept_id].sort().join("|");
      const group = pairRelations.get(key) ?? [];
      group.push(relation);
      pairRelations.set(key, group);
    }
    const handlesByNode = new Map<string, ConceptHandle[]>();
    const minimumHeights = new Map<string, number>();
    for (const group of pairRelations.values()) {
      group.sort((a, b) => a.relation_id.localeCompare(b.relation_id));
      group.forEach((relation, index) => {
        const source = layoutById.get(relation.source_concept_id)!;
        const target = layoutById.get(relation.target_concept_id)!;
        const sourcePosition = source.x < target.x ? Position.Right : Position.Left;
        const targetPosition = sourcePosition === Position.Right ? Position.Left : Position.Right;
        for (const [nodeId, type, position] of [
          [source.id, "source", sourcePosition],
          [target.id, "target", targetPosition],
        ] as const) {
          // 平行關係的標籤至少相隔 24px，避免卡片縮短後互相覆蓋。
          minimumHeights.set(
            nodeId,
            Math.max(minimumHeights.get(nodeId) ?? 110, (group.length + 1) * 24),
          );
          const handles = handlesByNode.get(nodeId) ?? [];
          handles.push({
            id: `${relation.relation_id}:${type}`,
            type,
            position,
            offset: ((index + 1) * 100) / (group.length + 1),
          });
          handlesByNode.set(nodeId, handles);
        }
      });
    }
    const palette = islandPalette(view.material_id);
    const position = new Map(view.initial_learning_path.map((step) => [step.concept_id, step.position]));
    const states = new Map(progress?.concept_states.map((state) => [state.concept_id, state.status]));
    const nodes: Node[] = layout.map((node) => {
      const concept = conceptById.get(node.id)!;
      const current = node.id === selected.concept_id;
      return {
        id: node.id,
        type: "concept",
        position: { x: node.x, y: node.y },
        width: node.width,
        measured: measurements[node.id],
        style: {
          width: node.width,
          minHeight: minimumHeights.get(node.id),
          opacity:
            highlightedRelation &&
            ![
              highlightedRelation.source_concept_id,
              highlightedRelation.target_concept_id,
            ].includes(node.id)
              ? 0.5
              : 1,
        },
        data: {
          handles: handlesByNode.get(node.id) ?? [],
          label: (
            <>
              <span className="island-sign">
                {position.has(node.id) && <em aria-hidden="true">{position.get(node.id)}</em>}
                <span>
                  <strong>{concept.label}</strong>
                  <small>
                    <LearningBadge conceptId={concept.concept_id} progress={progress} />
                    <DifficultyBadge concept={difficulty?.concepts.find(item => item.concept_id === concept.concept_id)} hardOnly />
                    {concept.claim_count} 個重點
                  </small>
                </span>
              </span>
              <span className="island-node-art"><IslandArt palette={regions ? regionPalette(view.material_id, regionColorIndex(regions, regions.byConcept.get(node.id)!)) : palette} landmarks={current} /></span>
            </>
          ),
        },
        className: `concept-flow-node is-${states.get(node.id) ?? "not_started"}${current ? " is-focus" : ""}${node.depth === 2 ? " is-secondary" : ""}`,
        ariaLabel: `教材概念：${concept.label}`,
        ariaRole: "button",
        domAttributes: nodeKeyboardAction(
          () => openConcept(node.id, () => restoreGraphFocus(node.id)),
          `${concept.label}：查看概念與教材來源`,
        ),
        draggable: false,
        focusable: true,
      };
    });
    const edges: Edge[] = projection.relations.map((relation) => {
      const source = layoutById.get(relation.source_concept_id)!;
      const target = layoutById.get(relation.target_concept_id)!;
      const { label, color } = relationStyles[relation.type];
      return {
        id: relation.relation_id,
        source: source.id,
        target: target.id,
        type: "default",
        sourceHandle: `${relation.relation_id}:source`,
        targetHandle: `${relation.relation_id}:target`,
        label,
        selected: relation.relation_id === selectedRelationId,
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color,
          width: 16,
          height: 16,
          markerUnits: "userSpaceOnUse",
        },
        labelStyle: { fill: "#fff", fontSize: 11, fontWeight: 800 },
        labelBgStyle: { fill: color },
        labelBgPadding: [7, 3],
        labelBgBorderRadius: 999,
        className: `concept-flow-edge is-relation is-${relation.type}`,
        ariaLabel: `${label}：${previewRead.context?.relations.find(r => r.relation_id === relation.relation_id)?.learner_reason ?? `${conceptById.get(relation.source_concept_id)?.label} → ${conceptById.get(relation.target_concept_id)?.label}`}`,
        focusable: true,
        domAttributes: {
          onKeyDown: (event: KeyboardEvent<SVGGElement>) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              openRelation(relation.relation_id, () => restoreGraphFocus(relation.relation_id));
            }
          },
        },
      };
    });
    return { nodes, edges };
  }, [
    layout,
    projection.relations,
    previewRead.context,
    previewRead.error,
    selected.concept_id,
    highlightedRelation,
    difficulty,
    conceptById,
    progress,
    openConcept,
    openRelation,
    measurements,
    regions,
  ]);
  // resize 只保留世界座標中心；使用者按適應畫面才重新 fit。
  useEffect(() => {
    const element = graphElement.current!;
    let width = element.clientWidth;
    let height = element.clientHeight;
    const observer = new ResizeObserver(() => {
      const nextWidth = element.clientWidth,
        nextHeight = element.clientHeight;
      setCanvasReady(nextWidth > 0 && nextHeight > 0);
      if (
        width &&
        height &&
        nextWidth &&
        nextHeight &&
        (width !== nextWidth || height !== nextHeight)
      ) {
        const viewport = graph.getViewport();
        void graph.setViewport({
          ...viewport,
          x: viewport.x + (nextWidth - width) / 2,
          y: viewport.y + (nextHeight - height) / 2,
        });
      }
      width = nextWidth;
      height = nextHeight;
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [graph]);
  useEffect(() => {
    if (!initialized || !canvasReady || (!previewRead.context && !previewRead.error)
      || fittedConcept.current === selected.concept_id) return;
    let secondFrame = 0;
    const frame = requestAnimationFrame(() => {
      // 按需文字到達後，先讓節點量測更新，再計算第一次視野。
      secondFrame = requestAnimationFrame(() => {
        fittedConcept.current = selected.concept_id;
        // 畫布本身太窄（依畫布寬度而非視窗斷點），初次視野只框住中心與第一層，其餘可平移或縮小查看。
        const narrow = graphElement.current!.clientWidth < 600;
        void graph.fitView(narrow ? { ...fitViewOptions, minZoom: 0.5, nodes: layout.filter((node) => node.depth < 2).map((node) => ({ id: node.id })) } : fitViewOptions);
      });
    });
    return () => { cancelAnimationFrame(frame); cancelAnimationFrame(secondFrame); };
  }, [initialized, canvasReady, graph, selected.concept_id, previewRead.context, previewRead.error, layout]);
  return (
    <section
      className={`focus-workspace${detail ? " has-inspector" : ""}`}
      aria-label="知識地圖工作區"
    >
      <div
        className="focus-graph surface"
        ref={graphElement}
        aria-label={`「${selected.label}」的兩層相關概念`}
      >
        <ReactFlow
          nodeTypes={nodeTypes}
          nodes={nodes}
          edges={edges}
          onNodesChange={measureNodes}
          proOptions={{ hideAttribution: true }}
          ariaLabelConfig={{
            "controls.zoomIn.ariaLabel": "放大地圖",
            "controls.zoomOut.ariaLabel": "縮小地圖",
            "controls.fitView.ariaLabel": "適應畫面",
            "node.a11yDescription.default": "按 Enter 或空白鍵查看概念。",
            "edge.a11yDescription.default": "按 Enter 或空白鍵查看關係說明。",
          }}
          minZoom={0.001}
          maxZoom={1.8}
          zoomOnScroll={true}
          preventScrolling={true}
          nodesConnectable={false}
          nodesDraggable={false}
          edgesReconnectable={false}
          onNodeClick={(_, node) => openConcept(node.id, () => restoreGraphFocus(node.id))}
          onEdgeClick={(_, edge) => openRelation(edge.id, () => restoreGraphFocus(edge.id))}
        >

          <Controls
            aria-label="地圖縮放控制"
            showInteractive={false}
            fitViewOptions={fitViewOptions}
          />
        </ReactFlow>
        <div
          className="map-navigation"
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.preventDefault();
              setNavigatorOpen(false);
              navigatorToggle.current?.focus();
            }
          }}
        >
          <div className="map-toolbar">
            {toolbar}
            <button
              ref={navigatorToggle}
              className="secondary-button map-navigator-toggle"
              type="button"
              aria-expanded={navigatorOpen}
              aria-controls="map-navigator"
              onClick={() => {
                navigatorToggle.current?.focus();
                setNavigatorOpen((value) => !value);
              }}
            >
              <Icon name="route" size={18} />
              學習導覽
            </button>
          </div>
          <div id="map-navigator" hidden={!navigatorOpen}>
            <LearningNavigator
              regions={regions}
              progress={progress}
              view={view}
              selectedConceptId={selected.concept_id}
              focusConcept={(id) => {
                setNavigatorOpen(false);
                openConcept(id, () => navigatorToggle.current?.focus());
              }}
            />
          </div>
          {/* 地圖一次只畫目前概念附近兩層；標出所在區域，全部區域清單從「學習導覽」開啟。 */}
          {!navigatorOpen && regions && regions.regions.length > 1 && (
            <p className="map-whereabouts">目前在 {whereabouts(regions, selected.concept_id)}</p>
          )}
        </div>
        <div className="map-annotations">
          <div className="relation-legend" aria-label="概念關係圖例">
            {Object.entries(relationStyles).map(([type, { label, bridge, color }]) => (
              <span key={type} style={{ color }}>
                <i className={`relation-swatch is-${type}`} aria-hidden="true" />
                {label}<small>{bridge}</small>
              </span>
            ))}
          </div>
          {projection.totalNodes <= 1 && view.concepts.length > 1 && (
            <p className="map-limit-note" role="status">
              這個概念目前沒有直接相連的概念。可從「學習導覽」選擇其他{regions && regions.regions.length > 1 ? "區域或" : ""}概念繼續探索。
            </p>
          )}
          {(nodes.length < projection.totalNodes || edges.length < projection.totalRelations) && (
            <p className="map-limit-note" role="status">
              兩層範圍：{nodes.length}／{projection.totalNodes} 個概念、{edges.length}／
              {projection.totalRelations} 條關係。 尚有其他相關內容，可用搜尋或學習導覽選取。
            </p>
          )}
        </div>
      </div>
      {detail && (
        <aside className="focus-context surface" aria-label="地圖詳情">
          {detail}
        </aside>
      )}
    </section>
  );
}

function RelationDetail({
  relation,
  view,
  apiClient,
  close,
  openConcept,
}: {
  relation: KnowledgeOutlineView["relations"][number];
  view: KnowledgeOutlineView;
  apiClient: StudydyApiClient;
  close: () => void;
  openConcept: (id: string) => void;
}) {
  const read = useConceptContext(apiClient, view.material_id, view.knowledge_structure_revision, [relation.source_concept_id]);
  const full = read.context?.relations.find(item => item.relation_id === relation.relation_id);
  const evidence = (read.context?.relation_evidence ?? []).filter(item => full?.evidence_refs.includes(item.evidence_id));
  return (
    <DetailPanel label="關係詳情" focusKey={relation.relation_id} close={close}>
      <header>
        <div>
          <span className="detail-kicker">概念之間的關係</span>
          <h2>{relationStyles[relation.type].label}</h2>
        </div>
        <button className="panel-close" type="button" aria-label="關閉關係詳情" onClick={close}>
          ×
        </button>
      </header>
      <section className="relation-direction">
        {[relation.source_concept_id, relation.target_concept_id].map((id, index) => (
          <div key={index}>
            {index === 1 && <span aria-hidden="true">↓</span>}
            <button className="detail-related" type="button" onClick={() => openConcept(id)}>
              <small>{index === 0 ? "來源概念" : "目標概念"}</small>
              <strong>{view.concepts.find((concept) => concept.concept_id === id)?.label}</strong>
            </button>
          </div>
        ))}
      </section>
      <section>
        <h3>為什麼有這個關係？</h3>
        <p className="claim-text">{full?.learner_reason ?? (read.error ? "暫時無法讀取關係說明。" : "正在讀取關係說明…")}</p>
      </section>
      <section>
        <h3>教材來源</h3>
        {sourceLinks(evidence).map((item) => (
          <SourceButton
            key={item.evidence_id}
            apiClient={apiClient}
            resolver={view.source_resolver}
            evidence={item}
          />
        ))}
        {read.error ? <p role="alert">{read.error}<button type="button" onClick={read.retry}>重新讀取</button></p>
          : !read.context ? <p role="status">正在讀取關係來源…</p>
          : evidence.length === 0 && <p>可從來源與目標概念查看相關教材。</p>}
      </section>
    </DetailPanel>
  );
}

function ReviewView({ view, progress, startStudy, busyLabel, loading, returnToMap }: {
  view: KnowledgeOutlineView;
  progress: MapProgressView | null;
  startStudy: (id: string) => void;
  busyLabel: string | null;
  loading: boolean;
  returnToMap: () => void;
}) {
  const states = new Map(progress?.concept_states.map(state => [state.concept_id, state]));
  // attempts 由後端正式答案事件計算；閱讀、準備題組與出題失敗都不算作答。
  const attempted = view.concepts.filter(concept => (states.get(concept.concept_id)?.attempts ?? 0) > 0);
  const pending = attempted.filter(concept => states.get(concept.concept_id)?.status !== "completed");
  const completed = attempted.filter(concept => states.get(concept.concept_id)?.status === "completed");
  const list = (concepts: Concept[], practice: boolean) => <ul className="assessment-concept-list">
    {concepts.map(concept => {
      const state = states.get(concept.concept_id);
      return <li key={concept.concept_id} className={`is-${state?.status ?? "not_started"}`}>
        <span className="assessment-concept-icon" aria-hidden="true"><Icon name={practice ? "check" : "quiz"} size={20} /></span>
        <div><strong>{concept.label}</strong>
          <span className="assessment-concept-meta"><LearningBadge conceptId={concept.concept_id} progress={progress}/>{state?.attempts ? <small>已作答 {state.attempts} 題</small> : null}</span></div>
        <button className={practice ? "secondary-button" : "primary-button"} disabled={!!busyLabel}
          onClick={() => startStudy(concept.concept_id)}>{busyLabel ?? (practice ? "再次練習" : "繼續測驗")}</button>
      </li>;
    })}
  </ul>;
  return <section className="review-records" aria-labelledby="review-title">
    <header className="review-records-head">
      <div><h2 id="review-title">測驗紀錄</h2>
        <p>查看作答結果、繼續未完成的檢測，或再次練習已完成的概念。</p></div>
      {!loading && attempted.length > 0 && <dl className="review-stats" aria-label="測驗摘要">
        <div><dt>已作答概念</dt><dd>{attempted.length}<small> / {view.concepts.length}</small></dd></div>
        <div className="is-pending"><dt>待完成</dt><dd>{pending.length}</dd></div>
        <div className="is-done"><dt>已完成</dt><dd>{completed.length}</dd></div>
      </dl>}
    </header>
    {loading ? <p role="status">正在讀取學習進度…</p> : attempted.length === 0 ? <>
      <EmptyState label="開始學習引導" title="還沒有測驗紀錄" description="目前還沒有測驗紀錄，可以從知識地圖選擇概念開始學習。"
        action={<button className="primary-button" type="button" onClick={returnToMap}>回到知識地圖</button>} />
    </> : <>
      {pending.length > 0 && <h3>尚未完成／需要加強</h3>}
      {pending.length ? list(pending, false) : <p className="review-records-clear">已有作答紀錄的概念都已完成。</p>}
      {completed.length > 0 && <details className="review-records-done"><summary>已完成的概念 · 再次練習（{completed.length}）</summary>{list(completed, true)}</details>}
    </>}
  </section>;
}

export function KnowledgeMapWorkspace({
  apiClient,
  progress,
  isLoadingProgress,
  progressMessage,
  onReloadProgress,
  isStartingStudy,
  onReturnToRun,
  mapRoute,
  materialName,
  onStartStudy,
  startMessage,
  view,
}: {
  apiClient: StudydyApiClient;
  progress: MapProgressView | null;
  learningStateStatus: StudySessionView["status"] | null;
  isLoadingProgress: boolean;
  progressMessage: string | null;
  onReloadProgress: () => void;
  isStartingStudy: boolean;
  onReturnToRun: () => void;
  mapRoute: Extract<AppRoute, { name: "knowledge-map" }>;
  materialName: string;
  onStartStudy: (conceptId: string) => void;
  startMessage: string | null;
  view: KnowledgeOutlineView;
}) {
  const difficulty = useConceptDifficulty(apiClient, mapRoute.materialId, view.knowledge_structure_revision);
  const initialConceptId = progress?.current_concept_id ?? initialFocusConceptId(view);
  // 只記住目前瀏覽器紀錄的呈現位置；登入、作答與 learner progress 仍由 API 管理。
  const [restored] = useState(() => {
    const saved = window.history.state?.knowledgeMap;
    if (saved?.revision !== view.knowledge_structure_revision) return null;
    const conceptId = (id: unknown) =>
      typeof id === "string" && view.concepts.some((concept) => concept.concept_id === id)
        ? id
        : null;
    return {
      mode: saved.mode === "review" ? ("review" as const) : ("focus" as const),
      search: typeof saved.search === "string" ? saved.search : "",
      concept: conceptId(saved.concept),
      review: conceptId(saved.review),
      detail: conceptId(saved.detail),
      relation:
        typeof saved.relation === "string" &&
        view.relations.some((relation) => relation.relation_id === saved.relation)
          ? (saved.relation as string)
          : null,
    };
  });
  const [mode, setMode] = useState<Mode>(restored?.mode ?? "focus");
  const [searchQuery, setSearchQuery] = useState(restored?.search ?? "");
  const [selectedConceptId, setSelectedConceptId] = useState(restored?.concept ?? initialConceptId);
  const [reviewConceptId] = useState(restored?.review ?? initialConceptId);
  const [detailConceptId, setDetailConceptId] = useState<string | null>(restored?.detail ?? null);
  const [relationId, setRelationId] = useState<string | null>(restored?.relation ?? null);
  useEffect(() => {
    window.history.replaceState(
      {
        ...window.history.state,
        knowledgeMap: {
          revision: view.knowledge_structure_revision,
          mode,
          search: searchQuery,
          concept: selectedConceptId,
          review: reviewConceptId,
          detail: detailConceptId,
          relation: relationId,
        },
      },
      "",
      window.location.href,
    );
  }, [
    view.knowledge_structure_revision,
    mode,
    searchQuery,
    selectedConceptId,
    reviewConceptId,
    detailConceptId,
    relationId,
  ]);
  const opener = useRef<RestoreFocus | null>(null);
  const restoreFrame = useRef(0);
  useEffect(() => () => cancelAnimationFrame(restoreFrame.current), []);
  const searchInput = useRef<HTMLInputElement>(null);
  const searchResultsElement = useRef<HTMLDivElement>(null);
  const tabs = useRef(new Map<Mode, HTMLButtonElement>());
  const hasBrowsed = useRef(!!restored?.concept);
  useEffect(() => {
    if (
      !hasBrowsed.current &&
      progress?.current_concept_id &&
      view.concepts.some((concept) => concept.concept_id === progress.current_concept_id)
    ) {
      setSelectedConceptId(progress.current_concept_id);
    }
  }, [progress?.study_session_id, progress?.current_concept_id, view.concepts]);
  const selectedRelation = view.relations.find((relation) => relation.relation_id === relationId);
  const query = searchQuery.trim().toLocaleLowerCase();
  const [searchState, setSearchState] = useState<{ query: string; ids: string[]; error: string | null }>({ query: "", ids: [], error: null });
  useEffect(() => {
    let cancelled = false;
    if (!query) return;
    const timer = window.setTimeout(() => {
      void apiClient.searchConcepts({ materialId: mapRoute.materialId, structureRevision: mapRoute.structureRevision }, query)
        .then(result => { if (!cancelled) setSearchState({ query, ids: result.concept_ids, error: null }); },
          error => { if (!cancelled) setSearchState({ query, ids: [], error: errorMessage(error) }); });
    }, 150);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [apiClient, mapRoute.materialId, mapRoute.structureRevision, query]);
  const matchedIds = new Set(searchState.query === query ? searchState.ids : []);
  const searchResults = query ? view.concepts.filter(concept => matchedIds.has(concept.concept_id)) : [];
  const selectedConcept = useMemo(
    () => view.concepts.find((concept) => concept.concept_id === detailConceptId) ?? null,
    [detailConceptId, view.concepts],
  );
  // 區域只是呈現分組；讀取失敗或與地圖不一致時維持不分區的地圖。
  const [regionsView, setRegionsView] = useState<KnowledgeRegionsView | null>(null);
  useEffect(() => {
    let cancelled = false;
    setRegionsView(null);
    void apiClient.getKnowledgeRegions({ materialId: view.material_id, structureRevision: view.knowledge_structure_revision })
      .then((value) => { if (!cancelled) setRegionsView(value); }, () => undefined);
    return () => { cancelled = true; };
  }, [apiClient, view.material_id, view.knowledge_structure_revision]);
  const regions = useMemo(() => mapRegions(view, regionsView), [view, regionsView]);
  const regionLabel = (id: string) =>
    regions && regions.regions.length > 1 ? regions.regions[regions.byConcept.get(id)!].label : null;

  if (view.concepts.length === 0)
    return (
      <StateView
        action={
          <div className="state-actions">
            <button className="secondary-button" type="button" onClick={onReturnToRun}>
              <Icon name="arrow-left" />
              查看處理狀態
            </button>
          </div>
        }
        description="這份教材目前沒有可供學習的概念。請返回處理結果查看原因。"
        image="/assets/studydy/empty-disappointed.png"
        title="知識地圖目前是空的"
        tone="empty"
      />
    );
  const rememberOpener = (restoreFocus?: RestoreFocus) => {
    // 快速關閉再開啟詳情時，不讓前一次延遲恢復搶走新面板的焦點。
    cancelAnimationFrame(restoreFrame.current);
    if (restoreFocus) {
      opener.current = restoreFocus;
      return;
    }
    const element = document.activeElement as HTMLElement | null;
    if (element?.closest(".detail-panel")) return;
    opener.current = () => {
      if (element?.isConnected) element.focus({ preventScroll: true });
      else tabs.current.get(mode)?.focus({ preventScroll: true });
    };
  };
  const openConceptDetail = (id: string, restoreFocus?: RestoreFocus) => {
    hasBrowsed.current = true;
    rememberOpener(restoreFocus);
    setSelectedConceptId(id);
    setDetailConceptId(id);
    setRelationId(null);
  };
  const chooseSearchResult = (id: string) => {
    setMode("focus");
    openConceptDetail(id);
    setSearchQuery("");
    searchInput.current?.focus({ preventScroll: true });
  };
  const openRelation = (id: string, restoreFocus?: RestoreFocus) => {
    rememberOpener(restoreFocus);
    setRelationId(id);
    setDetailConceptId(null);
  };
  const closeDetail = () => {
    const closingFocus = document.activeElement;
    setDetailConceptId(null);
    setRelationId(null);
    // 等 React Flow 完成下一幀量測後，再恢復節點或連線焦點。
    cancelAnimationFrame(restoreFrame.current);
    restoreFrame.current = requestAnimationFrame(() => {
      restoreFrame.current = requestAnimationFrame(() => {
        // 使用者已開始搜尋等新操作時，不以延遲恢復搶走焦點。
        if (document.activeElement !== closingFocus && document.activeElement !== document.body)
          return;
        if (opener.current) opener.current();
        else tabs.current.get(mode)?.focus({ preventScroll: true });
      });
    });
  };
  const selectMode = (nextMode: Mode) => {
    cancelAnimationFrame(restoreFrame.current);
    setMode(nextMode);
    setDetailConceptId(null);
    setRelationId(null);
    window.requestAnimationFrame(() => tabs.current.get(nextMode)?.focus());
  };
  const busyStudyLabel = isLoadingProgress ? "讀取學習進度…" : isStartingStudy ? "正在開始…" : null;
  const focusStudyConceptId = selectedConceptId;
  const focusStudyButtonLabel =
    busyStudyLabel ??
    (progress?.concept_states.find(state => state.concept_id === focusStudyConceptId)?.status === "completed"
      ? "再次練習"
      : "檢測這個概念");
  const focusStudyAction = (
    <section className="concept-study-action" aria-label="學習入口">
      <button
        className="primary-button"
        disabled={isStartingStudy || isLoadingProgress}
        type="button"
        onClick={() => onStartStudy(focusStudyConceptId)}
      >
        <Icon name="learning" />
        {focusStudyButtonLabel}
      </button>
    </section>
  );
  const detail =
    selectedRelation || selectedConcept ? (
      <>
        {selectedRelation && (
          <RelationDetail
            relation={selectedRelation}
            view={view}
            apiClient={apiClient}
            close={closeDetail}
            openConcept={openConceptDetail}
          />
        )}
        {selectedConcept && (
          <ConceptDetail
            apiClient={apiClient}
            close={closeDetail}
            view={view}
            concept={selectedConcept}
            studyAction={focusStudyAction}
            progress={progress}
            isLoadingProgress={isLoadingProgress}
            progressMessage={progressMessage}
            region={regionLabel(selectedConcept.concept_id)}
            difficulty={difficulty.data}
          />
        )}
      </>
    ) : null;
  const mapToolbar = (
    <>
        <form
          className="map-search"
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              // 只清空搜尋；不讓外層的學習導覽把焦點移走。
              event.stopPropagation();
              setSearchQuery("");
              searchInput.current?.focus();
            }
          }}
          onBlur={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget)) setSearchQuery("");
          }}
          role="search"
          aria-label="搜尋教材概念"
          onSubmit={(event) => {
            event.preventDefault();
            if (searchResults[0]) chooseSearchResult(searchResults[0].concept_id);
          }}
        >
          <input
            ref={searchInput}
            type="search"
            aria-label="搜尋概念或關鍵字"
            placeholder="搜尋概念或關鍵字…"
            maxLength={200}
            value={searchQuery}
            onChange={(event) => setSearchQuery(event.currentTarget.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape") setSearchQuery("");
              if (event.key === "ArrowDown") {
                event.preventDefault();
                searchResultsElement.current?.querySelector<HTMLButtonElement>("button")?.focus();
              }
            }}
          />
          {query && (
            <div ref={searchResultsElement} className="map-search-results">
              <p role="status">
                {searchState.query !== query ? "正在搜尋教材內容…" : searchState.error ? searchState.error : searchResults.length
                  ? `找到 ${searchResults.length} 個概念${searchResults.length > 8 ? "，顯示前 8 個，請輸入更多關鍵字縮小範圍" : ""}`
                  : "找不到符合的概念，試試其他關鍵字。"}
              </p>
              {searchResults.slice(0, 8).map((concept) => (
                <button
                  key={concept.concept_id}
                  type="button"
                  onClick={() => chooseSearchResult(concept.concept_id)}
                >
                  <strong>{concept.label}</strong>
                  <small>{concept.claim_count} 個重點</small>
                </button>
              ))}
            </div>
          )}
        </form>
          <div className="map-facts" aria-label="地圖摘要">
            <span>
              <strong>{view.concepts.length}</strong>概念
            </span>
            <span>
              <strong>{view.relations.length}</strong>關係
            </span>
            {regions && regions.regions.length > 1 && (
              <span title={regionBasisText(regions)}>
                <strong>{regions.regions.length}</strong>{regions.basis === "chapters" ? "章節" : "區域"}
              </span>
            )}
          </div>
    </>
  );
  return (
    <section
      className={`map-workspace${mode === "focus" ? " is-focus-mode" : ""}${selectedConcept || selectedRelation ? " has-detail" : ""}`}
    >
      <MaterialContentNav apiClient={apiClient} materialId={mapRoute.materialId} mapRoute={mapRoute} materialName={materialName} current={mode === "review" ? "review" : "knowledge-map"} assessmentDisabled={isStartingStudy || isLoadingProgress} onMapModeChange={selectMode} onMapTabRef={(id, element) => { if (element) tabs.current.set(id, element); else tabs.current.delete(id); }}/>
      {progressMessage && (
        <div className="partial-banner" role="status">
          <span>{progressMessage}</span>
          <button className="text-button" type="button" onClick={onReloadProgress}>
            重新讀取進度
          </button>
        </div>
      )}
      {startMessage && (
        <p className="map-start-error" role="alert">
          {startMessage}
        </p>
      )}
      {view.excluded_pages.length > 0 && (
        <p className="form-error" role="status">
          第 {view.excluded_pages.map((item) => item.page).join("、")}{" "}
          頁未納入概念與練習，請從原始教材閱讀。
        </p>
      )}
      <div className="map-content">
        <div
          aria-labelledby={`map-tab-${mode}`}
          className="map-view"
          id={`map-panel-${mode}`}
          role="tabpanel"
          tabIndex={0}
        >
          {mode === "focus" && (
            <ReactFlowProvider>
              <MapGraph
                apiClient={apiClient}
                selectedRelationId={relationId}
                progress={progress}
                openRelation={openRelation}
                openConcept={openConceptDetail}
                selectedConceptId={selectedConceptId}
                detail={detail}
                view={view}
                regions={regions}
                toolbar={mapToolbar}
                difficulty={difficulty.data}
              />
            </ReactFlowProvider>
          )}
          {mode === "review" && <div className="review-diagnosis">
            <LearningDiagnosis api={apiClient} materialId={mapRoute.materialId} revision={view.knowledge_structure_revision} onReview={onStartStudy} disabled={isStartingStudy} />
            <DifficultyList report={difficulty} onReview={onStartStudy} disabled={isStartingStudy} />
          </div>}
          {mode === "review" &&
            (progressMessage ? (
              <div className="review-empty">
                <div>
                  <h2>暫時無法顯示複習重點</h2>
                  <p>重新讀取進度後，即可查看最近一次學習的複習方向。</p>
                  <button className="primary-button" type="button" onClick={onReloadProgress}>
                    重新讀取進度
                  </button>
                </div>
              </div>
            ) : (
              <ReviewView
                loading={isLoadingProgress}
                returnToMap={() => selectMode("focus")}
                view={view}
                progress={progress}
                startStudy={onStartStudy}
                busyLabel={busyStudyLabel}
              />
            ))}
        </div>
      </div>
    </section>
  );
}
