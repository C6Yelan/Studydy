import { MapExports } from "../exports/MapExports";
import { openNoteTarget, useConceptNoteTarget } from "../notes/note-location";
import { relationStyles } from "./map-appearance";
import { DifficultyBadge, LearningDiagnosis, useLearningDiagnosis, useConceptDifficulty } from "../learning-diagnosis/LearningDiagnosis";
import { GoalEditorHost, LearningGoalBlock, TodayConceptsBlock, useLearningGoal } from "../learning-goals/LearningGoalPanel";
import { DailyGoalProgress } from "../learning-goals/DailyGoalProgress";
import type { AppRoute } from "../../app/routes";
import { SourceButton, sourceLinks } from "../../ui/SourceButton";
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode, type KeyboardEvent } from "react";
import {
  Background,
  BaseEdge,
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
  type EdgeProps,
  type NodeChange,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import type { StudydyApiClient } from "../../api/client";
import type {
  KnowledgeOutlineView,
  KnowledgeRegionsView,
  MapProgressView,
  ConceptDifficultyView,
  StudySessionView,
} from "../../api/contracts";
import { useConceptContext } from "../material-flow/useConceptContext";
import { errorMessage } from "../../api/client";
import { ConceptClaims, ConceptSources } from "../../ui/ConceptContent";
import { Icon } from "../../ui/Icon";
import { MathText } from "../../ui/MathText";
import { mathPlainText } from "../../ui/math-text";
import { Progress } from "../../ui/Progress";
import { IslandArt, islandColors, islandPalette, regionPalette } from "../../ui/art/Island";
import { learningNavigationItems, focusGraph, groupSlices, initialFocusConceptId, mapRegions, navigatorRange, NAVIGATOR_WINDOW, regionBasisText, regionColorIndex, frameCamera, nextStationId, relationPhrase, routeFrameIds, routeStationIds, relationTitle, relationVisibility, routeSegments, frameRouteCamera, type MapRegions, type NavigatorRange } from "./knowledge-map";

type Concept = KnowledgeOutlineView["concepts"][number];
type RestoreFocus = () => void;

const fitViewOptions = { padding: 0.18, minZoom: 0.001, maxZoom: 1.1 };
// 手機的詳情卡片是佔視窗下半部的抽屜（styles.css .detail-panel 的 50dvh）；取景只用畫布露出的上半部。
const phoneDrawerShare = 0.5;
function visibleCanvas(element: HTMLElement, drawerOpen: boolean) {
  // 上方工具列（搜尋、學習導覽、匯出鈕）蓋住的那一條，取景時不能用。
  const toolbarBottom = Math.max(0, ...[...element.querySelectorAll(".map-toolbar, .map-corner-tools > .export-entry")]
    .map((tool) => tool.getBoundingClientRect().bottom - element.getBoundingClientRect().top));
  const size = { width: element.clientWidth, height: element.clientHeight, top: toolbarBottom };
  if (!drawerOpen || !window.matchMedia("(max-width: 720px)").matches) return size;
  const covered = element.getBoundingClientRect().bottom - window.innerHeight * (1 - phoneDrawerShare);
  return { ...size, height: Math.max(120, size.height - Math.max(0, covered)) };
}

const learningLabels = {
  not_started: "尚未開始",
  in_progress: "進行中",
  needs_review: "要再練",
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
  openRelation,
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
  openRelation: (id: string) => void;
}) {
  const read = useConceptContext(apiClient, view.material_id, view.knowledge_structure_revision, [concept.concept_id]);
  const full = read.context?.concepts.find(c => c.concept_id === concept.concept_id);
  const noteTarget = useConceptNoteTarget(apiClient, view.material_id, view.knowledge_structure_revision, concept.concept_id);
  const station = view.initial_learning_path.find((step) => step.concept_id === concept.concept_id)?.position;
  const relations = view.relations.filter((relation) =>
    relation.source_concept_id !== relation.target_concept_id
    && (relation.source_concept_id === concept.concept_id || relation.target_concept_id === concept.concept_id));
  // 重點原文平常每條只露出兩行；換一座島就回到收合。
  const [expandedFor, setExpandedFor] = useState<string | null>(null);
  const expanded = expandedFor === concept.concept_id;
  const points = useRef<HTMLDivElement>(null);
  const [clipped, setClipped] = useState(false);
  useLayoutEffect(() => {
    const element = points.current;
    if (!element) return;
    // 只有真的被截掉時才出現「展開全文」；面板寬度改變會重新判斷。
    const check = () => setClipped([...element.querySelectorAll(".claim-text")].some((text) => text.scrollHeight > text.clientHeight + 1));
    check();
    const observer = new ResizeObserver(check);
    observer.observe(element);
    return () => observer.disconnect();
  }, [full, expanded]);
  return (
    <DetailPanel label="概念詳情" focusKey={concept.concept_id} close={close}>
      <header>
        <div>
          <span className="detail-kicker">{[station && `第 ${station} 站`, `${concept.claim_count} 個重點`, region].filter(Boolean).join(" · ")}</span>
          <h2><MathText text={concept.label} notation={false} /></h2>
        </div>
        <button aria-label="關閉概念詳情" className="panel-close" type="button" onClick={close}>
          ×
        </button>
      </header>
      {/* 照示意圖：標題和狀態下面緊接「開始挑戰」，難度與作答率放在按鈕下方。 */}
      <div className="detail-badges">
        {progress ? <LearningBadge conceptId={concept.concept_id} progress={progress} /> : (
          <span className="map-learning-badge">{isLoadingProgress ? "讀取學習狀態…" : progressMessage ? "學習狀態暫時無法讀取" : "尚未開始"}</span>
        )}
      </div>
      {studyAction}
      <div className="detail-difficulty">
        <DifficultyBadge concept={difficulty?.concepts.find(item => item.concept_id === concept.concept_id)} detail />
      </div>
      {full?.uncertain === true && <p className="flow-notice">影片逐字稿可能有聽錯的詞，建議對照原影片</p>}
      <section className={`detail-points${expanded ? "" : " is-clamped"}`}>
        <h3>{full ? `${full.claims.length} 個重點（教材原文）` : "教材重點"}</h3>
        {read.error ? <p role="alert">{read.error}<button type="button" onClick={read.retry}>重新讀取</button></p>
          : full ? <div ref={points}><ConceptClaims claims={full.claims} compact /></div>
          : <p role="status">正在讀取概念與來源…</p>}
        {full && (expanded || clipped) && (
          <button type="button" className="text-button detail-points-toggle" aria-expanded={expanded}
            onClick={() => setExpandedFor(expanded ? null : concept.concept_id)}>
            {expanded ? "收合重點" : "展開全文"}
          </button>
        )}
      </section>
      {relations.length > 0 && (
        <section className="detail-relations">
          <h3>跟其他島的關係</h3>
          <ul>
            {relations.map((relation) => {
              const phrase = relationPhrase(relation, concept.concept_id);
              const other = view.concepts.find((item) => item.concept_id === phrase.otherId)?.label ?? "";
              const style = relationStyles[relation.type];
              return (
                <li key={relation.relation_id}>
                  <button type="button" aria-label={`${style.label}：${phrase.before}${mathPlainText(other)}${phrase.after}，查看關係說明`}
                    onClick={() => openRelation(relation.relation_id)}>
                    <b style={{ background: style.color }}>{style.label}</b>
                    <span>{phrase.before}<MathText text={other} notation={false} />{phrase.after}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      )}
      {full && <ConceptSources claims={full.claims} apiClient={apiClient} sourceResolver={view.source_resolver} />}
      {noteTarget && <button type="button" className="secondary-button detail-note-link" onClick={() => openNoteTarget(view.material_id, noteTarget)}>在筆記中查看</button>}
    </DetailPanel>
  );
}

type ConceptHandle = {
  id: string;
  type: "source" | "target";
  position: Position.Left | Position.Right;
  offset: number;
};
// 金色航線的接點放在島嶼插畫中央（節點高度的比例），讓航線從島的中間穿出。
const ROUTE_HANDLE_OFFSET = 62;
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
      {(["source", "target"] as const).map((type) => (
        <Handle key={type} id={`route:${type}`} type={type} position={Position.Left} className="map-route-handle"
          style={{ top: `${ROUTE_HANDLE_OFFSET}%`, left: "50%" }} />
      ))}
      {/* 島插畫右緣的接點（左右位置依插畫寬度寫在 styles.css），下一站搬到正下方時航線接這裡。 */}
      {(["source", "target"] as const).map((type) => (
        <Handle key={`side-${type}`} id={`route-side:${type}`} type={type} position={Position.Right} className="map-route-side-handle"
          style={{ top: `${ROUTE_HANDLE_OFFSET}%` }} />
      ))}
      {data.label}
    </>
  );
}
const nodeTypes = { concept: ConceptMapNode };

/**
 * 金色航線：微彎的寬帶，帶子中點畫一個往下一站的淡箭頭，看得出往哪走。
 * 往返方向相反的兩段會彎向不同側；排版上剛好擋在中間的島也比較不會被直線穿過。
 */
function RouteEdge({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition }: EdgeProps) {
  if (sourcePosition === Position.Right && targetPosition === Position.Right) {
    // 接兩座島右緣：往右繞出再水平接回，避開下一站的名稱卡（卡片比插畫寬，正上方接下來一定會穿過它）。
    // 140 讓弧線在名稱卡那一段離卡片右緣夠遠，又不碰到右邊第一層（中心島右緣再過 120 起排）。
    const bend = 140;
    const [c1x, c2x] = [sourceX + bend, targetX + bend];
    const path = `M${sourceX},${sourceY}C${c1x},${sourceY} ${c2x},${targetY} ${targetX},${targetY}`;
    const midX = (sourceX + 3 * c1x + 3 * c2x + targetX) / 8, midY = (sourceY + targetY) / 2;
    const angle = (Math.atan2(1.5 * (targetY - sourceY), 0.75 * (c1x - sourceX) + 1.5 * (c2x - c1x) + 0.75 * (targetX - c2x)) * 180) / Math.PI;
    return (
      <>
        <BaseEdge id={id} path={path} interactionWidth={0} />
        <path className="map-route-core" d={path} />
        <path className="map-route-arrow" d="M-9,-11L9,0L-9,11L-3,0Z" transform={`translate(${midX},${midY}) rotate(${angle})`} />
      </>
    );
  }
  const dx = targetX - sourceX, dy = targetY - sourceY;
  // 控制點沿垂直方向偏移線長的 30%，弧頂約偏 15%。
  const controlX = (sourceX + targetX) / 2 - dy * 0.3, controlY = (sourceY + targetY) / 2 + dx * 0.3;
  const midX = (sourceX + 2 * controlX + targetX) / 4, midY = (sourceY + 2 * controlY + targetY) / 4;
  const angle = (Math.atan2(dy, dx) * 180) / Math.PI;
  const path = `M${sourceX},${sourceY}Q${controlX},${controlY} ${targetX},${targetY}`;
  return (
    <>
      <BaseEdge id={id} path={path} interactionWidth={0} />
      <path className="map-route-core" d={path} />
      <path className="map-route-arrow" d="M-9,-11L9,0L-9,11L-3,0Z" transform={`translate(${midX},${midY}) rotate(${angle})`} />
    </>
  );
}
const edgeTypes = { route: RouteEdge };

function regionCountText(regions: MapRegions) {
  if (regions.basis !== "chapters") return `${regions.regions.length} 個區域`;
  return (regions.groups.length ? `${regions.groups.length} 個島群・` : "") + `${regions.regions.length} 個章節`;
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
  // 兩個以上區域才分組；組內維持教材順序。單次分桶，避免區域數×概念數的重複篩選。
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
      status === "completed" ? "已完成" : status === "needs_review" ? "要再練" : "";
    const name = [
      `第 ${step.position} 個，${mathPlainText(concept.label)}`,
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
            <span className="navigator-label"><MathText text={concept.label} notation={false} /></span>
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
            `教材順序從第 ${firstStep!.position} 個概念開始`
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
              <optgroup key={group.id} label={mathPlainText(group.label)}>
                {group.regionIndexes.map((index) => (
                  <option key={index} value={index}>{mathPlainText(regions!.regions[index].label)}</option>
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
                  <h3 title={mathPlainText(group.label)}>
                    <i style={{ background: islandColors(regionPalette(view.material_id, group.index)).c2 }} aria-hidden="true" />
                    <span><MathText text={group.label} notation={false} /></span>
                    <small>{group.regionIndexes.length} 章 · {group.conceptCount} 個</small>
                  </h3>
                  <ul className="navigator-chapters">
                    {slices.map(({ group: chapter, slice }) => (
                      <li key={chapter.id} className="navigator-region">
                        <h4 title={mathPlainText(chapter.label)}>
                          <span><MathText text={chapter.label} notation={false} /></span>
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
                <h3 title={mathPlainText(group.label)}>
                  <i style={{ background: islandColors(regionPalette(view.material_id, regionColorIndex(regions!, group.index))).c2 }} aria-hidden="true" />
                  <span><MathText text={group.label} notation={false} /></span>
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
  litConceptId,
}: {
  apiClient: StudydyApiClient;
  detail: ReactNode;
  /** 選島卡片打開的那座島；只亮起和它相連的關係，其他島變淡。 */
  litConceptId: string | null;
  difficulty: ConceptDifficultyView | null;
  /** 浮在地圖左上的搜尋，和「學習導覽」排成同一列。 */
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
  // 上方工具列那一條的高度，給淡出底用。
  const [toolbarBand, setToolbarBand] = useState(0);
  const fittedConcept = useRef<string | null>(null);
  // 上次自動取景的方式：選島（框住那座島、第一層和它的下一站）或航線（目前這站和接下來 3 站）。
  const fittedFraming = useRef<"island" | "route" | null>(null);
  // 使用者自己拖曳或縮放過鏡頭後，點開中心島、關卡片都不再自動取景；換了中心島才重新取景。
  const userMoved = useRef(false);
  const moveStart = useRef<{ x: number; y: number; zoom: number } | null>(null);
  // 目前這站：有進度用目前學到的概念，沒有進度用第 1 站。
  const routeStart = progress?.current_concept_id
    ?? [...view.initial_learning_path].sort((a, b) => a.position - b.position)[0]?.concept_id
    ?? selected.concept_id;
  // 選了島時航線從那座島出發，只畫「這座島 → 下一站」；最後一站或不在學習順序上的島沒有航線。
  const litNext = litConceptId ? nextStationId(view.initial_learning_path, litConceptId) : null;
  // 沒選島時一定畫出目前這站和接下來 3 站，讓航線看得到；選了島就只畫兩層，再加上它的下一站。
  const routeStations = useMemo(
    () => litConceptId ? [] : routeStationIds(view.initial_learning_path, routeStart),
    [litConceptId, view.initial_learning_path, routeStart],
  );
  const routeKey = routeStations.join("|");
  const projection = useMemo(
    () => focusGraph(view, selected.concept_id, routeStations, litNext),
    // routeKey 代表 routeStations 的內容；內容相同時不重算，避免節點重排。
    [view, selected.concept_id, routeKey, litNext],
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
    const visibility = relationVisibility(projection.relations, {
      litConceptId: litConceptId && layoutById.has(litConceptId) ? litConceptId : null,
      selectedRelationId,
    });
    // 選了島時，和它有亮起關係的島、航線上的下一站維持原樣，其餘的島淡化。
    const litNeighbours = new Set(litConceptId ? [litConceptId, ...(litNext ? [litNext] : [])] : []);
    for (const relation of projection.relations)
      if (litConceptId && visibility.get(relation.relation_id) === "lit"
        && [relation.source_concept_id, relation.target_concept_id].includes(litConceptId)) {
        litNeighbours.add(relation.source_concept_id);
        litNeighbours.add(relation.target_concept_id);
      }
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
          opacity: highlightedRelation
            ? [highlightedRelation.source_concept_id, highlightedRelation.target_concept_id].includes(node.id) ? 1 : 0.5
            : litConceptId && layoutById.has(litConceptId) && !litNeighbours.has(node.id) ? 0.35 : 1,
        },
        data: {
          handles: handlesByNode.get(node.id) ?? [],
          label: (
            <>
              <span className="island-sign">
                {position.has(node.id) && <em aria-hidden="true">{position.get(node.id)}</em>}
                <span>
                  <strong><MathText text={concept.label} notation={false} /></strong>
                  <small>
                    <LearningBadge conceptId={concept.concept_id} progress={progress} />
                    <DifficultyBadge concept={difficulty?.concepts.find(item => item.concept_id === concept.concept_id)} />
                    {concept.claim_count} 個重點
                  </small>
                </span>
              </span>
              <span className="island-node-art">
                <IslandArt palette={regions ? regionPalette(view.material_id, regionColorIndex(regions, regions.byConcept.get(node.id)!)) : palette} landmarks={current} />
                {/* 雲霧依學習狀態變淡：還沒開始最濃，學完散開；只是外觀，不擋點擊。 */}
                <span className="island-cloud" aria-hidden="true" />
              </span>
            </>
          ),
        },
        className: `concept-flow-node is-${states.get(node.id) ?? "not_started"}${current ? " is-focus" : ""}${node.depth === 2 ? " is-secondary" : ""}`,
        ariaLabel: `教材概念：${mathPlainText(concept.label)}`,
        ariaRole: "button",
        domAttributes: nodeKeyboardAction(
          () => openConcept(node.id, () => restoreGraphFocus(node.id)),
          `${mathPlainText(concept.label)}：查看概念與教材來源`,
        ),
        draggable: false,
        focusable: true,
      };
    });
    // 航線排在關係線前面，畫在最下層；純裝飾，不能點也不進鍵盤順序。
    const route: Edge[] = routeSegments(view.initial_learning_path, new Set(layoutById.keys()), litConceptId ?? routeStart).map(({ source, target }) => ({
      id: `route:${source}:${target}`,
      source,
      target,
      type: "route",
      sourceHandle: target === projection.nextBelowId ? "route-side:source" : "route:source",
      targetHandle: target === projection.nextBelowId ? "route-side:target" : "route:target",
      className: "map-route-edge",
      focusable: false,
      selectable: false,
      interactionWidth: 0,
      domAttributes: { "aria-hidden": true },
    }));
    const relationEdges: Edge[] = projection.relations.filter((relation) => visibility.has(relation.relation_id)).map((relation) => {
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
        labelStyle: { fill: color, fontSize: 13, fontWeight: 900 },
        labelBgStyle: { fill: "#fff" },
        labelBgPadding: [10, 4],
        labelBgBorderRadius: 999,
        className: `concept-flow-edge is-relation is-${relation.type}${visibility.get(relation.relation_id) === "dim" ? " is-dimmed" : ""}`,
        ariaLabel: `${label}：${previewRead.context?.relations.find(r => r.relation_id === relation.relation_id)?.learner_reason ?? `${mathPlainText(conceptById.get(relation.source_concept_id)?.label ?? "")} → ${mathPlainText(conceptById.get(relation.target_concept_id)?.label ?? "")}`}`,
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
    return { nodes, edges: [...route, ...relationEdges] };
  }, [
    layout,
    projection.relations,
    projection.nextBelowId,
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
    litConceptId,
    selectedRelationId,
    view.initial_learning_path,
    routeStart,
    litNext,
  ]);
  // 限制提示講的是兩層範圍內有幾條關係，不受目前顯示哪些關係影響。
  const relationCount = projection.relations.length;
  // resize 只保留世界座標中心；使用者按適應畫面才重新 fit。
  useEffect(() => {
    const element = graphElement.current!;
    let width = element.clientWidth;
    let height = element.clientHeight;
    const observer = new ResizeObserver(() => {
      const nextWidth = element.clientWidth,
        nextHeight = element.clientHeight;
      setCanvasReady(nextWidth > 0 && nextHeight > 0);
      setToolbarBand(visibleCanvas(element, false).top);
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
    if (!initialized || !canvasReady || (!previewRead.context && !previewRead.error)) return;
    const framing = litConceptId === selected.concept_id ? "island" : "route";
    const closingCard = framing === "route" && fittedFraming.current === "island";
    if (fittedConcept.current === selected.concept_id) {
      if (fittedFraming.current === framing) return;
      // 中心島不變、只是點開中心島或關掉卡片：使用者動過鏡頭就照他的，不拉回。
      if (userMoved.current) {
        fittedFraming.current = framing;
        return;
      }
    }
    let secondFrame = 0;
    const frame = requestAnimationFrame(() => {
      // 按需文字到達後，先讓節點量測更新，再計算第一次視野。
      secondFrame = requestAnimationFrame(() => {
        fittedConcept.current = selected.concept_id;
        fittedFraming.current = framing;
        userMoved.current = false;
        const visible = new Set(layout.map((node) => node.id));
        let ids: string[];
        let anchorIds: string[];
        if (framing === "island") {
          // 選了島：框住它、第一層和它的下一站，縮放保持島名可讀；框不下時這座島和下一站優先留在畫面內，
          // 太遠的鄰島可以超出畫面。
          const next = litNext && visible.has(litNext) ? [litNext] : [];
          ids = [...layout.filter((node) => node.depth < 2).map((node) => node.id), ...next];
          anchorIds = [selected.concept_id, ...next];
        } else {
          // 沒選島：框住目前這站（沒有進度時第 1 站）和接下來 3 站（關卡片後縮放太小時少框遠的站）；起點不在畫面上就從中心概念起算。
          ids = routeFrameIds(view.initial_learning_path, routeStart, visible);
          if (!ids.length) ids = routeFrameIds(view.initial_learning_path, selected.concept_id, visible);
          if (!ids.length) ids = [selected.concept_id];
          anchorIds = [ids[0]];
        }
        const canvas = visibleCanvas(graphElement.current!, framing === "island");
        // 少框遠站只在關卡片後（上次是選島取景）；剛打開地圖照舊全部框進來。
        void graph.setViewport(closingCard
          ? frameRouteCamera(ids, (list) => graph.getNodesBounds(list), canvas)
          : frameCamera(graph.getNodesBounds(ids), graph.getNodesBounds(anchorIds), canvas));
      });
    });
    return () => { cancelAnimationFrame(frame); cancelAnimationFrame(secondFrame); };
  }, [initialized, canvasReady, graph, selected.concept_id, previewRead.context, previewRead.error, layout, litConceptId, litNext, routeStart, view.initial_learning_path]);
  // 點一條關係不重新取景；卡片打開讓畫布變窄、把被選的島或那條關係擠出畫面時，才平移到看得到（不縮放）。
  useEffect(() => {
    if (!litConceptId && !selectedRelationId) return;
    let secondFrame = 0;
    const frame = requestAnimationFrame(() => {
      secondFrame = requestAnimationFrame(() => {
        if (fittedConcept.current !== selected.concept_id) return;
        const relation = view.relations.find((item) => item.relation_id === selectedRelationId);
        const ids = relation ? [relation.source_concept_id, relation.target_concept_id] : [litConceptId!];
        if (!ids.every((id) => layout.some((node) => node.id === id))) return;
        const element = graphElement.current!;
        const viewport = graph.getViewport();
        let rect = graph.getNodesBounds(ids);
        const canvas = visibleCanvas(element, true);
        const viewWidth = canvas.width / viewport.zoom, viewHeight = canvas.height / viewport.zoom;
        // 關係兩端放不進畫面時，只確保兩端中點（線的中段）看得到。
        if (rect.width > viewWidth || rect.height > viewHeight)
          rect = { x: rect.x + rect.width / 2 - 40, y: rect.y + rect.height / 2 - 40, width: 80, height: 80 };
        const left = -viewport.x / viewport.zoom, top = -viewport.y / viewport.zoom;
        const shift = (start: number, size: number, viewStart: number, view: number) =>
          start < viewStart ? viewStart - start : start + size > viewStart + view ? viewStart + view - start - size : 0;
        const dx = shift(rect.x, rect.width, left, viewWidth), dy = shift(rect.y, rect.height, top, viewHeight);
        if (dx || dy) void graph.setViewport({ ...viewport, x: viewport.x + dx * viewport.zoom, y: viewport.y + dy * viewport.zoom });
      });
    });
    return () => { cancelAnimationFrame(frame); cancelAnimationFrame(secondFrame); };
    // 只在打開或切換詳情時檢查一次；之後使用者自己拖曳、縮放不再介入。
  }, [litConceptId, selectedRelationId]);
  return (
    <section
      className={`focus-workspace${detail ? " has-inspector" : ""}`}
      aria-label="知識地圖工作區"
    >
      <div
        className="focus-graph surface"
        ref={graphElement}
        aria-label={`「${mathPlainText(selected.label)}」的兩層相關概念`}
      >
        <ReactFlow
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
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
          onEdgeClick={(_, edge) => { if (edge.className !== "map-route-edge") openRelation(edge.id, () => restoreGraphFocus(edge.id)); }}
          // 只有使用者操作（event 不是 null）才算動過鏡頭；按下滑鼠就會觸發開始，
          // 所以放開時比對位置，點島、點空白處時手抖幾 px 不算拖曳。
          onMoveStart={(event, viewport) => { moveStart.current = event ? viewport : null; }}
          onMoveEnd={(event, viewport) => {
            const start = moveStart.current;
            moveStart.current = null;
            if (event && start && (viewport.zoom !== start.zoom || Math.hypot(viewport.x - start.x, viewport.y - start.y) > 4))
              userMoved.current = true;
          }}
        >

          <Controls
            aria-label="地圖縮放控制"
            showInteractive={false}
            fitViewOptions={fitViewOptions}
            onZoomIn={() => { userMoved.current = true; }}
            onZoomOut={() => { userMoved.current = true; }}
          />
        </ReactFlow>
        {/* 滑到工具列底下的島和名稱卡淡成海色，不和按鈕撞在一起；不擋地圖操作。 */}
        <div className="map-toolbar-fade" aria-hidden="true" style={{ height: toolbarBand + 24 }} />
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
        </div>
        <div className="map-corner-tools">
          <MapExports api={apiClient} view={view} />
          {(nodes.length < projection.totalNodes || relationCount < projection.totalRelations) && (
            <p className="map-limit-note" role="status">
              兩層範圍：{nodes.length}／{projection.totalNodes} 個概念、{relationCount}／
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
  const label = (id: string) => mathPlainText(view.concepts.find((concept) => concept.concept_id === id)?.label ?? "");
  return (
    <DetailPanel label="關係詳情" focusKey={relation.relation_id} close={close}>
      <header>
        <div>
          <span className="detail-kicker">{relationStyles[relation.type].label}</span>
          <h2>{relationTitle(relation.type, label(relation.source_concept_id), label(relation.target_concept_id))}</h2>
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
              <strong><MathText text={view.concepts.find((concept) => concept.concept_id === id)?.label ?? ""} notation={false} /></strong>
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

function ReviewView({ apiClient, materialId, view, progress, difficulty, isLoadingProgress, progressMessage, onReloadProgress, isStartingStudy, onStartStudy, onOpenRecord }: {
  apiClient: StudydyApiClient; materialId: string; view: KnowledgeOutlineView; progress: MapProgressView | null;
  difficulty: ReturnType<typeof useConceptDifficulty>; isLoadingProgress: boolean; progressMessage: string | null;
  onReloadProgress: () => void; isStartingStudy: boolean; onStartStudy: (id: string) => void; onOpenRecord: (id: string) => void;
}) {
  const diagnosis = useLearningDiagnosis(apiClient, materialId, view.knowledge_structure_revision, difficulty, progress, view.concepts);
  const goal = useLearningGoal(apiClient, materialId);
  const nextLearningConcept = view.concepts.find(concept => concept.concept_id === (progress ? progress.learning_path[0] : view.initial_learning_path[0]?.concept_id));
  const completedCount = progress?.concept_states.filter(state => state.status === "completed").length ?? 0;
  const startId = nextLearningConcept?.concept_id ?? view.initial_learning_path[0]?.concept_id;
  const hasToday = !!goal.goal;
  const total = view.concepts.length;
  return <div className="review-diagnosis">
    <h1 className="review-title">學習進度</h1>
    <section className="review-summary" aria-label="學習進度">
      {isLoadingProgress ? <p role="status">正在讀取學習進度…</p> : progressMessage ? <p>重新讀取進度後，即可接續學習。</p> : <>
        <div className="review-lit">
          <p className="review-lit-count"><b>{completedCount}</b>{" "}<small>/ {total} 座島已點亮</small></p>
          <Progress value={completedCount} max={total || 1} aria-label="概念學習進度" aria-valuetext={`已點亮 ${completedCount} / ${total} 座島`} />
          {goal.goal && <DailyGoalProgress stage={goal.goal.today_stage} stageKey={materialId} />}
        </div>
        <div className="review-next">
          <small className="review-next-kicker">下一站</small>
          <p className="review-next-concept">{nextLearningConcept ? <MathText text={nextLearningConcept.label} notation={false} /> : progress ? "目前路線已完成，可再次練習。" : "從第一個概念開始學習。"}</p>
          {nextLearningConcept && <small className="review-next-meta">照地圖的建議順序{nextLearningConcept.claim_count > 0 && ` · ${nextLearningConcept.claim_count} 個重點`}</small>}
        </div>
        {startId && <button className="primary-button" type="button" disabled={isStartingStudy} onClick={() => onStartStudy(startId)}>{isStartingStudy ? "正在接續學習…" : "繼續學習"}</button>}
      </>}
    </section>
    <div className="review-grid">
      <div className="review-goal-column">
        <LearningGoalBlock state={goal} />
        {hasToday && <TodayConceptsBlock state={goal} />}
      </div>
      <LearningDiagnosis api={apiClient} materialId={materialId} revision={view.knowledge_structure_revision} onReview={onOpenRecord} disabled={isStartingStudy || isLoadingProgress}
        difficulty={difficulty} progress={progress} concepts={view.concepts} report={diagnosis} />
    </div>
    <GoalEditorHost state={goal} />
  </div>;
}

/** 地圖與學習進度頁共用的提示：進度讀取失敗、開始學習失敗、未納入的頁面。 */
function WorkspaceNotices({ progressMessage, onReloadProgress, startMessage, view }: {
  progressMessage: string | null; onReloadProgress: () => void; startMessage: string | null; view: KnowledgeOutlineView;
}) {
  return <>
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
  </>;
}

/** 教材側欄「學習進度」：內容沿用原本地圖內切換的學習診斷與目標。 */
export function MaterialProgress({ apiClient, materialId, view, progress, isLoadingProgress, progressMessage, onReloadProgress, isStartingStudy, onStartStudy, onOpenRecord, startMessage }: {
  apiClient: StudydyApiClient; materialId: string; view: KnowledgeOutlineView; progress: MapProgressView | null;
  isLoadingProgress: boolean; progressMessage: string | null; onReloadProgress: () => void;
  isStartingStudy: boolean; onStartStudy: (id: string) => void; onOpenRecord: (id: string) => void; startMessage: string | null;
}) {
  const difficulty = useConceptDifficulty(apiClient, materialId, view.knowledge_structure_revision);
  return <section className="map-workspace is-review-mode">
    <WorkspaceNotices progressMessage={progressMessage} onReloadProgress={onReloadProgress} startMessage={startMessage} view={view} />
    <div className="map-content">
      <div className="map-view">
        <ReviewView apiClient={apiClient} materialId={materialId} view={view} progress={progress} difficulty={difficulty}
          isLoadingProgress={isLoadingProgress} progressMessage={progressMessage} onReloadProgress={onReloadProgress} isStartingStudy={isStartingStudy} onStartStudy={onStartStudy} onOpenRecord={onOpenRecord} />
      </div>
    </div>
  </section>;
}

export function KnowledgeMapWorkspace({
  apiClient,
  progress,
  isLoadingProgress,
  progressMessage,
  onReloadProgress,
  isStartingStudy,
  mapRoute,
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
  mapRoute: Extract<AppRoute, { name: "knowledge-map" }>;
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
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState(restored?.search ?? "");
  const [selectedConceptId, setSelectedConceptId] = useState(restored?.concept ?? initialConceptId);
  const [reviewConceptId] = useState(restored?.review ?? initialConceptId);
  const [detailConceptId, setDetailConceptId] = useState<string | null>(restored?.detail ?? null);
  const [relationId, setRelationId] = useState<string | null>(restored?.relation ?? null);
  // 亮起關係的那座島：開選島卡片時設定；從卡片點進關係說明時保留，關閉詳情才清掉。
  const [litConceptId, setLitConceptId] = useState<string | null>(restored?.detail ?? null);
  useEffect(() => {
    window.history.replaceState(
      {
        ...window.history.state,
        knowledgeMap: {
          revision: view.knowledge_structure_revision,
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
  const mapView = useRef<HTMLDivElement>(null);
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
      else mapView.current?.focus({ preventScroll: true });
    };
  };
  const openConceptDetail = (id: string, restoreFocus?: RestoreFocus) => {
    hasBrowsed.current = true;
    rememberOpener(restoreFocus);
    setSelectedConceptId(id);
    setDetailConceptId(id);
    setLitConceptId(id);
    setRelationId(null);
  };
  const chooseSearchResult = (id: string) => {
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
    setLitConceptId(null);
    setRelationId(null);
    // 等 React Flow 完成下一幀量測後，再恢復節點或連線焦點。
    cancelAnimationFrame(restoreFrame.current);
    restoreFrame.current = requestAnimationFrame(() => {
      restoreFrame.current = requestAnimationFrame(() => {
        // 使用者已開始搜尋等新操作時，不以延遲恢復搶走焦點。
        if (document.activeElement !== closingFocus && document.activeElement !== document.body)
          return;
        if (opener.current) opener.current();
        else mapView.current?.focus({ preventScroll: true });
      });
    });
  };
  const busyStudyLabel = isLoadingProgress ? "讀取學習進度…" : isStartingStudy ? "正在開始…" : null;
  const focusStudyConceptId = selectedConceptId;
  const focusStudyButtonLabel =
    busyStudyLabel ??
    (progress?.concept_states.find(state => state.concept_id === focusStudyConceptId)?.status === "completed"
      ? "再次練習"
      : "開始挑戰");
  const focusStudyAction = (
    <section className="concept-study-action" aria-label="學習入口">
      <button
        className="primary-button"
        disabled={isStartingStudy || isLoadingProgress}
        type="button"
        onClick={() => onStartStudy(focusStudyConceptId)}
      >
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
            // 從卡片點進關係說明，關閉後焦點回到地圖上那條線（線不在畫面上時回到地圖）。
            openRelation={(id) => openRelation(id, () => {
              const edge = mapView.current?.querySelector<SVGElement>(`.concept-flow-edge[data-id="${CSS.escape(id)}"]`);
              (edge ?? mapView.current)?.focus({ preventScroll: true });
            })}
          />
        )}
      </>
    ) : null;
  const mapToolbar = (
    <>
        <form
          className={`map-search map-corner-search ${searchOpen ? "is-expanded" : ""}`}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              // 只清空搜尋；不讓外層的學習導覽把焦點移走。
              event.stopPropagation();
              setSearchQuery("");
              setSearchOpen(false);
              if (window.matchMedia("(max-width:720px)").matches) event.currentTarget.querySelector<HTMLButtonElement>(".map-search-toggle")?.focus();
              else searchInput.current?.focus();
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
          <button type="button" className="secondary-button map-search-toggle" aria-label="開啟概念搜尋" aria-expanded={searchOpen} onClick={() => { setSearchOpen(value => !value); requestAnimationFrame(() => searchInput.current?.focus()); }}><Icon name="search" size={18} /></button>
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
                  <strong><MathText text={concept.label} notation={false} /></strong>
                  <small>{concept.claim_count} 個重點</small>
                </button>
              ))}
            </div>
          )}
        </form>
    </>
  );
  return (
    <section
      className={`map-workspace is-focus-mode${selectedConcept || selectedRelation ? " has-detail" : ""}`}
    >
      <WorkspaceNotices progressMessage={progressMessage} onReloadProgress={onReloadProgress} startMessage={startMessage} view={view} />
      <div className="map-content">
        <div
          ref={mapView}
          aria-label="知識地圖"
          className="map-view"
          role="region"
          tabIndex={0}
        >
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
                litConceptId={litConceptId}
              />
            </ReactFlowProvider>
        </div>
      </div>
    </section>
  );
}
