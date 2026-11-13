import { MapExports } from "../exports/MapExports";
import { openNoteTarget, useConceptNoteTarget } from "../notes/note-location";
import { relationStyles } from "./map-appearance";
import { LearningDiagnosis, useLearningDiagnosis, useConceptDifficulty } from "../learning-diagnosis/LearningDiagnosis";
import { GoalEditorHost, LearningGoalBlock, TodayConceptsBlock, useLearningGoal } from "../learning-goals/LearningGoalPanel";
import { DailyGoalProgress } from "../learning-goals/DailyGoalProgress";
import { MaterialContentNav } from "../material-flow/MaterialContentNav";
import type { AppRoute } from "../../app/routes";
import { SourceButton, evidencePlace, evidenceSourceTitle, sourceLinks } from "../../ui/SourceButton";
import { Fragment, useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode, type KeyboardEvent } from "react";
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
  RelationType,
  KnowledgeOutlineView,
  KnowledgeRegionsView,
  MapProgressView,
  ConceptDifficultyView,
  StudySessionView,
} from "../../api/contracts";
import { useConceptContext } from "../material-flow/useConceptContext";
import { errorMessage } from "../../api/client";
import { ConceptContent } from "../../ui/ConceptContent";
import { Icon } from "../../ui/Icon";
import { MathText } from "../../ui/MathText";
import { mathPlainText } from "../../ui/math-text";
import { Progress } from "../../ui/Progress";
import { IslandArt, islandColors, islandPalette, regionPalette } from "../../ui/art/Island";
import { StateView } from "../../ui/StateView";
import { learningNavigationItems, focusGraph, groupSlices, initialFocusConceptId, mapRegions, navigatorRange, NAVIGATOR_WINDOW, nextStationId, routePreviewIds, regionBasisText, regionColorIndex, regionHeading, type MapRegions, type NavigatorRange } from "./knowledge-map";

type Concept = KnowledgeOutlineView["concepts"][number];
type Mode = "focus" | "review";
type RestoreFocus = () => void;

const fitViewOptions = { padding: 0.18, minZoom: 0.001, maxZoom: 1.1 };
// 任務卡最高佔畫布高度的比例；選中概念時的視野一律預留這麼高，卡片內容晚到變高也不會蓋住島。
const QUEST_CARD_SHARE = 0.45;
const COMPACT_QUERY = "(max-width: 720px)";

// 島牌：底色＋旗子高度表示狀態，文字只放在節點的 aria-label。
const islandStates = {
  not_started: { label: "還沒去過", flag: null },
  in_progress: { label: "走到一半", flag: "flag-low" },
  needs_review: { label: "要再走一趟", flag: "flag-half" },
  completed: { label: "已插旗", flag: "flag" },
} as const;

function MapProgressHud({ view, progress, regions, conceptId }: {
  view: KnowledgeOutlineView;
  progress: MapProgressView | null;
  regions: MapRegions | null;
  conceptId: string;
}) {
  const completed = progress?.concept_states.filter((state) => state.status === "completed").length ?? 0;
  const heading = regionHeading(regions, conceptId);
  return (
    <div className="map-hud">
      <p>
        {heading && <span className="map-hud-region"><MathText text={heading} notation={false} /></span>}
        <span className="map-hud-count">已插旗 {completed} <small>／{view.concepts.length} 座島</small></span>
      </p>
      <Progress value={completed} max={view.concepts.length} aria-label="已插旗的島" />
    </div>
  );
}

function DetailPanel({
  label,
  className,
  focusKey,
  close,
  children,
}: {
  label: string;
  className: string;
  focusKey: string;
  close: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const panel = ref.current!;
    // 所有寬度都是非 modal：桌機疊在畫布下方，手機是貼底抽屜；上方的地圖照常可拖曳、可點別座島。
    // 焦點由下一個 effect 移進卡片，Esc 與關閉鈕關閉後焦點回到原本的節點。
    panel.show();
    return () => panel.close();
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
      className={`detail-panel ${className}`}
      aria-label={label}
      tabIndex={-1}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          close();
        }
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
  isLoadingProgress,
  progressMessage,
  difficulty,
  isStartingStudy,
  onStartStudy,
  openConcept,
}: {
  view: KnowledgeOutlineView;
  progress: MapProgressView | null;
  apiClient: StudydyApiClient;
  concept: Concept;
  difficulty: ConceptDifficultyView | null;
  close: () => void;
  isLoadingProgress: boolean;
  progressMessage: string | null;
  isStartingStudy: boolean;
  onStartStudy: (conceptId: string) => void;
  openConcept: (id: string) => void;
}) {
  const read = useConceptContext(apiClient, view.material_id, view.knowledge_structure_revision, [concept.concept_id]);
  const full = read.context?.concepts.find(c => c.concept_id === concept.concept_id);
  const noteTarget = useConceptNoteTarget(apiClient, view.material_id, view.knowledge_structure_revision, concept.concept_id);
  const route = useMemo(() => learningNavigationItems(view), [view]);
  const state = progress?.concept_states.find((item) => item.concept_id === concept.concept_id);
  // 出不了題的旗標和學習狀態並存；已插旗的島照常顯示已插旗。
  const unassessable = !!state && state.status !== "completed" && !state.assessable;
  const status = state?.status ?? "not_started";
  const look = islandStates[status];
  const statusText = progress
    ? unassessable ? "暫時出不了題" : look.label
    : isLoadingProgress ? "讀取學習狀態…" : progressMessage ? "學習狀態暫時無法讀取" : look.label;
  const record = difficulty?.concepts.find((item) => item.concept_id === concept.concept_id);
  // 「先去下一站」：學習路線上的下一座島；已經是最後一座就不顯示。
  const routeIndex = route.findIndex((item) => item.concept.concept_id === concept.concept_id);
  const nextOnRoute = route[routeIndex + 1]?.concept;
  const busyLabel = isLoadingProgress ? "讀取學習進度…" : isStartingStudy ? "正在開始…" : null;
  const studyLabel = busyLabel ?? (status === "completed" ? "再練一次" : status === "in_progress" ? "繼續練習" : "開始練習");
  return (
    <DetailPanel label="概念詳情" className="quest-card" focusKey={concept.concept_id} close={close}>
      <button aria-label="關閉概念詳情" className="quest-close" type="button" onClick={close}>
        <Icon name="close" size={22} />
      </button>
      <div className="quest-body">
        {/* 換概念時重建，左欄捲動回到頂端。 */}
        <div className="quest-main" key={concept.concept_id}>
          <header className="quest-title">
            <h2><MathText text={concept.label} notation={false} /></h2>
            <span className={`quest-status is-${progress ? status : "unknown"}${unassessable ? " is-unassessable" : ""}`}>
              {progress && !unassessable && look.flag && <Icon name={look.flag} size={16} />}
              {statusText}
            </span>
            {record?.level === "hard" && <span className="quest-sticker is-hard" title={record.reason}>可能比較難</span>}
            {record?.personal_difficulty && <span className="quest-sticker is-personal">你常在這裡卡關</span>}
          </header>
          {full?.uncertain === true && <p className="flow-notice">影片逐字稿可能有聽錯的詞，建議對照原影片</p>}
          {read.error ? <p role="alert">{read.error}<button type="button" onClick={read.retry}>重新讀取</button></p>
            : full ? <ConceptContent claims={full.claims} apiClient={apiClient} sourceResolver={view.source_resolver} showSources={false} />
            : <p role="status">正在讀取概念與來源…</p>}
          {(full || noteTarget) && (
            <div className="quest-links">
              {full && (
                <section className="quest-sources" aria-label="教材來源">
                  {sourceLinks(full.claims.flatMap((claim) => claim.evidence)).map((evidence) => (
                    <SourceButton
                      key={evidence.evidence_id}
                      apiClient={apiClient}
                      resolver={view.source_resolver}
                      evidence={evidence}
                      label={`看原文：${evidencePlace(evidence)}`}
                      title={evidenceSourceTitle(evidence)}
                      ariaLabel={`看原文：${evidenceSourceTitle(evidence)}`}
                    />
                  ))}
                </section>
              )}
              {noteTarget && <button type="button" className="quest-link" onClick={() => openNoteTarget(view.material_id, noteTarget)}>看筆記怎麼寫</button>}
            </div>
          )}
        </div>
        <section className="quest-action" aria-label="學習入口">
          {unassessable ? (
            <>
              <p>這座島暫時出不了題，不是你的問題</p>
              {nextOnRoute && (
                <button className="quest-primary is-plain" type="button" onClick={() => openConcept(nextOnRoute.concept_id)}>
                  先去下一站
                </button>
              )}
            </>
          ) : (
            <>
              {record && record.answer_count > 0 && (
                <p>
                  答過 {record.answer_count} 題，{record.incorrect_count ? `錯了 ${record.incorrect_count} 題` : "全部答對"}
                  {status === "needs_review" && "，再挑戰一次"}
                </p>
              )}
              <button
                className={`quest-primary${status === "completed" && !busyLabel ? " is-plain" : ""}`}
                disabled={isStartingStudy || isLoadingProgress}
                type="button"
                onClick={() => onStartStudy(concept.concept_id)}
              >
                {studyLabel}
              </button>
            </>
          )}
        </section>
      </div>
    </DetailPanel>
  );
}

/** 沒點任何島時的小卡：帶你去下一站；路線全部插旗後帶回第一站。 */
function NextStationCard({
  view,
  progress,
  open,
}: {
  view: KnowledgeOutlineView;
  progress: MapProgressView | null;
  open: (id: string) => void;
}) {
  const nextId = nextStationId(view, progress);
  const id = nextId ?? learningNavigationItems(view)[0]?.concept.concept_id;
  const concept = view.concepts.find((item) => item.concept_id === id);
  if (!concept) return null;
  const state = progress?.concept_states.find((item) => item.concept_id === concept.concept_id);
  const unassessable = !!state && state.status !== "completed" && !state.assessable;
  return (
    <section className="map-next-card" aria-label="接下來">
      <div>
        <p>
          <span>{nextId ? "接下來" : "這片海都插滿旗了"}</span>
          <strong>
            <MathText text={concept.label} notation={false} />
            {nextId && state && (
              <small className={`is-${unassessable ? "unassessable" : state.status}`}>
                {unassessable ? "暫時出不了題" : islandStates[state.status].label}
              </small>
            )}
          </strong>
        </p>
        <button className="quest-primary" type="button" onClick={() => open(concept.concept_id)}>帶我去</button>
      </div>
    </section>
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
  hereConceptId,
  onClose,
}: {
  progress: MapProgressView | null;
  regions: MapRegions | null;
  view: KnowledgeOutlineView;
  selectedConceptId: string;
  /** 詳情打開的那座島（「你在這裡」）；沒點任何島時為 null。 */
  hereConceptId: string | null;
  focusConcept: (id: string) => void;
  onClose: () => void;
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
  const [expanded, setExpanded] = useState(false);
  const nextId = nextStationId(view, progress);
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
  }, [selectedConceptId, expanded]);
  const renderItem = ({ concept, step }: (typeof items)[number]) => {
    const selected = concept.concept_id === selectedConceptId;
    const here = concept.concept_id === hereConceptId;
    const state = states.get(concept.concept_id);
    const status = state?.status;
    // 出不了題的旗標和學習狀態並存；已插旗的站照常顯示綠色。
    const unassessable = !!state && state.status !== "completed" && !state.assessable;
    const next = concept.concept_id === nextId && !here;
    const stateText = unassessable ? "暫時出不了題" : status && status !== "not_started" ? islandStates[status].label : "";
    // 副標：「你在這裡・要再走一趟」「下一站」「已插旗」等；還沒去過的站不寫。
    const note = here ? `你在這裡${stateText ? `・${stateText}` : ""}` : next ? "下一站" : stateText;
    const name = [
      `第 ${step.position} 個，${mathPlainText(concept.label)}`,
      here && "你在這裡",
      stateText,
      next && "下一站",
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
            "navigator-station",
            selected && "is-selected",
            here && "is-here",
            unassessable ? "is-unassessable" : status && `is-${status}`,
            next && "is-next-suggested",
          ]
            .filter(Boolean)
            .join(" ")}
          onClick={() => focusConcept(concept.concept_id)}
        >
          <span className="navigator-position" aria-hidden="true">
            {status === "completed" && !unassessable ? <Icon name="check" size={18} /> : step.position}
          </span>
          <span className="navigator-concept">
            <span className="navigator-label"><MathText text={concept.label} notation={false} /></span>
            {note && <span className="navigator-note">{note}</span>}
          </span>
        </button>
      </li>
    );
  };
  const orderedItems = useMemo(() => new Map(ordered.map((item) => [item.concept.concept_id, item])), [ordered]);
  const previewItems = routePreviewIds(ordered.map((item) => item.concept.concept_id), hereConceptId, nextId)
    .map((id) => orderedItems.get(id)!);
  return (
    <nav className="focus-navigator" aria-label="關卡路線">
      <header>
        <h2>關卡路線</h2>
        <button type="button" className="navigator-close" aria-label="收起關卡路線" onClick={onClose}>
          <Icon name="close" size={20} />
        </button>
      </header>
      {expanded && groups && <p className="navigator-basis">{regionBasisText(regions!)}</p>}
      {expanded && groups && regions!.groups.length > 0 && (
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
        {!expanded && <ul>{previewItems.map(renderItem)}</ul>}
        {expanded && shown.start > 0 && (
          <button type="button" className="navigator-more" onClick={() => setRange({ start: Math.max(0, shown.start - NAVIGATOR_WINDOW), end: shown.end })}>
            顯示前 {Math.min(NAVIGATOR_WINDOW, shown.start)} 個概念
          </button>
        )}
        {expanded && (groups ? (
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
        ))}
        {expanded && shown.end < ordered.length && (
          <button type="button" className="navigator-more" onClick={() => setRange({ start: shown.start, end: Math.min(ordered.length, shown.end + NAVIGATOR_WINDOW) })}>
            顯示後 {Math.min(NAVIGATOR_WINDOW, ordered.length - shown.end)} 個概念
          </button>
        )}
      </div>
      <button type="button" className="navigator-toggle" aria-expanded={expanded} aria-controls="focus-concept-list" onClick={() => setExpanded((value) => !value)}>
        {expanded ? "收起" : `看全部 ${view.concepts.length} 座島`}
      </button>
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
  hereConceptId,
}: {
  apiClient: StudydyApiClient;
  /** 疊在畫布下方的任務卡或關係卡；沒有時顯示「接下來」小卡。 */
  detail: ReactNode;
  /** 詳情打開的那座島（「你在這裡」）；沒點任何島時為 null。 */
  hereConceptId: string | null;
  /** 浮在地圖左上的搜尋鈕，和關卡路線、下載排成同一列。 */
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
  const [legendOpen, setLegendOpen] = useState(false);
  // 關卡路線卡：桌機預設打開；手機（≤720）會蓋住大半地圖，預設收起，選了站就收起。
  const [navigatorOpen, setNavigatorOpen] = useState(() => !window.matchMedia(COMPACT_QUERY).matches);
  const navigatorToggle = useRef<HTMLButtonElement>(null);
  // 打開時圓鈕是隱藏的，收起後要等它重新顯示才能把焦點還給它。
  const focusToggle = useRef(false);
  useEffect(() => {
    if (!navigatorOpen && focusToggle.current) navigatorToggle.current?.focus();
    focusToggle.current = false;
  }, [navigatorOpen]);
  const closeNavigator = () => {
    focusToggle.current = true;
    setNavigatorOpen(false);
  };
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
    const states = new Map(progress?.concept_states.map((state) => [state.concept_id, state]));
    const nextId = nextStationId(view, progress);
    const nodes: Node[] = layout.map((node) => {
      const concept = conceptById.get(node.id)!;
      const current = node.id === selected.concept_id;
      const state = states.get(node.id);
      // 出不了題的旗標和學習狀態並存；已插旗的島照常顯示綠色。
      const unassessable = !!state && state.status !== "completed" && !state.assessable;
      const look = state ? islandStates[state.status] : null;
      const here = node.id === hereConceptId;
      const next = node.id === nextId && !here;
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
                {here ? <small>你在這裡</small> : look?.flag && !unassessable && <Icon name={look.flag} size={20} />}
                <strong><MathText text={concept.label} notation={false} /></strong>
                {next && <span className="island-sign-next">下一站</span>}
              </span>
              <span className="island-node-art"><IslandArt palette={regions ? regionPalette(view.material_id, regionColorIndex(regions, regions.byConcept.get(node.id)!)) : palette} landmarks={current} /></span>
            </>
          ),
        },
        className: `concept-flow-node is-${state?.status ?? "not_started"}${unassessable ? " is-unassessable" : ""}${here ? " is-here" : ""}${current ? " is-focus" : ""}${node.depth === 2 ? " is-secondary" : ""}`,
        ariaLabel: [
          `教材概念：${mathPlainText(concept.label)}`,
          look && (unassessable ? "暫時出不了題" : look.label),
          here && "你在這裡",
          next && "下一站",
        ].filter(Boolean).join("，"),
        ariaRole: "button",
        domAttributes: nodeKeyboardAction(
          () => openConcept(node.id, () => restoreGraphFocus(node.id)),
          `${mathPlainText(concept.label)}：查看概念與教材來源`,
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
    return { nodes, edges };
  }, [
    layout,
    projection.relations,
    previewRead.context,
    previewRead.error,
    selected.concept_id,
    highlightedRelation,
    hereConceptId,
    view,
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
        const element = graphElement.current!;
        const firstHop = layout.filter((node) => node.depth < 2).map((node) => ({ id: node.id }));
        // 畫布本身太窄（依畫布寬度而非視窗斷點）：不縮到字看不清，初次視野框中心與第一層，
        // 抽屜打開時只框選中的島，其餘可平移或縮小查看。
        const narrow = element.clientWidth < 600;
        // 疊在畫布上的進度條、圓鈕、橋的說明、縮放鈕、關卡路線與下方卡片都算進留白，島落在沒被擋住的地方。
        const bounds = element.getBoundingClientRect();
        const box = (selector: string) => element.querySelector(selector)?.getBoundingClientRect();
        const gap = 24;
        const top = Math.max(0, ...[box(".map-hud"), box(".map-toolbar")].map((item) => item ? item.bottom - bounds.top : 0));
        // 窄畫布的縮放鈕和「?」只是右上角一小塊、關卡路線卡橫跨整個上半部，都不算進左右留白，
        // 否則中心的島會被擠出畫布左緣。
        const right = narrow ? 0 : Math.max(0, ...[box(".react-flow__controls"), box(".map-annotations")].map((item) => item ? bounds.right - item.left : 0));
        const navigator = narrow ? undefined : box("#map-navigator:not([hidden])");
        const left = navigator ? navigator.right - bounds.left : 0;
        const card = element.querySelector<HTMLElement>(".detail-panel");
        const next = box(".map-next-card > div");
        // 卡片內容是非同步載入的，先照最高高度預留；打開卡片時只框住選中的島和第一層鄰居。
        const bottom = card
          ? bounds.bottom - card.getBoundingClientRect().bottom + bounds.height * QUEST_CARD_SHARE
          : next ? bounds.bottom - next.top : 0;
        void graph.fitView({
          ...fitViewOptions,
          padding: { top: `${top + gap}px`, right: `${right + gap}px`, bottom: `${bottom + gap}px`, left: `${left + gap}px` },
          ...(narrow ? { minZoom: 0.5, nodes: card ? [{ id: selected.concept_id }] : firstHop } : card && { nodes: firstHop }),
        });
      });
    });
    return () => { cancelAnimationFrame(frame); cancelAnimationFrame(secondFrame); };
  }, [initialized, canvasReady, graph, selected.concept_id, previewRead.context, previewRead.error, layout]);
  // 開啟關係卡時（鏡頭不因點線而重框）：被點的線和兩端的島若被下方卡片蓋住，才把它們平移到卡片上方；
  // 手機的任務卡抽屜佔掉近半個畫布，打開的島已是選中的島時（例如「帶我去」）不會重新框視野，同樣只在被蓋住時平移。
  // 卡片的說明和出處是非同步載入的，高度變了也要重新確認。
  const compactHere = hereConceptId && window.matchMedia(COMPACT_QUERY).matches ? hereConceptId : null;
  useEffect(() => {
    const ids = highlightedRelation
      ? [highlightedRelation.source_concept_id, highlightedRelation.target_concept_id]
      : compactHere ? [compactHere] : null;
    if (!ids) return;
    const element = graphElement.current!;
    const card = element.querySelector<HTMLElement>(".detail-panel");
    if (!card) return;
    let frame = 0;
    const reveal = () => {
      const bounds = element.getBoundingClientRect();
      const cardTop = card.getBoundingClientRect().top;
      const gap = 24;
      // 窄畫布（手機）上兩端的島常有一座在畫布外，跑出左右或上緣也算看不到；留白規則同上方的初次視野。
      const narrow = element.clientWidth < 600;
      const covered = [
        ...ids.map((id) => element.querySelector(`.react-flow__node[data-id="${CSS.escape(id)}"]`)),
        element.querySelector(".concept-flow-edge.selected .react-flow__edge-textbg"),
      ].some((item) => {
        const rect = item?.getBoundingClientRect();
        return rect && (rect.bottom > cardTop - gap / 2
          || (narrow && (rect.left < bounds.left || rect.right > bounds.right || rect.top < bounds.top)));
      });
      if (!covered) return;
      const box = (selector: string) => element.querySelector(selector)?.getBoundingClientRect();
      const top = Math.max(0, ...[box(".map-hud"), box(".map-toolbar")].map((item) => item ? item.bottom - bounds.top : 0));
      const right = narrow ? 0 : Math.max(0, ...[box(".react-flow__controls"), box(".map-annotations")].map((item) => item ? bounds.right - item.left : 0));
      const navigator = narrow ? undefined : box("#map-navigator:not([hidden])");
      void graph.fitView({
        ...fitViewOptions,
        // 只平移或縮小，不為了框兩座島而放大。
        maxZoom: graph.getZoom(),
        nodes: ids.map((id) => ({ id })),
        padding: {
          top: `${top + gap}px`,
          right: `${right + gap}px`,
          bottom: `${bounds.bottom - cardTop + gap}px`,
          left: `${(navigator ? navigator.right - bounds.left : 0) + gap}px`,
        },
      });
    };
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(reveal);
    });
    observer.observe(card);
    return () => { observer.disconnect(); cancelAnimationFrame(frame); };
  }, [highlightedRelation?.relation_id, compactHere, graph]);
  return (
    <section className="focus-workspace" aria-label="知識地圖工作區">
      <div
        className={`focus-graph surface${navigatorOpen ? " has-navigator" : ""}`}
        style={{ "--quest-card-max": `${QUEST_CARD_SHARE * 100}%` } as CSSProperties}
        ref={graphElement}
        aria-label={`「${mathPlainText(selected.label)}」的兩層相關概念`}
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
            if (event.key === "Escape" && navigatorOpen) {
              event.preventDefault();
              closeNavigator();
            }
          }}
        >
          <div className="map-toolbar">
            {toolbar}
            <button
              ref={navigatorToggle}
              className="map-round-button map-navigator-toggle"
              type="button"
              aria-label="關卡路線"
              title="關卡路線"
              aria-expanded={navigatorOpen}
              aria-controls="map-navigator"
              hidden={navigatorOpen}
              onClick={() => setNavigatorOpen(true)}
            >
              <Icon name="route" size={28} />
            </button>
            <MapExports api={apiClient} view={view} />
          </div>
          <div id="map-navigator" hidden={!navigatorOpen}>
            <LearningNavigator
              regions={regions}
              progress={progress}
              view={view}
              selectedConceptId={selected.concept_id}
              hereConceptId={hereConceptId}
              onClose={closeNavigator}
              focusConcept={(id) => {
                // 手機上路線卡蓋住地圖，選完就收起並把焦點還給圓鈕；桌機卡片留著，焦點回到被點的那一站。
                if (window.matchMedia(COMPACT_QUERY).matches) {
                  setNavigatorOpen(false);
                  openConcept(id, () => navigatorToggle.current?.focus());
                } else openConcept(id);
              }}
            />
          </div>
        </div>
        <MapProgressHud view={view} progress={progress} regions={regions} conceptId={selected.concept_id} />
        <div className={`map-annotations map-corner-legend ${legendOpen ? "is-expanded" : ""}`}>
          <button type="button" className="map-round-button map-legend-toggle" aria-label="顯示橋的說明" aria-expanded={legendOpen} onClick={() => setLegendOpen(value => !value)}>?</button>
          <div className="relation-legend" aria-label="橋的說明">
            {Object.entries(relationStyles).map(([type, { bridge, meaning }]) => (
              <span key={type} className={highlightedRelation?.type === type ? "is-current" : undefined}>
                <i className={`relation-swatch is-${type}`} aria-hidden="true" />
                {bridge}：{meaning}
              </span>
            ))}
          </div>
          {nodes.length < projection.totalNodes && (
            <p className="map-limit-note" role="status">
              這裡只看得到附近的 {nodes.length} 座島（共 {projection.totalNodes} 座），其他的用搜尋或關卡路線找。
            </p>
          )}
        </div>
        {detail ?? <NextStationCard view={view} progress={progress} open={(id) => openConcept(id, () => restoreGraphFocus(id))} />}
      </div>
    </section>
  );
}

/** 關係卡標題的一句話；方向照後端定義：先備 source 先學、組成 source 是部分、應用 source 是概念 target 是用途、例子 source 是抽象概念。 */
function relationSentence(type: RelationType, from: string, to: string) {
  switch (type) {
    case "prerequisite": return <>先學「{from}」，再學「{to}」</>;
    case "part_of": return <>「{from}」是「{to}」的一部分</>;
    case "application": return <>學會「{from}」，到「{to}」就會用到</>;
    case "example": return <>「{to}」是「{from}」的例子</>;
    case "contrast": return <>「{from}」和「{to}」可以拿來比較</>;
  }
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
  const evidence = sourceLinks((read.context?.relation_evidence ?? []).filter(item => full?.evidence_refs.includes(item.evidence_id)));
  const style = relationStyles[relation.type];
  // 兩端依後端的來源／目標順序；對岸沒有方向，只是不畫箭頭。
  const ends = [relation.source_concept_id, relation.target_concept_id].map(id => view.concepts.find(concept => concept.concept_id === id)!);
  const [from, to] = ends.map(concept => mathPlainText(concept.label));
  return (
    <DetailPanel label="關係詳情" className="relation-card" focusKey={relation.relation_id} close={close}>
      <button aria-label="關閉關係詳情" className="quest-close" type="button" onClick={close}>
        <Icon name="close" size={22} />
      </button>
      <div className="relation-body">
        <header className="relation-title">
          <span className="relation-kind">
            <i className={`relation-swatch is-${relation.type}`} aria-hidden="true" />
            {style.bridge}：{style.meaning}
          </span>
          <h2>{relationSentence(relation.type, from, to)}</h2>
        </header>
        <section className={`relation-direction${relation.type === "contrast" ? " is-undirected" : ""}`} aria-label="這條航線連起來的兩座島">
          {ends.map((concept, index) => (
            <Fragment key={index}>
              {index === 1 && relation.type !== "contrast" && (
                <svg className="relation-arrow" aria-hidden="true" width="36" height="24" viewBox="0 0 36 24" fill="none" stroke="currentColor" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M3 12h28" /><path d="M23 4l8 8-8 8" />
                </svg>
              )}
              <button className={`relation-end is-${index === 0 ? "first" : "second"}`} type="button"
                aria-label={`${index === 0 ? "這一頭" : "另一頭"}：${index === 0 ? from : to}`}
                onClick={() => openConcept(concept.concept_id)}>
                <small>{index === 0 ? "這一頭" : "另一頭"}</small>
                <strong><MathText text={concept.label} notation={false} /></strong>
              </button>
            </Fragment>
          ))}
        </section>
        <div className="relation-why">
          <p className="relation-reason">
            <b>為什麼連在一起？</b>
            {read.error ? "說明現在讀不到，等一下再試。"
              : full ? full.learner_reason
              : <span role="status">正在找說明…</span>}
          </p>
          {read.error && <button type="button" className="quest-link" onClick={read.retry}>重新讀取</button>}
          {full && (evidence.length === 0
            ? <p className="relation-no-source">點兩端的島可以看相關教材。</p>
            : (
              <div className="quest-sources">
                {evidence.map((item) => (
                  <SourceButton
                    key={item.evidence_id}
                    apiClient={apiClient}
                    resolver={view.source_resolver}
                    evidence={item}
                    label={evidence.length === 1 ? "看教材怎麼說" : `看原文：${evidencePlace(item)}`}
                    title={evidenceSourceTitle(item)}
                    ariaLabel={`${evidence.length === 1 ? "看教材怎麼說" : "看原文"}：${evidenceSourceTitle(item)}`}
                  />
                ))}
              </div>
            ))}
        </div>
      </div>
    </DetailPanel>
  );
}

function ReviewView({ apiClient, materialId, view, progress, difficulty, isLoadingProgress, progressMessage, onReloadProgress, isStartingStudy, onStartStudy }: {
  apiClient: StudydyApiClient; materialId: string; view: KnowledgeOutlineView; progress: MapProgressView | null;
  difficulty: ReturnType<typeof useConceptDifficulty>; isLoadingProgress: boolean; progressMessage: string | null;
  onReloadProgress: () => void; isStartingStudy: boolean; onStartStudy: (id: string) => void;
}) {
  const diagnosis = useLearningDiagnosis(apiClient, materialId, view.knowledge_structure_revision, difficulty, progress, view.concepts);
  const goal = useLearningGoal(apiClient, materialId);
  const nextLearningConcept = view.concepts.find(concept => concept.concept_id === (progress ? progress.learning_path[0] : view.initial_learning_path[0]?.concept_id));
  const completedCount = progress?.concept_states.filter(state => state.status === "completed").length ?? 0;
  const startId = nextLearningConcept?.concept_id ?? view.initial_learning_path[0]?.concept_id;
  const hasToday = !!goal.goal;
  return <div className="review-diagnosis">
    <section className="review-summary" aria-label="學習進度">
      {isLoadingProgress ? <p role="status">正在讀取學習進度…</p> : progressMessage ? <p>重新讀取進度後，即可接續學習。</p> : <>
        {goal.goal ? <DailyGoalProgress stage={goal.goal.today_stage} stageKey={materialId} /> : <div className="daily-goal-progress">
          <span>已完成 {completedCount}／{view.concepts.length} 個概念</span>
          <Progress value={completedCount} max={view.concepts.length || 1} aria-label="概念學習進度" />
        </div>}
        <p className="review-next-concept">{nextLearningConcept ? <>接下來：<MathText text={nextLearningConcept.label} notation={false} /></> : progress ? "目前路線已完成，可再次練習。" : "從第一個概念開始學習。"}</p>
        {startId && <button className="primary-button" type="button" disabled={isStartingStudy} onClick={() => onStartStudy(startId)}>{isStartingStudy ? "正在接續學習…" : "繼續學習"}</button>}
      </>}
    </section>
    <div className={`review-grid${hasToday ? "" : " no-today"}`}>
      {hasToday && <TodayConceptsBlock state={goal} />}
      <LearningGoalBlock state={goal} />
      <LearningDiagnosis api={apiClient} materialId={materialId} revision={view.knowledge_structure_revision} onReview={onStartStudy} disabled={isStartingStudy || isLoadingProgress}
        difficulty={difficulty} progress={progress} concepts={view.concepts} report={diagnosis} />
    </div>
    <GoalEditorHost state={goal} />
  </div>;
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
  // 搜尋框平常收在圓鈕裡；重新整理後還原了關鍵字就直接打開，看得到還原的內容。
  const [searchOpen, setSearchOpen] = useState(!!restored?.search);
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
  const searchToggle = useRef<HTMLButtonElement>(null);
  const routePosition = useMemo(
    () => new Map(view.initial_learning_path.map((step) => [step.concept_id, step.position])),
    [view.initial_learning_path],
  );
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

  if (view.concepts.length === 0)
    return (
      <div className="quest-state">
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
      </div>
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
            progress={progress}
            isLoadingProgress={isLoadingProgress}
            progressMessage={progressMessage}
            difficulty={difficulty.data}
            isStartingStudy={isStartingStudy}
            onStartStudy={onStartStudy}
            openConcept={openConceptDetail}
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
              // 只清空搜尋；不讓外層的關卡路線把焦點移走。
              event.stopPropagation();
              setSearchQuery("");
              setSearchOpen(false);
              event.currentTarget.querySelector<HTMLButtonElement>(".map-search-toggle")?.focus();
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
          <div className="map-search-bar">
            <button ref={searchToggle} type="button" className="map-round-button map-search-toggle" aria-label="找一座島" title="找一座島" aria-expanded={searchOpen} onClick={() => { setSearchOpen(value => !value); requestAnimationFrame(() => searchInput.current?.focus()); }}><Icon name="search" size={28} /></button>
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
            <button type="button" className="map-search-close" aria-label="關閉搜尋" onClick={() => { setSearchQuery(""); setSearchOpen(false); searchToggle.current?.focus(); }}>
              <Icon name="close" size={22} />
            </button>
          </div>
          {query && (
            <div ref={searchResultsElement} className={`map-search-results${searchResults.length && searchState.query === query ? "" : " is-message"}`}>
              <p role="status">
                {searchState.query !== query ? "正在找…" : searchState.error ? searchState.error : searchResults.length
                  ? `找到 ${searchResults.length} 座島${searchResults.length > 8 ? "，顯示前 8 座，請輸入更多關鍵字縮小範圍" : ""}`
                  : `這片海上沒有「${searchQuery.trim()}」這座島，換個詞試試。`}
              </p>
              {searchResults.slice(0, 8).map((concept) => {
                const state = progress?.concept_states.find((item) => item.concept_id === concept.concept_id);
                const unassessable = !!state && state.status !== "completed" && !state.assessable;
                return (
                  <button
                    key={concept.concept_id}
                    type="button"
                    onClick={() => chooseSearchResult(concept.concept_id)}
                  >
                    <span className="map-search-position" aria-hidden="true">{routePosition.get(concept.concept_id)}</span>
                    <span>
                      <strong><MathText text={concept.label} notation={false} /></strong>
                      <small>{unassessable ? "暫時出不了題" : islandStates[state?.status ?? "not_started"].label}</small>
                    </span>
                  </button>
                );
              })}
            </div>
          )}
        </form>
    </>
  );
  return (
    <section
      className={`map-workspace${mode === "focus" ? " is-focus-mode" : ""}${mode === "review" ? " is-review-mode" : ""}${selectedConcept || selectedRelation ? " has-detail" : ""}`}
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
                hereConceptId={selectedConcept?.concept_id ?? null}
              />
            </ReactFlowProvider>
          )}
          {mode === "review" && <ReviewView apiClient={apiClient} materialId={mapRoute.materialId} view={view} progress={progress} difficulty={difficulty}
            isLoadingProgress={isLoadingProgress} progressMessage={progressMessage} onReloadProgress={onReloadProgress} isStartingStudy={isStartingStudy} onStartStudy={onStartStudy} />}
        </div>
      </div>
    </section>
  );
}
