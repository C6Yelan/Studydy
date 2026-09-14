import { useEffect, useMemo, useRef, useState, type ReactNode, type KeyboardEvent } from "react";
import {
  Background,
  getViewportForBounds,
  MarkerType,
  Handle,
  Position,
  Controls,
  ReactFlow,
  useUpdateNodeInternals,
  type Edge,
  type Node,
  type NodeProps,
  type ReactFlowInstance,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import type { StudydyApiClient } from "../../api/client";
import type { KnowledgeStructureView, RelationType, LearnerProgressView } from "../../api/contracts";
import { ConceptContent } from "../../ui/ConceptContent";
import { Icon } from "../../ui/Icon";
import { StateView } from "../../ui/StateView";
import {
  learningNavigationItems,
  focusLayout,
  initialFocusConceptId,
} from "./knowledge-map";

type Concept = KnowledgeStructureView["concepts"][number];
type Mode = "overview" | "focus" | "review";
type RestoreFocus = () => void;

const modes: { id: Mode; label: string }[] = [
  { id: "focus", label: "概念地圖" },
  { id: "overview", label: "總覽" },
  { id: "review", label: "複習重點" },
];

const relationLabels: Record<RelationType, string> = {
  prerequisite: "先備", part_of: "組成", application: "應用", example: "例子", contrast: "對照",
};

const learningLabels = { not_started: "尚未練習", learning: "學習中", needs_review: "需要複習", mastered: "已掌握" } as const;

function LearningBadge({ conceptId, progress }: { conceptId: string; progress: LearnerProgressView | null }) {
  const state = progress?.concept_states.find((item) => item.concept_id === conceptId);
  return state ? <span className={`map-learning-badge is-${state.status}`}>{learningLabels[state.status]}</span> : null;
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

function ConceptDetail({ apiClient, concept, close, isStartingStudy, onStartStudy, sourceArtifactId, view, openConcept, progress, canResume, showStudyAction }: {
  showStudyAction: boolean;
  view: KnowledgeStructureView;
  progress: LearnerProgressView | null;
  canResume: boolean;
  openConcept: (id: string) => void;
  apiClient: StudydyApiClient;
  concept: Concept;
  close: () => void;
  isStartingStudy: boolean;
  onStartStudy: (conceptId: string) => void;
  sourceArtifactId: string;
}) {
  const resumesCurrent = canResume && progress?.current_concept_id === concept.concept_id;
  const relatedConcepts = useMemo(() => {
    const byId = new Map(view.concepts.map(item => [item.concept_id, item]));
    const related = new Map<string, { concept: Concept; types: Set<RelationType> }>();
    for (const relation of view.relations) {
      if (relation.source_concept_id !== concept.concept_id && relation.target_concept_id !== concept.concept_id) continue;
      const otherId = relation.source_concept_id === concept.concept_id ? relation.target_concept_id : relation.source_concept_id;
      if (!related.has(otherId)) related.set(otherId, { concept: byId.get(otherId)!, types: new Set() });
      related.get(otherId)!.types.add(relation.type);
    }
    return [...related.values()];
  }, [concept.concept_id, view.concepts, view.relations]);
  return (
    <DetailPanel label="概念詳情" focusKey={concept.concept_id} close={close}>
      <header>
        <div><span className="detail-kicker">教材概念</span><h2>{concept.label}</h2></div>
        <button aria-label="關閉概念詳情" className="panel-close" type="button" onClick={close}>×</button>
      </header>
      <LearningBadge conceptId={concept.concept_id} progress={progress} />
      {showStudyAction && <><button
        className="primary-button detail-start"
        disabled={isStartingStudy}
        type="button"
        onClick={() => onStartStudy(concept.concept_id)}
      ><Icon name="learning" />{isStartingStudy ? "請稍候…" : resumesCurrent ? "繼續這個概念" : progress ? "從這裡開始新學習" : "從這個概念開始"}</button>
      {progress && !resumesCurrent && <p className="new-study-note">會建立新的學習紀錄，原有紀錄仍保留。</p>}
      </>}
      <section>
        <h3>教材重點</h3>
        <ConceptContent claims={concept.claims} apiClient={apiClient} sourceArtifactId={sourceArtifactId} />
      </section>
      {concept.aliases.length > 0 && (
        <section><h3>教材中的其他名稱</h3><p className="page-list">{concept.aliases.join("、")}</p></section>
      )}
      {relatedConcepts.length > 0 && <details className="detail-explore" key={concept.concept_id}>
        <summary onKeyDown={event => {
          // Keep native disclosure activation out of React Flow's global Space shortcut.
          if (event.key === " ") event.stopPropagation();
        }}><span>延伸探索</span><small>{relatedConcepts.length} 個相關概念</small></summary>
        <p>依知識地圖中的直接關係，探索其他概念。</p>
        <ul className="detail-explore-list">{relatedConcepts.map(item => {
          const types = [...item.types].map(type => relationLabels[type]).join("、");
          return <li key={item.concept.concept_id}><button className="detail-explore-item" type="button"
            aria-label={`${types}：前往${item.concept.label}`} onClick={() => openConcept(item.concept.concept_id)}>
            <span><small>{types}</small><strong>{item.concept.label}</strong></span><Icon name="chevron-right" size={16} />
          </button></li>;
        })}</ul>
      </details>}
    </DetailPanel>
  );
}

function Overview({ focusInMap, view }: {
  focusInMap: (id: string) => void;
  view: KnowledgeStructureView;
}) {
  const sections = useMemo(() => view.document_tree.sections.filter(section => section.concept_ids.length > 0)
    .sort((a, b) => a.order - b.order), [view.document_tree.sections]);
  const conceptById = useMemo(() => new Map(view.concepts.map(concept => [concept.concept_id, concept])), [view.concepts]);
  const normalizeLabel = (text: string) => text.trim().replace(/\s+/gu, " ").toLocaleLowerCase();
  return <div className="overview-view">
    <div className="view-heading"><div><h2 id="overview-title">教材結構</h2><p>依教材原本順序快速瀏覽段落與概念。</p></div></div>
    {sections.length > 0 ? <section className="overview-index surface" aria-label="教材結構">
      <header><p>{sections.length} 個可探索段落</p></header>
      <div className="overview-index-columns" aria-hidden="true"><span>段落</span><span>教材內容</span><span>概念</span></div>
      <div className="overview-index-list"><ol>{sections.map(section => {
        const sectionName = `教材段落 ${section.order + 1}，${section.title}`;
        const number = <span className="overview-index-number" aria-hidden="true">{String(section.order + 1).padStart(2, "0")}</span>;
        const title = <span className="overview-index-title" title={section.title}>{section.title}</span>;
        if (section.concept_ids.some(id => !conceptById.has(id))) return <li key={section.section_id}>
          <div className="overview-index-error" role="alert">{sectionName}：概念資料不完整，請重新讀取知識地圖。</div>
        </li>;
        if (section.concept_ids.length === 1) {
          const concept = conceptById.get(section.concept_ids[0])!;
          const sameLabel = normalizeLabel(section.title) === normalizeLabel(concept.label);
          return <li key={section.section_id}><button className="overview-index-row" type="button"
            aria-label={`${sectionName}${sameLabel ? "" : `，概念：${concept.label}`}，在概念地圖中查看`}
            onClick={() => focusInMap(concept.concept_id)}>
            {number}{title}<span className="overview-index-action">{!sameLabel && <span>{concept.label}</span>}<Icon name="chevron-right" size={18} /></span>
          </button></li>;
        }
        return <li key={section.section_id}><details className="overview-index-disclosure">
          <summary className="overview-index-row" aria-label={`${sectionName}，${section.concept_ids.length} 個概念`}>
            {number}{title}<span className="overview-index-action"><span>{section.concept_ids.length} 個概念</span><Icon name="chevron-right" size={18} /></span>
          </summary>
          <ul className="overview-index-concepts">{section.concept_ids.map(id => {
            const concept = conceptById.get(id)!;
            return <li key={id}><button type="button" aria-label={`在概念地圖中查看：${concept.label}`} onClick={() => focusInMap(id)}>
              <span>{concept.label}</span><Icon name="chevron-right" size={18} />
            </button></li>;
          })}</ul>
        </details></li>;
      })}</ol></div>
    </section> : <StateView title="目前沒有可探索的教材段落" description="這份教材尚無包含概念的段落。" tone="empty" />}
    {view.excluded_pages.length > 0 && <section className="material-quality" aria-label="未能整理的頁面"><h3>有些頁面未能整理</h3><p>第 {view.excluded_pages.map((item) => item.page).join("、")} 頁未納入概念與練習，這些內容請從原始 PDF 閱讀。</p></section>}
  </div>;
}

type ConceptHandle = { id: string; type: "source" | "target"; position: Position; offset: number };
type ConceptNode = Node<{ label: ReactNode; handles: ConceptHandle[] }, "concept">;

function ConceptMapNode({ id, data }: NodeProps<ConceptNode>) {
  const updateNodeInternals = useUpdateNodeInternals();
  useEffect(() => { updateNodeInternals(id); }, [id, data.handles, updateNodeInternals]);
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
  const [expanded, setExpanded] = useState(false);
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
  }, [selectedConceptId, expanded]);
  return <nav className={`focus-navigator surface${expanded ? " is-expanded" : ""}`} aria-label="學習導覽">
    <header><h2>學習導覽</h2><span>{view.concepts.length} 個概念</span></header>
    <button className="navigator-toggle" type="button" aria-expanded={expanded} aria-controls="focus-concept-list" onClick={() => setExpanded(value => !value)}>
      <strong>學習導覽</strong><span>{view.concepts.length} 個概念 · {expanded ? "收合" : "展開"}</span>
    </button>
    {(progress || firstStep) && <p className="navigator-summary">
      {progress ? <>{currentStep && `第 ${currentStep.position} / ${view.initial_learning_path.length} 個 · `}已掌握 {mastered} 個</> : `建議從第 ${firstStep!.position} 個概念開始`}
    </p>}
    <div className="navigator-list" id="focus-concept-list" ref={list}>
      <ul>{items.map(({ concept, step }, index) => {
          const selected = concept.concept_id === selectedConceptId;
          const learningCurrent = concept.concept_id === progress?.current_concept_id;
          const status = states.get(concept.concept_id)?.status;
          const next = concept.concept_id === nextId && !learningCurrent;
          const stateLabel = status === "mastered" ? "已掌握" : status === "needs_review" ? "需要複習" : "";
          const name = [step ? `第 ${step.position} 個，${concept.label}` : concept.label,
            learningCurrent && "目前學習", stateLabel, next && "下一步"].filter(Boolean).join("；");
          return <li key={concept.concept_id}>
            {!step && (index === 0 || items[index - 1].step) && <h3 className="navigator-other">其他概念</h3>}
            <button
            ref={selected ? selectedRow : undefined} type="button" aria-label={name} aria-current={selected ? "true" : undefined}
            className={[selected && "is-selected", learningCurrent && "is-learning-current", status === "mastered" && "is-mastered", status === "needs_review" && "is-needs-review", next && "is-next-suggested"].filter(Boolean).join(" ")}
            onClick={() => focusConcept(concept.concept_id)}>
            <span className="navigator-position" aria-hidden="true">{step?.position}</span>
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

function FocusContext({ selected, directRelations, conceptById, progress, openConcept, openRelation, mobile }: {
  mobile: boolean;
  selected: Concept; directRelations: KnowledgeStructureView["relations"]; conceptById: Map<string, Concept>;
  progress: LearnerProgressView | null; openConcept: (id: string) => void; openRelation: (id: string) => void;
}) {
  const relations = mobile && (directRelations.length === 0
    ? <p className="relation-empty">這個概念目前沒有直接連結，可查看教材重點，或從學習導覽探索其他概念。</p>
    : <ul className="relation-list" aria-label="直接概念關係">{directRelations.map(relation => <li key={relation.relation_id}>
      <button type="button" onClick={() => openRelation(relation.relation_id)}>
        <span style={{ color: relationColors[relation.type] }}>{relationLabels[relation.type]}</span>
        <strong><span className="relation-direction-hint">{relation.source_concept_id === selected.concept_id ? "連向 →" : "來自 ←"}</span><span className="relation-other-concept">{conceptById.get(relation.source_concept_id === selected.concept_id ? relation.target_concept_id : relation.source_concept_id)?.label}</span></strong>
        <small>{relation.learner_reason}</small>
      </button>
    </li>)}</ul>);
  return <>
    <header className="focus-context-heading"><h2>目前焦點</h2><h3 title={selected.label}>{selected.label}</h3></header>
    <p className="focus-claim-summary">{selected.claims.find(claim => claim.text.trim())?.text}</p>
    <LearningBadge conceptId={selected.concept_id} progress={progress} />
    {mobile ? <>
      <button className="secondary-button focus-detail-action" type="button" onClick={() => openConcept(selected.concept_id)}>查看概念與來源</button>
      <details className="focus-relations"><summary>查看關係說明（{directRelations.length}）</summary>{relations}</details>
    </> : <section className="focus-relation-summary">
      <p>{directRelations.length > 0 ? <><strong>{directRelations.length}</strong> 個直接關係</> : "目前沒有直接關係"}</p>
      <p className="focus-map-hint">{directRelations.length > 0 ? "點選地圖上的概念或連線查看詳細內容。" : "可以從學習導覽選擇其他概念，或切換到「總覽」探索教材。"}</p>
    </section>}
  </>;
}

function FocusView({ openConcept, selectedConceptId, focusConcept, view, openRelation, progress, selectedRelationId, detail, studyAction }: {
  detail: ReactNode;
  studyAction: ReactNode;
  selectedRelationId: string | null;
  progress: LearnerProgressView | null;
  openConcept: (id: string, restoreFocus?: RestoreFocus) => void;
  selectedConceptId: string;
  focusConcept: (id: string) => void;
  view: KnowledgeStructureView;
  openRelation: (id: string, restoreFocus?: RestoreFocus) => void;
}) {
  const conceptById = useMemo(() => new Map(view.concepts.map(concept => [concept.concept_id, concept])), [view.concepts]);
  const [mobile, setMobile] = useState(() => window.matchMedia("(max-width: 900px)").matches);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 900px)");
    const update = () => setMobile(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  const selected = conceptById.get(selectedConceptId) ?? view.concepts[0];
  const highlightedRelation = view.relations.find((relation) => relation.relation_id === selectedRelationId);
  const graphElement = useRef<HTMLDivElement>(null);
  const graphInstance = useRef<ReactFlowInstance | null>(null);
  // React Flow can recreate an edge while measuring nodes. Resolve its stable id
  // inside this canvas when restoring focus, rather than retaining a detached SVG.
  const restoreGraphFocus = (id: string) => graphElement.current
    ?.querySelector<HTMLElement | SVGElement>(`[data-id="${CSS.escape(id)}"]`)?.focus({ preventScroll: true });
  const layout = useMemo(() => focusLayout(view, selected.concept_id), [view, selected.concept_id]);
  const layoutById = new Map(layout.map((node) => [node.id, node]));
  const directRelations = view.relations.filter((relation) => relation.source_concept_id === selected.concept_id || relation.target_concept_id === selected.concept_id);
  // Share lanes across both directions so parallel relations keep distinct labels and arrowheads.
  const pairRelations = new Map<string, typeof directRelations>();
  for (const relation of directRelations) {
    const key = [relation.source_concept_id, relation.target_concept_id].sort().join("|");
    const group = pairRelations.get(key) ?? [];
    group.push(relation);
    pairRelations.set(key, group);
  }
  const handlesByNode = new Map<string, ConceptHandle[]>();
  for (const group of pairRelations.values()) {
    group.sort((a, b) => a.relation_id.localeCompare(b.relation_id));
    group.forEach((relation, index) => {
      const source = layoutById.get(relation.source_concept_id)!;
      const target = layoutById.get(relation.target_concept_id)!;
      const sourcePosition = source.x < target.x ? Position.Right : Position.Left;
      const targetPosition = { [Position.Bottom]: Position.Top, [Position.Top]: Position.Bottom, [Position.Left]: Position.Right, [Position.Right]: Position.Left }[sourcePosition];
      for (const [nodeId, type, position] of [[source.id, "source", sourcePosition], [target.id, "target", targetPosition]] as const) {
        const handles = handlesByNode.get(nodeId) ?? [];
        handles.push({ id: `${relation.relation_id}:${type}`, type, position, offset: (index + 1) * 100 / (group.length + 1) });
        handlesByNode.set(nodeId, handles);
      }
    });
  }
  // Allocate the focal ports in neighbour order, rather than reusing each pair's lanes.
  // Monotonic ports keep the fan of curves from crossing in the open column gaps.
  const focalHandles = handlesByNode.get(selected.concept_id) ?? [];
  const relationByHandle = new Map<string, typeof directRelations[number]>(directRelations.flatMap((relation) => [
    [`${relation.relation_id}:source`, relation] as const, [`${relation.relation_id}:target`, relation] as const,
  ]));
  for (const side of [Position.Left, Position.Right]) {
    const handles = focalHandles.filter((handle) => handle.position === side);
    handles.sort((a, b) => {
      const neighbourY = (handle: ConceptHandle) => {
        const relation = relationByHandle.get(handle.id)!;
        const neighbourId = relation.source_concept_id === selected.concept_id ? relation.target_concept_id : relation.source_concept_id;
        return layoutById.get(neighbourId)!.y;
      };
      return neighbourY(a) - neighbourY(b) || a.offset - b.offset;
    });
    handles.forEach((handle, index) => { handle.offset = (index + 1) * 100 / (handles.length + 1); });
  }
  const bounds = {
    x: Math.min(...layout.map((node) => node.x)), y: Math.min(...layout.map((node) => node.y)),
    width: Math.max(...layout.map((node) => node.x + node.width)) - Math.min(...layout.map((node) => node.x)),
    height: Math.max(...layout.map((node) => node.y + node.height)) - Math.min(...layout.map((node) => node.y)),
  };
  const focalNode = layoutById.get(selected.concept_id)!;
  const centerX = focalNode.x + focalNode.width / 2;
  const centerY = focalNode.y + focalNode.height / 2;
  const frameFocus = () => {
    const element = graphElement.current;
    if (!element) return;
    const viewport = getViewportForBounds(bounds, element.clientWidth, element.clientHeight, 0.1, 1.4, 0.14);
    void graphInstance.current?.setViewport(viewport.zoom >= 0.7 ? viewport : {
      x: element.clientWidth / 2 - centerX * 0.85,
      y: element.clientHeight / 2 - centerY * 0.85,
      zoom: 0.85,
    });
  };
  useEffect(() => {
    const element = graphElement.current;
    if (!element) return;
    let frame = 0;
    const resize = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(frameFocus); };
    const observer = new ResizeObserver(resize);
    observer.observe(element);
    resize();
    return () => { cancelAnimationFrame(frame); observer.disconnect(); };
  }, [layout]);
  const nodes: Node[] = layout.map((node) => {
    const concept = view.concepts.find((item) => item.concept_id === node.id)!;
    const current = node.id === selected.concept_id;
    return {
      id: node.id, type: "concept", position: { x: node.x, y: node.y }, width: node.width,
      style: { width: node.width, opacity: highlightedRelation && ![highlightedRelation.source_concept_id, highlightedRelation.target_concept_id].includes(node.id) ? 0.5 : 1 },
      data: { handles: handlesByNode.get(node.id) ?? [], label: <>{current && <small>目前焦點</small>}<strong>{concept.label}</strong><p>{concept.claims[0]?.text}</p><LearningBadge conceptId={concept.concept_id} progress={progress} /></> },
      className: `concept-flow-node${current ? " is-focus" : ""}`,
      ariaLabel: `教材概念：${concept.label}`, ariaRole: "button",
      domAttributes: nodeKeyboardAction(() => openConcept(node.id, () => restoreGraphFocus(node.id)), `${concept.label}：查看概念與教材來源`),
      draggable: false, focusable: true,
    };
  });
  const edges: Edge[] = directRelations.map((relation) => {
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
  return <section className="focus-workspace" aria-labelledby="focus-title">
    <LearningNavigator progress={progress} view={view} selectedConceptId={selected.concept_id} focusConcept={focusConcept} />
    <section className="focus-main surface">
      <header className="focus-graph-header">
        <h2 id="focus-title">概念地圖</h2>
        <div className="relation-legend" aria-label="概念關係圖例">{Object.entries(relationLabels).map(([type, label]) => <span className={`is-${type}`} key={type} style={{ color: relationColors[type as RelationType] }}><i className={`relation-swatch${type === "application" || type === "contrast" ? " is-dashed" : ""}`} aria-hidden="true" />{label}</span>)}</div>
      </header>
    <div className="focus-graph" ref={graphElement} aria-label={`「${selected.label}」與直接相關概念`}>
      <ReactFlow nodeTypes={nodeTypes} nodes={nodes} edges={edges} proOptions={{ hideAttribution: true }}
        ariaLabelConfig={{ "controls.zoomIn.ariaLabel": "放大地圖", "controls.zoomOut.ariaLabel": "縮小地圖", "controls.fitView.ariaLabel": "顯示完整關係圖", "node.a11yDescription.default": "按 Enter 或空白鍵查看概念。", "edge.a11yDescription.default": "按 Enter 或空白鍵查看關係說明。" }} onInit={(instance) => { graphInstance.current = instance; requestAnimationFrame(frameFocus); }}
        minZoom={0.1} maxZoom={1.8} zoomOnScroll={true} preventScrolling={true} nodesConnectable={false} nodesDraggable={false} edgesReconnectable={false}
        onNodeClick={(_, node) => openConcept(node.id, () => restoreGraphFocus(node.id))} onEdgeClick={(_, edge) => openRelation(edge.id, () => restoreGraphFocus(edge.id))}>
        <Background color="var(--border)" gap={28} size={1} />
        <Controls aria-label="概念地圖縮放與置中控制" showInteractive={false} fitViewOptions={{ minZoom: 0.1, maxZoom: 1.4, padding: 0.14 }} />
      </ReactFlow>
      </div>
    </section>
    <aside className="focus-context surface" aria-label="目前焦點資訊">
      <div className="focus-context-content" hidden={!!detail && !mobile}>
        <FocusContext mobile={mobile} selected={selected} directRelations={directRelations} conceptById={conceptById} progress={progress} openConcept={openConcept} openRelation={openRelation} />
      </div>
      {detail}
    </aside>
    {studyAction}
  </section>;
}

function RelationDetail({ relation, view, apiClient, sourceArtifactId, close, openConcept }: {
  relation: KnowledgeStructureView["relations"][number]; view: KnowledgeStructureView;
  apiClient: StudydyApiClient; sourceArtifactId: string; close: () => void; openConcept: (id: string) => void;
}) {
  const evidence = view.concepts.flatMap((concept) => concept.claims.flatMap((claim) => claim.evidence)).filter((item) => relation.evidence_refs.includes(item.evidence_id));
  return <DetailPanel label="關係詳情" focusKey={relation.relation_id} close={close}>
    <header><div><span className="detail-kicker">概念之間的關係</span><h2>{relationLabels[relation.type]}</h2></div><button className="panel-close" type="button" aria-label="關閉關係詳情" onClick={close}>×</button></header>
    <section className="relation-direction">{[relation.source_concept_id, relation.target_concept_id].map((id, index) => <div key={index}>{index === 1 && <span aria-hidden="true">↓</span>}<button className="detail-related" type="button" onClick={() => openConcept(id)}><small>{index === 0 ? "來源概念" : "目標概念"}</small><strong>{view.concepts.find((concept) => concept.concept_id === id)?.label}</strong></button></div>)}</section>
    <section><h3>為什麼有這個關係？</h3><p className="claim-text">{relation.learner_reason}</p></section>
    <section><h3>教材來源</h3>{[...new Set(evidence.map((item) => item.page))].map((page) => <button className="text-button" key={page} type="button" onClick={() => window.open(apiClient.sourceArtifactUrl(sourceArtifactId, page), "_blank", "noopener,noreferrer")}>原始教材第 {page} 頁<Icon name="chevron-right" /></button>)}{evidence.length === 0 && <p>可從來源與目標概念查看相關教材。</p>}</section>
  </DetailPanel>;
}

function ReviewView({ view, progress, startStudy, busyLabel, initialConceptId, showNavigator, apiClient, sourceArtifactId }: {
  view: KnowledgeStructureView; progress: LearnerProgressView | null; initialConceptId: string;
  startStudy: (id: string) => void; busyLabel: string | null; showNavigator: () => void;
  apiClient: StudydyApiClient; sourceArtifactId: string;
}) {
  const weak = progress?.concept_states.filter((state) => state.status === "needs_review") ?? [];
  const [selectedId, setSelectedId] = useState(initialConceptId);
  const selected = weak.find(state => state.concept_id === selectedId) ?? weak[0];
  const concept = selected && view.concepts.find(item => item.concept_id === selected.concept_id)!;
  const reason = (state: typeof selected) => state.latest_is_correct === false
    ? "最近一次作答尚未答對，建議再確認教材重點。"
    : "根據最近一次學習結果，建議再看一次這個概念。";
  const points = concept ? [...concept.claims.filter(claim => selected.weak_claim_ids.includes(claim.claim_id)),
    ...concept.claims.filter(claim => !selected.weak_claim_ids.includes(claim.claim_id))].slice(0, 4) : [];
  const excerpt = (text: string) => {
    const compact = text.replace(/\s+/g, " ").trim();
    return compact.length > 96 ? `${compact.slice(0, 96)}…` : compact;
  };
  return <section aria-labelledby="review-title">
    <div className="view-heading"><div><h2 id="review-title">複習重點</h2><p>依最近一次學習結果，以下是建議優先複習的概念。</p></div></div>
    {!concept ? <div className="review-empty"><div><h3>{progress ? "目前沒有需要複習的概念" : "練習後，幫你找出複習方向"}</h3><p>{progress ? "可以回到學習導覽，繼續探索下一個概念。" : "先學習一個概念並作答，這裡就會整理需要補強的重點。"}</p><button className="secondary-button" type="button" onClick={showNavigator}>查看學習導覽</button></div></div> :
      <div className="review-workspace">
        <header className="review-context">
          <LearningBadge conceptId={concept.concept_id} progress={progress} />
          <h3>{concept.label}</h3><p>{reason(selected)}</p>
        </header>
        <aside className="review-actions" aria-label="複習行動">
          <h3>接下來怎麼做</h3><LearningBadge conceptId={concept.concept_id} progress={progress} />
          {selected.attempts > 0 && <dl><div><dt>作答</dt><dd>{selected.attempts} 次</dd></div><div><dt>答對</dt><dd>{selected.correct_answers} 次</dd></div><div><dt>已掌握重點</dt><dd>{selected.mastered_claim_ids.length} / {concept.claims.length}</dd></div></dl>}
          <p>看過重點後，回到這個概念繼續閱讀與練習。</p>
          <button className="primary-button" type="button" disabled={!!busyLabel} onClick={() => startStudy(concept.concept_id)}>{busyLabel ?? "繼續這個概念"}</button>
          <button className="text-button" type="button" onClick={showNavigator}>查看學習導覽<Icon name="chevron-right" /></button>
        </aside>
        <section className="review-points" aria-label="選中概念的複習重點" key={concept.concept_id}>
          <h3>複習重點</h3><p className="review-excerpt-note">優先查看需要補強的教材重點；以下為教材節錄。</p>
          <ol>{points.map(claim => <li key={claim.claim_id}><p>{excerpt(claim.text)}</p></li>)}</ol>
          <details className="review-full-content"><summary>查看完整教材重點</summary><ConceptContent claims={concept.claims} apiClient={apiClient} sourceArtifactId={sourceArtifactId} /></details>
        </section>
        <nav className="review-list" aria-label="需要複習的概念">
          <h3>需要複習 <small>{weak.length} 個概念</small></h3>
          <ul>{weak.map(state => {
            const item = view.concepts.find(item => item.concept_id === state.concept_id)!;
            return <li key={state.concept_id}><button type="button" aria-current={state.concept_id === concept.concept_id ? "true" : undefined} onClick={() => setSelectedId(state.concept_id)}>
              <strong>{item.label}</strong><span className="map-learning-badge is-needs_review">需要複習</span><small>{reason(state)}</small><Icon name="chevron-right" />
            </button></li>;
          })}</ul>
        </nav>
      </div>}
  </section>;
}

export function KnowledgeMapWorkspace({ apiClient, progress, isLoadingProgress, canResume, progressMessage, onReloadProgress, isStartingStudy, onReturnToRun, onStartStudy, sourceArtifactId, startMessage, view }: {
  apiClient: StudydyApiClient;
  progress: LearnerProgressView | null;
  canResume: boolean;
  isLoadingProgress: boolean;
  progressMessage: string | null;
  onReloadProgress: () => void;
  isStartingStudy: boolean;
  onReturnToRun: () => void;
  onStartStudy: (conceptId: string) => void;
  sourceArtifactId: string;
  startMessage: string | null;
  view: KnowledgeStructureView;
}) {
  const initialConceptId = progress?.current_concept_id ?? initialFocusConceptId(view);
  const [mode, setMode] = useState<Mode>("focus");
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedConceptId, setSelectedConceptId] = useState(initialConceptId);
  const [detailConceptId, setDetailConceptId] = useState<string | null>(null);
  const [relationId, setRelationId] = useState<string | null>(null);
  const opener = useRef<RestoreFocus | null>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  const searchResultsElement = useRef<HTMLDivElement>(null);
  const tabs = useRef(new Map<Mode, HTMLButtonElement>());
  useEffect(() => { if (progress?.current_concept_id) setSelectedConceptId(progress.current_concept_id); }, [progress?.study_session_id]);
  const selectedRelation = view.relations.find((relation) => relation.relation_id === relationId);
  const query = searchQuery.trim().toLocaleLowerCase();
  const searchResults = query ? view.concepts.filter((concept) => [concept.label, ...concept.aliases, ...concept.claims.map((claim) => claim.text)].some((text) => text.toLocaleLowerCase().includes(query))) : [];
  const nextConcept = progress && ["advance", "review_prerequisite", "resume"].includes(progress.next_action.action)
    ? view.concepts.find(concept => concept.concept_id === progress.next_action.target_concept_id) : undefined;
  const nextCaption = nextConcept ? "建議接著學習" : "教材導覽";
  const weakCount = progress?.concept_states.filter((state) => state.status === "needs_review").length ?? 0;
  const masteredCount = progress?.concept_states.filter((state) => state.status === "mastered").length ?? 0;
  const selectedConcept = useMemo(() => view.concepts.find((concept) =>
    concept.concept_id === detailConceptId) ?? null, [detailConceptId, view.concepts]);

  if (view.concepts.length === 0) return (
    <StateView
      action={<button className="secondary-button" type="button" onClick={onReturnToRun}><Icon name="arrow-left" />查看處理狀態</button>}
      description="這份教材目前沒有可供學習的概念。請返回處理結果查看原因。"
      image="/assets/studydy/empty-disappointed.png"
      title="知識地圖目前是空的"
      tone="empty"
    />
  );
  const rememberOpener = (restoreFocus?: RestoreFocus) => {
    if (restoreFocus) { opener.current = restoreFocus; return; }
    const element = document.activeElement as HTMLElement | null;
    if (element?.closest(".detail-panel")) return;
    opener.current = () => {
      if (element?.isConnected) element.focus({ preventScroll: true });
      else tabs.current.get(mode)?.focus({ preventScroll: true });
    };
  };
  const focusConcept = (id: string) => { setSelectedConceptId(id); setDetailConceptId(null); setRelationId(null); };
  const openConceptDetail = (id: string, restoreFocus?: RestoreFocus) => { rememberOpener(restoreFocus); setSelectedConceptId(id); setDetailConceptId(id); setRelationId(null); };
  const chooseSearchResult = (id: string) => { setMode("focus"); focusConcept(id); setSearchQuery(""); searchInput.current?.focus({ preventScroll: true }); };
  const openRelation = (id: string, restoreFocus?: RestoreFocus) => { rememberOpener(restoreFocus); setRelationId(id); setDetailConceptId(null); };
  const closeDetail = () => {
    setDetailConceptId(null); setRelationId(null);
    // React Flow measures changed nodes on the next frame before they can take focus.
    requestAnimationFrame(() => requestAnimationFrame(() => {
      if (opener.current) opener.current();
      else tabs.current.get(mode)?.focus({ preventScroll: true });
    }));
  };
  const selectMode = (nextMode: Mode) => {
    setMode(nextMode);
    setDetailConceptId(null);
    setRelationId(null);
    window.requestAnimationFrame(() => tabs.current.get(nextMode)?.focus());
  };
  const focusInMap = (id: string) => { selectMode("focus"); focusConcept(id); };
  const busyStudyLabel = isLoadingProgress ? "讀取學習進度…" : isStartingStudy ? "正在開始…" : null;
  const focusStudyConceptId = selectedConceptId;
  const focusStudyLabel = view.concepts.find(concept => concept.concept_id === focusStudyConceptId)?.label ?? "目前概念";
  const selectedIsCurrentSessionConcept = canResume && progress?.current_concept_id === focusStudyConceptId;
  const focusStudyTitle = selectedIsCurrentSessionConcept ? `接著學習「${focusStudyLabel}」`
    : canResume ? `從「${focusStudyLabel}」開始新的學習？` : `準備開始學習「${focusStudyLabel}」？`;
  const focusStudyButtonLabel = busyStudyLabel ?? (selectedIsCurrentSessionConcept ? "繼續本次學習" : progress ? "開始新的學習" : "開始學習");
  const focusStudyAction = <section className="focus-study-action surface" aria-label="學習入口">
    <div><strong>{focusStudyTitle}</strong><p>{selectedIsCurrentSessionConcept ? "你的學習進度已保留。" : canResume ? "原有學習紀錄會保留。" : "選好概念後即可開始閱讀與練習。"}</p></div>
    <button className="primary-button" disabled={isStartingStudy || isLoadingProgress} type="button" onClick={() => onStartStudy(focusStudyConceptId)}>
      <Icon name="learning" />{focusStudyButtonLabel}
    </button>
  </section>;
  const detail = selectedRelation || selectedConcept ? <>
        {selectedRelation && <RelationDetail relation={selectedRelation} view={view} apiClient={apiClient} sourceArtifactId={sourceArtifactId} close={closeDetail} openConcept={openConceptDetail} />}
        {selectedConcept && (
          <ConceptDetail
            showStudyAction={mode !== "focus"}
            apiClient={apiClient}
            close={closeDetail}
            view={view}
            openConcept={openConceptDetail}
            concept={selectedConcept}
            progress={progress}
            canResume={canResume}
            isStartingStudy={isStartingStudy}
            onStartStudy={onStartStudy}
            sourceArtifactId={sourceArtifactId}
          />
        )}
  </> : null;
  return (
    <section className={`map-workspace${mode === "focus" ? " is-focus-mode" : mode === "overview" ? " is-overview-mode" : ""}${selectedConcept || selectedRelation ? " has-detail" : ""}`}>
      <header className="map-header">
        <div><div className="map-title-row"><h1>知識地圖</h1></div></div>
        <form className="map-search" onKeyDown={(event) => { if (event.key === "Escape") { setSearchQuery(""); searchInput.current?.focus(); } }} onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setSearchQuery(""); }} role="search" aria-label="搜尋教材概念" onSubmit={(event) => { event.preventDefault(); if (searchResults[0]) chooseSearchResult(searchResults[0].concept_id); }}>
          <input ref={searchInput} type="search" aria-label="搜尋概念或關鍵字" placeholder="搜尋概念或關鍵字…" value={searchQuery} onChange={(event) => setSearchQuery(event.currentTarget.value)} onKeyDown={(event) => { if (event.key === "Escape") setSearchQuery(""); if (event.key === "ArrowDown") { event.preventDefault(); searchResultsElement.current?.querySelector<HTMLButtonElement>("button")?.focus(); } }} />
          {query && <div ref={searchResultsElement} className="map-search-results"><p role="status">{searchResults.length ? `找到 ${searchResults.length} 個概念${searchResults.length > 8 ? "，顯示前 8 個，請輸入更多關鍵字縮小範圍" : ""}` : "找不到符合的概念，試試其他關鍵字。"}</p>{searchResults.slice(0, 8).map((concept) => <button key={concept.concept_id} type="button" onClick={() => chooseSearchResult(concept.concept_id)}><strong>{concept.label}</strong><small>{concept.claims[0]?.text}</small></button>)}</div>}
        </form>
        <div className="map-header-actions">
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
      {view.excluded_pages.length > 0 && <p className="form-error" role="status">第 {view.excluded_pages.map((item) => item.page).join("、")} 頁未能整理，可在總覽查看說明。</p>}
      {progress && <details className="map-summary-container"><summary>學習摘要</summary><div className="map-learning-summary has-progress" aria-label="探索摘要">
        <button type="button" onClick={() => mode !== "focus" ? focusInMap(selectedConceptId) : openConceptDetail(selectedConceptId)}><Icon name="book" /><span><small>目前焦點</small><strong>{view.concepts.find((concept) => concept.concept_id === selectedConceptId)?.label}</strong></span><Icon name="chevron-right" /></button>
        <button type="button" onClick={() => nextConcept ? (mode !== "focus" ? focusInMap(nextConcept.concept_id) : openConceptDetail(nextConcept.concept_id)) : selectMode("focus")}><Icon name="learning" /><span><small>{nextCaption}</small><strong>{nextConcept?.label ?? "查看學習導覽"}</strong></span><Icon name="chevron-right" /></button>
        <button type="button" onClick={() => selectMode("review")}><Icon name="warning" /><span><small>複習重點</small><strong>{`${weakCount} 個概念`}</strong></span></button>
        <div className="summary-progress"><Icon name="check" /><span><small>最近一次學習</small><strong>{`${masteredCount} / ${view.concepts.length} 已掌握`}</strong></span></div>
      </div></details>}
      <div className="map-content">
        <div aria-labelledby={`map-tab-${mode}`} className="map-view" id={`map-panel-${mode}`} role="tabpanel" tabIndex={0}>
          {mode === "overview" && <Overview focusInMap={focusInMap} view={view} />}
          {mode === "focus" && (
            <FocusView selectedRelationId={relationId} progress={progress} openRelation={openRelation} openConcept={openConceptDetail} selectedConceptId={selectedConceptId} focusConcept={focusConcept} detail={detail} studyAction={focusStudyAction} view={view} />
          )}
          {mode === "review" && (progressMessage ? <div className="review-empty"><div><h2>暫時無法顯示複習重點</h2><p>重新讀取進度後，即可查看最近一次學習的複習方向。</p><button className="primary-button" type="button" onClick={onReloadProgress}>重新讀取進度</button></div></div> : <ReviewView view={view} progress={progress} startStudy={onStartStudy} busyLabel={busyStudyLabel} initialConceptId={selectedConceptId} showNavigator={() => selectMode("focus")} apiClient={apiClient} sourceArtifactId={sourceArtifactId} />)}
        </div>
      </div>

    </section>
  );
}
