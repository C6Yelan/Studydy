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
import type { KnowledgeStructureView, RelationType, LearnerProgressView, StudySessionView } from "../../api/contracts";
import { ConceptContent } from "../../ui/ConceptContent";
import { Icon } from "../../ui/Icon";
import { StateView } from "../../ui/StateView";
import {
  learningNavigationItems,
  focusGraph,
  initialFocusConceptId,
} from "./knowledge-map";

type Concept = KnowledgeStructureView["concepts"][number];
type Mode = "focus" | "review";
type RestoreFocus = () => void;

const modes: { id: Mode; label: string }[] = [
  { id: "focus", label: "概念地圖" },
  { id: "review", label: "複習重點" },
];

const relationLabels: Record<RelationType, string> = {
  prerequisite: "先備", part_of: "組成", application: "應用", example: "例子", contrast: "對照",
};

const learningLabels = { not_started: "尚未練習", learning: "學習中", needs_review: "需要複習", mastered: "已掌握" } as const;

function LearningBadge({ conceptId, progress }: { conceptId: string; progress: LearnerProgressView | null }) {
  const state = progress?.concept_states.find((item) => item.concept_id === conceptId);
  const cycle = progress?.assessment_cycles.find(item => item.concept_id === conceptId);
  const label = cycle?.outcome === "passed" && state?.status !== "mastered" ? "本輪檢測通過"
    : cycle?.pending_count ? "待補強" : state ? learningLabels[state.status] : null;
  return state && label ? <span className={`map-learning-badge is-${state.status}`}>{label}</span> : null;
}

function DetailPanel({ label, focusKey, close, children }: { label: string; focusKey: string; close: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const panel = ref.current!;
    const mobile = window.matchMedia("(max-width: 900px)");
    const update = () => {
      if (panel.open) panel.close();
      if (mobile.matches) panel.showModal(); else panel.show();
    };
    update();
    mobile.addEventListener("change", update);
    return () => { mobile.removeEventListener("change", update); panel.close(); };
  }, []);
  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      ref.current?.focus({ preventScroll: true });
      ref.current?.scrollTo(0, 0);
    });
    return () => cancelAnimationFrame(frame);
  }, [focusKey]);
  return <dialog ref={ref} className="detail-panel" aria-label={label} tabIndex={-1}
    onCancel={(event) => { event.preventDefault(); close(); }}
    onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); close(); } }}
    onClick={(event) => { const rect = event.currentTarget.getBoundingClientRect(); if (event.target === event.currentTarget && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) close(); }}>
    {children}
  </dialog>;
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

function ConceptDetail({ apiClient, concept, close, view, progress, studyAction }: {
  view: KnowledgeStructureView; progress: LearnerProgressView | null;
  apiClient: StudydyApiClient;
  concept: Concept; close: () => void; studyAction: ReactNode;
}) {
  return (
    <DetailPanel label="概念詳情" focusKey={concept.concept_id} close={close}>
      <header>
        <div><span className="detail-kicker">教材概念</span><h2>{concept.label}</h2></div>
        <button aria-label="關閉概念詳情" className="panel-close" type="button" onClick={close}>×</button>
      </header>
      <LearningBadge conceptId={concept.concept_id} progress={progress} />
      {studyAction}
      <section>
        <h3>教材重點</h3>
        <ConceptContent claims={concept.claims} apiClient={apiClient} sourceResolver={view.source_resolver} />
      </section>
    </DetailPanel>
  );
}

type ConceptHandle = { id: string; type: "source" | "target"; position: Position; offset: number };
type ConceptNode = Node<{ label: ReactNode; handles: ConceptHandle[] }, "concept">;

function ConceptMapNode({ data }: NodeProps<ConceptNode>) {
  // 節點尺寸由 ResizeObserver 量測；拓樸改變由 MapGraph 一次批次更新。
  return <>
    {data.handles.map((handle) => <Handle key={handle.id} id={handle.id} type={handle.type} position={handle.position}
      style={handle.position === Position.Left || handle.position === Position.Right ? { top: `${handle.offset}%` } : { left: `${handle.offset}%` }} />)}
    {data.label}
  </>;
}
const nodeTypes = { concept: ConceptMapNode };

const relationColors: Record<RelationType, string> = {
  prerequisite: "#5B8DEF", part_of: "#22C55E", application: "#06B6D4", example: "#F59E0B", contrast: "#EF4444",
};

function LearningNavigator({ view, selectedConceptId, focusConcept, progress }: {
  progress: LearnerProgressView | null;
  view: KnowledgeStructureView; selectedConceptId: string; focusConcept: (id: string) => void;
}) {
  const items = useMemo(() => learningNavigationItems(view), [view]);
  const states = useMemo(() => new Map(progress?.concept_states.map(state => [state.concept_id, state])), [progress]);
  const firstStep = items[0]?.step;
  const currentStep = view.initial_learning_path.find(step => step.concept_id === progress?.current_concept_id);
  const mastered = progress?.concept_states.filter(state => state.status === "mastered").length ?? 0;
  const nextId = progress && ["advance", "review_prerequisite", "resume"].includes(progress.next_action.action) ? progress.next_action.target_concept_id : null;
  const list = useRef<HTMLDivElement>(null);
  const selectedRow = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const rail = list.current!;
    const reveal = () => {
      if (!selectedRow.current || !rail.clientHeight) return;
      const item = selectedRow.current.getBoundingClientRect();
      const bounds = rail.getBoundingClientRect();
      if (item.top < bounds.top) rail.scrollTop += item.top - bounds.top;
      else if (item.bottom > bounds.bottom) rail.scrollTop += item.bottom - bounds.bottom;
    };
    reveal();
    const observer = new ResizeObserver(reveal);
    observer.observe(rail);
    return () => observer.disconnect();
  }, [selectedConceptId]);
  return <nav className="focus-navigator surface" aria-label="學習導覽">
    <header><h2>學習導覽</h2><span>{view.concepts.length} 個概念</span></header>
    {(progress || firstStep) && <p className="navigator-summary">
      {progress ? <>{currentStep && `第 ${currentStep.position} / ${view.initial_learning_path.length} 個 · `}已掌握 {mastered} 個</> : `建議從第 ${firstStep!.position} 個概念開始`}
    </p>}
    <div className="navigator-list" id="focus-concept-list" ref={list}>
      <ul>{items.map(({ concept, step }) => {
          const selected = concept.concept_id === selectedConceptId;
          const learningCurrent = concept.concept_id === progress?.current_concept_id;
          const status = states.get(concept.concept_id)?.status;
          const next = concept.concept_id === nextId && !learningCurrent;
          const stateLabel = status === "mastered" ? "已掌握" : status === "needs_review" ? "需要複習" : "";
          const name = [`第 ${step.position} 個，${concept.label}`,
            learningCurrent && "目前學習", stateLabel, next && "下一步"].filter(Boolean).join("；");
          return <li key={concept.concept_id}>
            <button
            ref={selected ? selectedRow : undefined} type="button" aria-label={name} aria-current={selected ? "true" : undefined}
            className={[selected && "is-selected", learningCurrent && "is-learning-current", status === "mastered" && "is-mastered", status === "needs_review" && "is-needs-review", next && "is-next-suggested"].filter(Boolean).join(" ")}
            onClick={() => focusConcept(concept.concept_id)}>
            <span className="navigator-position" aria-hidden="true">{step.position}</span>
            <span className="navigator-concept"><span className="navigator-label">{concept.label}</span>
              {(learningCurrent || next) && <span className="navigator-notes">
                {learningCurrent ? <span className="navigator-current">目前學習</span> : <span>下一步</span>}
              </span>}
            </span>
            {stateLabel && <span className="navigator-state" title={stateLabel} aria-hidden="true"><Icon name={status === "mastered" ? "check" : "warning"} size={15} /></span>}
          </button></li>;
      })}</ul>
    </div>
  </nav>;
}

function MapGraph({ openConcept, selectedConceptId, view, openRelation, progress, selectedRelationId, detail }: {
  detail: ReactNode;
  selectedRelationId: string | null;
  progress: LearnerProgressView | null;
  openConcept: (id: string, restoreFocus?: RestoreFocus) => void;
  selectedConceptId: string;
  view: KnowledgeStructureView;
  openRelation: (id: string, restoreFocus?: RestoreFocus) => void;
}) {
  const conceptById = useMemo(() => new Map(view.concepts.map(concept => [concept.concept_id, concept])), [view.concepts]);
  const selected = conceptById.get(selectedConceptId) ?? view.concepts[0];
  const highlightedRelation = view.relations.find((relation) => relation.relation_id === selectedRelationId);
  const graphElement = useRef<HTMLDivElement>(null);
  // React Flow can recreate an edge while measuring nodes. Resolve its stable id
  // inside this canvas when restoring focus, rather than retaining a detached SVG.
  const restoreGraphFocus = (id: string) => {
    const element = graphElement.current?.querySelector<HTMLElement | SVGElement>(`[data-id="${CSS.escape(id)}"]`);
    (element ?? navigatorToggle.current)?.focus({ preventScroll: true });
  };
  const [navigatorOpen, setNavigatorOpen] = useState(false);
  const navigatorToggle = useRef<HTMLButtonElement>(null);
  const graph = useReactFlow();
  const initialized = useNodesInitialized();
  // 受控 nodes 必須保留 React Flow 回報的尺寸，避免詳情更新後永遠未完成量測。
  const [measurements, setMeasurements] = useState<Record<string, { width: number; height: number }>>({});
  const measureNodes = (changes: NodeChange[]) => {
    setMeasurements(previous => {
      let next = previous;
      for (const change of changes) {
        if (change.type !== "dimensions" || !change.dimensions) continue;
        const before = previous[change.id];
        if (before?.width === change.dimensions.width && before?.height === change.dimensions.height) continue;
        if (next === previous) next = { ...previous };
        next[change.id] = change.dimensions;
      }
      return next;
    });
  };
  const [canvasReady, setCanvasReady] = useState(false);
  const fittedConcept = useRef<string | null>(null);
  const projection = useMemo(() => focusGraph(view, selected.concept_id), [view, selected.concept_id]);
  const layout = projection.nodes;
  const updateNodeInternals = useUpdateNodeInternals();
  const measuredLayout = useRef<typeof layout | null>(null);
  useEffect(() => {
    if (!initialized || measuredLayout.current === layout) return;
    measuredLayout.current = layout;
    // 只在換焦點／資料時更新連接點，不依尺寸回寫逐節點重測。
    updateNodeInternals(layout.map(node => node.id));
  }, [layout, initialized, updateNodeInternals]);
  const { nodes, edges } = useMemo(() => {
    const layoutById = new Map(layout.map((node) => [node.id, node]));
    // Share lanes across both directions so parallel relations keep distinct labels and arrowheads.
    const pairRelations = new Map<string, KnowledgeStructureView["relations"]>();
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
        const targetPosition = { [Position.Bottom]: Position.Top, [Position.Top]: Position.Bottom, [Position.Left]: Position.Right, [Position.Right]: Position.Left }[sourcePosition];
        for (const [nodeId, type, position] of [[source.id, "source", sourcePosition], [target.id, "target", targetPosition]] as const) {
          // 平行關係的標籤至少相隔 24px，避免卡片縮短後互相覆蓋。
          minimumHeights.set(nodeId, Math.max(minimumHeights.get(nodeId) ?? 110, (group.length + 1) * 24));
          const handles = handlesByNode.get(nodeId) ?? [];
          handles.push({ id: `${relation.relation_id}:${type}`, type, position, offset: (index + 1) * 100 / (group.length + 1) });
          handlesByNode.set(nodeId, handles);
        }
      });
    }
    const nodes: Node[] = layout.map((node) => {
      const concept = conceptById.get(node.id)!;
      const current = node.id === selected.concept_id;
      return {
        id: node.id, type: "concept", position: { x: node.x, y: node.y }, width: node.width, measured: measurements[node.id],
        style: { width: node.width, minHeight: minimumHeights.get(node.id), opacity: highlightedRelation && ![highlightedRelation.source_concept_id, highlightedRelation.target_concept_id].includes(node.id) ? 0.5 : 1 },
        data: { handles: handlesByNode.get(node.id) ?? [], label: <><strong>{concept.label}</strong><p>{concept.claims[0]?.text}</p><LearningBadge conceptId={concept.concept_id} progress={progress} /></> },
        className: `concept-flow-node${current ? " is-focus" : ""}${node.depth === 2 ? " is-secondary" : ""}`,
        ariaLabel: `教材概念：${concept.label}`, ariaRole: "button",
        domAttributes: nodeKeyboardAction(() => openConcept(node.id, () => restoreGraphFocus(node.id)), `${concept.label}：查看概念與教材來源`),
        draggable: false, focusable: true,
      };
    });
    const edges: Edge[] = projection.relations.map((relation) => {
      const source = layoutById.get(relation.source_concept_id)!;
      const target = layoutById.get(relation.target_concept_id)!;
      return {
        id: relation.relation_id, source: source.id, target: target.id, type: "default",
        sourceHandle: `${relation.relation_id}:source`,
        targetHandle: `${relation.relation_id}:target`,
        label: relationLabels[relation.type], selected: relation.relation_id === selectedRelationId,
        markerEnd: { type: MarkerType.ArrowClosed, color: relationColors[relation.type], width: 18, height: 18 },
        style: { stroke: relationColors[relation.type], strokeWidth: 1.5, strokeDasharray: relation.type === "application" || relation.type === "contrast" ? "6 4" : undefined },
        labelStyle: { fill: relationColors[relation.type], fontSize: 11 }, labelBgPadding: [4, 2],
        className: `concept-flow-edge is-relation is-${relation.type}`,
        ariaLabel: `${relationLabels[relation.type]}：${relation.learner_reason}`, focusable: true,
        domAttributes: { onKeyDown: (event: KeyboardEvent<SVGGElement>) => {
          if (event.key === "Enter" || event.key === " ") { event.preventDefault(); openRelation(relation.relation_id, () => restoreGraphFocus(relation.relation_id)); }
        } },
      };
    });
    return { nodes, edges };
  }, [layout, projection.relations, selected.concept_id, highlightedRelation, conceptById, progress, openConcept, openRelation, measurements]);
  // resize 只保留世界座標中心；使用者按適應畫面才重新 fit。
  useEffect(() => {
    const element = graphElement.current!;
    let width = element.clientWidth;
    let height = element.clientHeight;
    const observer = new ResizeObserver(() => {
      const nextWidth = element.clientWidth, nextHeight = element.clientHeight;
      setCanvasReady(nextWidth > 0 && nextHeight > 0);
      if (width && height && nextWidth && nextHeight && (width !== nextWidth || height !== nextHeight)) {
        const viewport = graph.getViewport();
        void graph.setViewport({ ...viewport, x: viewport.x + (nextWidth - width) / 2, y: viewport.y + (nextHeight - height) / 2 });
      }
      width = nextWidth; height = nextHeight;
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [graph]);
  useEffect(() => {
    if (!initialized || !canvasReady || fittedConcept.current === selected.concept_id) return;
    const frame = requestAnimationFrame(() => {
      fittedConcept.current = selected.concept_id;
      void graph.fitView({ padding: 0.18, minZoom: 0.001, maxZoom: 1.1 });
    });
    return () => cancelAnimationFrame(frame);
  }, [initialized, canvasReady, graph, selected.concept_id]);
  return <section className={`focus-workspace${detail ? " has-inspector" : ""}`} aria-label="概念地圖工作區">
    <div className="focus-graph surface" ref={graphElement} aria-label={`「${selected.label}」的兩層相關概念`}>
      <ReactFlow nodeTypes={nodeTypes} nodes={nodes} edges={edges} onNodesChange={measureNodes} proOptions={{ hideAttribution: true }}
        ariaLabelConfig={{ "controls.zoomIn.ariaLabel": "放大地圖", "controls.zoomOut.ariaLabel": "縮小地圖", "controls.fitView.ariaLabel": "適應畫面", "node.a11yDescription.default": "按 Enter 或空白鍵查看概念。", "edge.a11yDescription.default": "按 Enter 或空白鍵查看關係說明。" }}
        minZoom={0.001} maxZoom={1.8} zoomOnScroll={true} preventScrolling={true} nodesConnectable={false} nodesDraggable={false} edgesReconnectable={false}
        onNodeClick={(_, node) => openConcept(node.id, () => restoreGraphFocus(node.id))} onEdgeClick={(_, edge) => openRelation(edge.id, () => restoreGraphFocus(edge.id))}>
        <Background color="var(--border)" gap={28} size={1} />
        <Controls aria-label="地圖縮放控制" showInteractive={false} fitViewOptions={{ padding: 0.18, minZoom: 0.001, maxZoom: 1.1 }} />
      </ReactFlow>
      <div className="map-navigation" onKeyDown={event => {
        if (event.key === "Escape") { event.preventDefault(); setNavigatorOpen(false); navigatorToggle.current?.focus(); }
      }}>
        <button ref={navigatorToggle} className="secondary-button" type="button" aria-expanded={navigatorOpen} aria-controls="map-navigator"
          onClick={() => { navigatorToggle.current?.focus(); setNavigatorOpen(value => !value); }}>學習導覽</button>
        <div id="map-navigator" hidden={!navigatorOpen}>
          <LearningNavigator progress={progress} view={view} selectedConceptId={selected.concept_id}
            focusConcept={id => { setNavigatorOpen(false); openConcept(id, () => navigatorToggle.current?.focus()); }} />
        </div>
      </div>
      {(nodes.length < projection.totalNodes || edges.length < projection.totalRelations) && <p className="map-limit-note" role="status">
        兩層範圍：{nodes.length}／{projection.totalNodes} 個概念、{edges.length}／{projection.totalRelations} 條關係。
        尚有其他相關內容，可用搜尋或學習導覽選取。
      </p>}
      <div className="relation-legend" aria-label="概念關係圖例">{Object.entries(relationLabels).map(([type, label]) => <span key={type} style={{ color: relationColors[type as RelationType] }}><i className={`relation-swatch${type === "application" || type === "contrast" ? " is-dashed" : ""}`} aria-hidden="true" />{label}</span>)}</div>
    </div>
    {detail && <aside className="focus-context surface" aria-label="地圖詳情">{detail}</aside>}
  </section>;
}

function RelationDetail({ relation, view, apiClient, close, openConcept }: {
  relation: KnowledgeStructureView["relations"][number]; view: KnowledgeStructureView;
  apiClient: StudydyApiClient; close: () => void; openConcept: (id: string) => void;
}) {
  const evidence = view.concepts.flatMap((concept) => concept.claims.flatMap((claim) => claim.evidence)).filter((item) => relation.evidence_refs.includes(item.evidence_id));
  return <DetailPanel label="關係詳情" focusKey={relation.relation_id} close={close}>
    <header><div><span className="detail-kicker">概念之間的關係</span><h2>{relationLabels[relation.type]}</h2></div><button className="panel-close" type="button" aria-label="關閉關係詳情" onClick={close}>×</button></header>
    <section className="relation-direction">{[relation.source_concept_id, relation.target_concept_id].map((id, index) => <div key={index}>{index === 1 && <span aria-hidden="true">↓</span>}<button className="detail-related" type="button" onClick={() => openConcept(id)}><small>{index === 0 ? "來源概念" : "目標概念"}</small><strong>{view.concepts.find((concept) => concept.concept_id === id)?.label}</strong></button></div>)}</section>
    <section><h3>為什麼有這個關係？</h3><p className="claim-text">{relation.learner_reason}</p></section>
    <section><h3>教材來源</h3>{sourceLinks(evidence).map((item) => <SourceButton key={item.evidence_id} apiClient={apiClient} resolver={view.source_resolver} evidence={item} />)}{evidence.length === 0 && <p>可從來源與目標概念查看相關教材。</p>}</section>
  </DetailPanel>;
}

function ReviewView({ view, progress, startStudy, busyLabel, studyLabel, selectedId, onSelect, showNavigator, apiClient, loading }: {
  view: KnowledgeStructureView; progress: LearnerProgressView | null; selectedId: string; onSelect: (id: string) => void;
  startStudy: (id: string) => void; busyLabel: string | null; studyLabel: string; showNavigator: () => void;
  apiClient: StudydyApiClient; loading: boolean;
}) {
  const weak = progress?.concept_states.filter((state) => state.status === "needs_review") ?? [];
  const selected = weak.find(state => state.concept_id === selectedId) ?? weak[0];
  const concept = selected && view.concepts.find(item => item.concept_id === selected.concept_id)!;
  const points = concept?.claims.filter(claim => selected.weak_claim_ids.includes(claim.claim_id)) ?? [];
  return <section aria-labelledby="review-title">
    <div className="view-heading"><div><h2 id="review-title">複習重點</h2><p>依最近一次學習結果，以下是建議優先複習的概念。</p></div></div>
    {loading && !progress ? <p role="status">正在讀取複習重點…</p> : !concept ? <div className="review-empty"><div><h3>{progress ? "目前沒有需要複習的概念" : "練習後，幫你找出複習方向"}</h3><p>{progress ? "可以回到學習導覽，繼續探索下一個概念。" : "先學習一個概念並作答，這裡就會整理需要補強的重點。"}</p><button className="secondary-button" type="button" onClick={showNavigator}>查看學習導覽</button></div></div> :
      <div className="review-workspace">
        <nav className="review-list" aria-label="需要複習的概念">
          <h3>需要複習 <small>{weak.length} 個概念</small></h3>
          <ul>{weak.map(state => {
            const item = view.concepts.find(item => item.concept_id === state.concept_id)!;
            return <li key={state.concept_id}><button type="button" aria-current={state.concept_id === concept.concept_id ? "true" : undefined} onClick={() => onSelect(state.concept_id)}>
              <strong>{item.label}</strong><Icon name="chevron-right" />
            </button></li>;
          })}</ul>
        </nav>
        <header className="review-context"><h3>{concept.label}</h3></header>
        <section className="review-points" aria-label="選中概念的複習重點" key={concept.concept_id}>
          <h3>需要補強的重點</h3>
          <ol>{points.map(claim => <li key={claim.claim_id}>
            <p>{claim.text}</p>
            <div className="review-claim-sources" role="group" aria-label="教材來源">
              {sourceLinks(claim.evidence).map(evidence => <SourceButton key={evidence.evidence_id}
                apiClient={apiClient} resolver={view.source_resolver} evidence={evidence} />)}
            </div>
          </li>)}</ol>
        </section>
        <aside className="review-actions" aria-label="複習行動">
          <h3>接下來怎麼做</h3>
          <button className="primary-button" type="button" disabled={!!busyLabel} onClick={() => startStudy(concept.concept_id)}>{busyLabel ?? studyLabel}</button>
          <button className="text-button" type="button" onClick={showNavigator}>查看學習導覽<Icon name="chevron-right" /></button>
        </aside>
      </div>}
  </section>;
}

export function KnowledgeMapWorkspace({ apiClient, progress, isLoadingProgress, learningStateStatus, progressMessage, onReloadProgress, isStartingStudy, onReturnToRun, onAddSources, onStartStudy, startMessage, view }: {
  apiClient: StudydyApiClient;
  progress: LearnerProgressView | null;
  learningStateStatus: StudySessionView["status"] | null;
  isLoadingProgress: boolean;
  progressMessage: string | null;
  onReloadProgress: () => void;
  isStartingStudy: boolean;
  onReturnToRun: () => void;
  onAddSources: () => void;
  onStartStudy: (conceptId: string) => void;
  startMessage: string | null;
  view: KnowledgeStructureView;
}) {
  const initialConceptId = progress?.current_concept_id ?? initialFocusConceptId(view);
  // 只記住目前瀏覽器紀錄的呈現位置；登入、作答與 learner progress 仍由 API 管理。
  const [restored] = useState(() => {
    const saved = window.history.state?.knowledgeMap;
    if (saved?.revision !== view.knowledge_structure_revision) return null;
    const conceptId = (id: unknown) => typeof id === "string" && view.concepts.some(c => c.concept_id === id) ? id : null;
    return {mode: saved.mode === "review" ? "review" as const : "focus" as const,
      search: typeof saved.search === "string" ? saved.search : "",
      concept: conceptId(saved.concept), review: conceptId(saved.review), detail: conceptId(saved.detail),
      relation: typeof saved.relation === "string" && view.relations.some(r => r.relation_id === saved.relation) ? saved.relation as string : null};
  });
  const [mode, setMode] = useState<Mode>(restored?.mode ?? "focus");
  const [searchQuery, setSearchQuery] = useState(restored?.search ?? "");
  const [selectedConceptId, setSelectedConceptId] = useState(restored?.concept ?? initialConceptId);
  const [reviewConceptId, setReviewConceptId] = useState(restored?.review ?? initialConceptId);
  const [detailConceptId, setDetailConceptId] = useState<string | null>(restored?.detail ?? null);
  const [relationId, setRelationId] = useState<string | null>(restored?.relation ?? null);
  useEffect(() => {
    window.history.replaceState({...window.history.state, knowledgeMap: {
      revision:view.knowledge_structure_revision,mode,search:searchQuery,concept:selectedConceptId,
      review:reviewConceptId,detail:detailConceptId,relation:relationId,
    }}, "", window.location.href);
  }, [view.knowledge_structure_revision,mode,searchQuery,selectedConceptId,reviewConceptId,detailConceptId,relationId]);
  const opener = useRef<RestoreFocus | null>(null);
  const restoreFrame = useRef(0);
  useEffect(() => () => cancelAnimationFrame(restoreFrame.current), []);
  const searchInput = useRef<HTMLInputElement>(null);
  const searchResultsElement = useRef<HTMLDivElement>(null);
  const tabs = useRef(new Map<Mode, HTMLButtonElement>());
  const hasBrowsed = useRef(!!restored?.concept);
  useEffect(() => {
    if (!hasBrowsed.current && progress?.current_concept_id && view.concepts.some(concept => concept.concept_id === progress.current_concept_id)) {
      setSelectedConceptId(progress.current_concept_id);
    }
  }, [progress?.study_session_id, progress?.current_concept_id, view.concepts]);
  const selectedRelation = view.relations.find((relation) => relation.relation_id === relationId);
  const query = searchQuery.trim().toLocaleLowerCase();
  const searchResults = query ? view.concepts.filter((concept) => [concept.label, ...concept.aliases, ...concept.claims.map((claim) => claim.text)].some((text) => text.toLocaleLowerCase().includes(query))) : [];
  const selectedConcept = useMemo(() => view.concepts.find((concept) =>
    concept.concept_id === detailConceptId) ?? null, [detailConceptId, view.concepts]);

  if (view.concepts.length === 0) return (
    <StateView
      action={<div className="state-actions">
        <button className="secondary-button" type="button" onClick={onReturnToRun}><Icon name="arrow-left" />查看處理狀態</button>
      </div>}
      description="這份教材目前沒有可供學習的概念。請返回處理結果查看原因。"
      image="/assets/studydy/empty-disappointed.png"
      title="知識地圖目前是空的"
      tone="empty"
    />
  );
  const rememberOpener = (restoreFocus?: RestoreFocus) => {
    // 快速關閉再開啟詳情時，不讓前一次延遲恢復搶走新面板的焦點。
    cancelAnimationFrame(restoreFrame.current);
    if (restoreFocus) { opener.current = restoreFocus; return; }
    const element = document.activeElement as HTMLElement | null;
    if (element?.closest(".detail-panel")) return;
    opener.current = () => {
      if (element?.isConnected) element.focus({ preventScroll: true });
      else tabs.current.get(mode)?.focus({ preventScroll: true });
    };
  };
  const openConceptDetail = (id: string, restoreFocus?: RestoreFocus) => { hasBrowsed.current = true; rememberOpener(restoreFocus); setSelectedConceptId(id); setDetailConceptId(id); setRelationId(null); };
  const chooseSearchResult = (id: string) => { setMode("focus"); openConceptDetail(id); setSearchQuery(""); searchInput.current?.focus({ preventScroll: true }); };
  const openRelation = (id: string, restoreFocus?: RestoreFocus) => { rememberOpener(restoreFocus); setRelationId(id); setDetailConceptId(null); };
  const closeDetail = () => {
    const closingFocus = document.activeElement;
    setDetailConceptId(null); setRelationId(null);
    // React Flow measures changed nodes on the next frame before they can take focus.
    cancelAnimationFrame(restoreFrame.current);
    restoreFrame.current = requestAnimationFrame(() => {
      restoreFrame.current = requestAnimationFrame(() => {
        // 使用者已開始搜尋等新操作時，不以延遲恢復搶走焦點。
        if (document.activeElement !== closingFocus && document.activeElement !== document.body) return;
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
  const canResume = learningStateStatus === "active" || learningStateStatus === "no_safe";
  const selectedIsCurrentSessionConcept = canResume && progress?.current_concept_id === focusStudyConceptId;
  const focusStudyButtonLabel = busyStudyLabel ?? (learningStateStatus === "completed" ? "查看學習成果" : selectedIsCurrentSessionConcept ? "繼續學習" : canResume ? "從這個概念繼續" : "開始學習");
  const focusStudyAction = <section className="concept-study-action" aria-label="學習入口">
    <button className="primary-button" disabled={isStartingStudy || isLoadingProgress} type="button" onClick={() => onStartStudy(focusStudyConceptId)}>
      <Icon name="learning" />{focusStudyButtonLabel}
    </button>
  </section>;
  const detail = selectedRelation || selectedConcept ? <>
        {selectedRelation && <RelationDetail relation={selectedRelation} view={view} apiClient={apiClient} close={closeDetail} openConcept={openConceptDetail} />}
        {selectedConcept && (
          <ConceptDetail
            apiClient={apiClient}
            close={closeDetail}
            view={view}
            concept={selectedConcept}
            studyAction={focusStudyAction}
            progress={progress}

          />
        )}
  </> : null;
  return (
    <section className={`map-workspace${mode === "focus" ? " is-focus-mode" : ""}${selectedConcept || selectedRelation ? " has-detail" : ""}`}>
      <header className="map-header">
        <div><div className="map-title-row"><h1>知識地圖</h1></div></div>
        <form className="map-search" onKeyDown={(event) => { if (event.key === "Escape") { setSearchQuery(""); searchInput.current?.focus(); } }} onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setSearchQuery(""); }} role="search" aria-label="搜尋教材概念" onSubmit={(event) => { event.preventDefault(); if (searchResults[0]) chooseSearchResult(searchResults[0].concept_id); }}>
          <input ref={searchInput} type="search" aria-label="搜尋概念或關鍵字" placeholder="搜尋概念或關鍵字…" value={searchQuery} onChange={(event) => setSearchQuery(event.currentTarget.value)} onKeyDown={(event) => { if (event.key === "Escape") setSearchQuery(""); if (event.key === "ArrowDown") { event.preventDefault(); searchResultsElement.current?.querySelector<HTMLButtonElement>("button")?.focus(); } }} />
          {query && <div ref={searchResultsElement} className="map-search-results"><p role="status">{searchResults.length ? `找到 ${searchResults.length} 個概念${searchResults.length > 8 ? "，顯示前 8 個，請輸入更多關鍵字縮小範圍" : ""}` : "找不到符合的概念，試試其他關鍵字。"}</p>{searchResults.slice(0, 8).map((concept) => <button key={concept.concept_id} type="button" onClick={() => chooseSearchResult(concept.concept_id)}><strong>{concept.label}</strong><small>{concept.claims[0]?.text}</small></button>)}</div>}
        </form>
        <div className="map-header-actions">
          <button className="secondary-button" type="button" onClick={onAddSources}>新增教材</button>
          <div className="map-facts" aria-label="地圖摘要">
            <span><strong>{view.concepts.length}</strong>概念</span>
            <span><strong>{view.document_tree.sections.length}</strong>段落</span>
            <span><strong>{view.relations.length}</strong>關係</span>
          </div>
        </div>
      </header>
      {progressMessage && <div className="partial-banner" role="status"><span>{progressMessage}</span><button className="text-button" type="button" onClick={onReloadProgress}>重新讀取進度</button></div>}
      {startMessage && <p className="map-start-error" role="alert">{startMessage}</p>}
      <div className="map-tabs" role="tablist" aria-label="知識地圖檢視">
        {modes.map((item) => (
          <button
            ref={element => { if (element) tabs.current.set(item.id, element); else tabs.current.delete(item.id); }}
            aria-selected={mode === item.id}
            aria-controls={`map-panel-${item.id}`}
            className={mode === item.id ? "is-active" : undefined}
            id={`map-tab-${item.id}`}
            key={item.id}
            role="tab"
            tabIndex={mode === item.id ? 0 : -1}
            type="button"
            onClick={() => selectMode(item.id)}
            onKeyDown={(event) => {
              const currentIndex = modes.findIndex((entry) => entry.id === item.id);
              let nextIndex = currentIndex;
              if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % modes.length;
              else if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + modes.length) % modes.length;
              else if (event.key === "Home") nextIndex = 0;
              else if (event.key === "End") nextIndex = modes.length - 1;
              else return;
              event.preventDefault();
              selectMode(modes[nextIndex].id);
            }}
          >{item.label}</button>
        ))}
      </div>
      {view.excluded_pages.length > 0 && <p className="form-error" role="status">第 {view.excluded_pages.map((item) => item.page).join("、")} 頁未納入概念與練習，請從原始教材閱讀。</p>}
      <div className="map-content">
        <div aria-labelledby={`map-tab-${mode}`} className="map-view" id={`map-panel-${mode}`} role="tabpanel" tabIndex={0}>
          {mode === "focus" && (
            <ReactFlowProvider><MapGraph selectedRelationId={relationId} progress={progress} openRelation={openRelation} openConcept={openConceptDetail} selectedConceptId={selectedConceptId} detail={detail} view={view} /></ReactFlowProvider>
          )}
          {mode === "review" && (progressMessage ? <div className="review-empty"><div><h2>暫時無法顯示複習重點</h2><p>重新讀取進度後，即可查看最近一次學習的複習方向。</p><button className="primary-button" type="button" onClick={onReloadProgress}>重新讀取進度</button></div></div> : <ReviewView loading={isLoadingProgress} view={view} progress={progress} startStudy={onStartStudy} busyLabel={busyStudyLabel} studyLabel={learningStateStatus === "completed" ? "查看學習成果" : "繼續這個概念"} selectedId={reviewConceptId} onSelect={setReviewConceptId} showNavigator={() => selectMode("focus")} apiClient={apiClient} />)}
        </div>
      </div>

    </section>
  );
}
