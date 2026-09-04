import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import {
  Background,
  Controls,
  ReactFlow,
  type Edge,
  type Node,
  type ReactFlowInstance,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import type { StudydyApiClient } from "../../api/client";
import type { KnowledgeStructureView } from "../../api/contracts";
import { Icon } from "../../ui/Icon";
import { StateView } from "../../ui/StateView";
import {
  documentTreeConnectors,
  hierarchyLayout,
  relationConnectors,
  safeExternalUrl,
} from "./knowledge-map";

type Concept = KnowledgeStructureView["concepts"][number];
type Mode = "overview" | "path" | "focus" | "review";

const modes: { id: Mode; label: string }[] = [
  { id: "focus", label: "概念地圖" },
  { id: "path", label: "學習順序" },
  { id: "overview", label: "總覽" },
  { id: "review", label: "內容複核" },
];

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

function EvidenceList({ apiClient, evidence, sourceArtifactId }: {
  apiClient: StudydyApiClient;
  evidence: Concept["claims"][number]["evidence"];
  sourceArtifactId: string;
}) {
  return (
    <ul className="evidence-list">
      {evidence.map((item) => (
        <li key={item.evidence_id}>
          <div>
            <strong>原始教材第 {item.page} 頁</strong>
            <span>{item.kind} · 已保留頁面定位</span>
          </div>
          <button
            className="text-button"
            type="button"
            onClick={() => window.open(
              apiClient.sourceArtifactUrl(sourceArtifactId, item.page),
              "_blank",
              "noopener,noreferrer",
            )}
          >開啟 PDF<Icon name="chevron-right" size={16} /></button>
        </li>
      ))}
    </ul>
  );
}

function ConceptDetail({ apiClient, concept, close, isStartingStudy, onStartStudy, sourceArtifactId }: {
  apiClient: StudydyApiClient;
  concept: Concept;
  close: () => void;
  isStartingStudy: boolean;
  onStartStudy: (conceptId: string) => void;
  sourceArtifactId: string;
}) {
  return (
    <aside className="detail-panel" aria-label="概念詳情">
      <header>
        <div><span className="detail-kicker">教材概念</span><h2>{concept.label}</h2></div>
        <button aria-label="關閉概念詳情" className="panel-close" type="button" onClick={close}>×</button>
      </header>
      <span className="status-badge is-review">可查看教材來源</span>
      <button
        className="primary-button detail-start"
        disabled={isStartingStudy}
        type="button"
        onClick={() => onStartStudy(concept.concept_id)}
      ><Icon name="learning" />{isStartingStudy ? "正在開始…" : "從這個概念開始"}</button>
      <section>
        <h3>教材重點</h3>
        {concept.claims.map((claim) => (
          <article className="claim-card" key={claim.claim_id}>
            <p>{claim.text}</p>
            <EvidenceList apiClient={apiClient} evidence={claim.evidence} sourceArtifactId={sourceArtifactId} />
          </article>
        ))}
      </section>
      <section>
        <h3>來源頁面</h3>
        <p className="page-list">第 {concept.source_pages.join("、")} 頁</p>
      </section>
      {concept.aliases.length > 0 && (
        <section><h3>教材中的其他名稱</h3><p className="page-list">{concept.aliases.join("、")}</p></section>
      )}
      <section>
        <h3>補充資源</h3>
        {concept.resources.length === 0 ? (
          <p className="muted-copy">目前沒有已發布的補充資源。</p>
        ) : (
          <ul className="resource-list">
            {concept.resources.map((resource) => {
              const sourceUrl = safeExternalUrl(resource.source_url);
              const licenseUrl = safeExternalUrl(resource.license_url);
              return (
                <li key={resource.resource_id}>
                  <strong>{resource.title}</strong>
                  <span>{resource.authors.join("、")}</span>
                  <small>{resource.citation}</small>
                  <small>{resource.license}</small>
                  <div>
                    {sourceUrl && <a href={sourceUrl} target="_blank" rel="noreferrer">開啟資源</a>}
                    {licenseUrl && <a href={licenseUrl} target="_blank" rel="noreferrer">授權說明</a>}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </aside>
  );
}

function Overview({ openConcept, view }: {
  openConcept: (id: string) => void;
  view: KnowledgeStructureView;
}) {
  return (
    <section aria-labelledby="overview-title">
      <div className="view-heading"><div><h2 id="overview-title">概念總覽</h2><p>從已整理的教材概念開始探索。</p></div></div>
      <div className="concept-grid">
        {view.concepts.map((concept, index) => (
          <button
            className={`concept-card accent-${index % 4}`}
            key={concept.concept_id}
            type="button"
            onClick={() => openConcept(concept.concept_id)}
          >
            <span className="concept-card__icon"><Icon name="book" /></span>
            <span className="status-badge is-review">可查看來源</span>
            <strong>{concept.label}</strong>
            <span>{concept.claims[0]?.text}</span>
            <small>{concept.claims.length} 個教材重點 · 第 {concept.source_pages.join("、")} 頁</small>
          </button>
        ))}
      </div>
    </section>
  );
}

function PathView({ isStartingStudy, onFocusConcept, onStartStudy, selectedConceptId, view }: {
  isStartingStudy: boolean;
  onFocusConcept: (id: string) => void;
  onStartStudy: (id: string) => void;
  selectedConceptId: string;
  view: KnowledgeStructureView;
}) {
  return (
    <section aria-labelledby="path-title">
      <div className="view-heading">
        <div><h2 id="path-title">教材建議學習順序</h2><p>依教材段落與 Claim Evidence 首次出現位置安排。</p></div>
        <button
          className="primary-button"
          disabled={isStartingStudy}
          type="button"
          onClick={() => onStartStudy(view.initial_learning_path[0].concept_id)}
        ><Icon name="learning" />{isStartingStudy ? "正在開始…" : "從第一步開始"}</button>
      </div>
      <ol className="learning-path">
        {view.initial_learning_path.map((step) => {
          const concept = view.concepts.find((item) => item.concept_id === step.concept_id)!;
          return (
            <li className={step.concept_id === selectedConceptId ? "is-current" : undefined} key={step.concept_id}>
              <button type="button" onClick={() => onFocusConcept(step.concept_id)}>
                <span>{step.position}</span>
                <div><strong>{concept.label}</strong><small>{step.reason}</small></div>
                <Icon name="chevron-right" />
              </button>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function TreeFallback({ collapsedSections, openConcept, selectedConceptId, toggleSection, view }: {
  collapsedSections: Set<string>;
  openConcept: (id: string) => void;
  selectedConceptId: string;
  toggleSection: (id: string) => void;
  view: KnowledgeStructureView;
}) {
  return (
    <div className="semantic-map-fallback" aria-label="教材概念階層清單">
      {view.document_tree.sections.map((section) => {
        const isCollapsed = collapsedSections.has(section.section_id);
        const isCurrent = section.concept_ids.includes(selectedConceptId);
        return (
          <section className={`semantic-group${isCurrent ? " is-current" : ""}`} key={section.section_id}>
            <header>
              <div><small>教材段落</small><h3>{section.title}</h3></div>
              <button type="button" aria-expanded={!isCollapsed} onClick={() => toggleSection(section.section_id)}>
                {isCollapsed ? "展開" : "收合"}
              </button>
            </header>
            {!isCollapsed && (
              <ul className="semantic-tree" role="tree">
                {section.concept_ids.map((conceptId) => (
                  <li aria-level={2} key={conceptId} role="treeitem">
                    <button aria-current={conceptId === selectedConceptId ? "true" : undefined} type="button" onClick={() => openConcept(conceptId)}>
                      <small>{conceptId === selectedConceptId ? "目前位置" : `段落 ${section.order + 1}`}</small>
                      <strong>{view.concepts.find((concept) => concept.concept_id === conceptId)?.label}</strong>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        );
      })}
    </div>
  );
}

function FocusView({ openConcept, selectedConceptId, setSelectedConceptId, view }: {
  openConcept: (id: string) => void;
  selectedConceptId: string;
  setSelectedConceptId: (id: string) => void;
  view: KnowledgeStructureView;
}) {
  const [collapsedSections, setCollapsedSections] = useState<Set<string>>(new Set());
  const [selectedRelationId, setSelectedRelationId] = useState<string | null>(null);
  const selected = view.concepts.find((concept) => concept.concept_id === selectedConceptId) ?? view.concepts[0];
  const layout = hierarchyLayout(view);
  const graphElement = useRef<HTMLDivElement>(null);
  const graphInstance = useRef<ReactFlowInstance | null>(null);
  const toggleSection = (sectionId: string) => setCollapsedSections((current) => {
    const next = new Set(current);
    if (next.has(sectionId)) next.delete(sectionId); else next.add(sectionId);
    return next;
  });
  const hiddenConceptIds = new Set(view.document_tree.sections
    .filter((section) => collapsedSections.has(section.section_id))
    .flatMap((section) => section.concept_ids));
  const visibleLayout = layout.filter((node) => !hiddenConceptIds.has(node.id));
  useEffect(() => {
    const element = graphElement.current;
    if (!element) return;
    let animationFrame = 0;
    const fitGraph = () => {
      window.cancelAnimationFrame(animationFrame);
      animationFrame = window.requestAnimationFrame(() => {
        void graphInstance.current?.fitView({ padding: 0.16, maxZoom: 1 });
      });
    };
    const observer = new ResizeObserver(fitGraph);
    observer.observe(element);
    fitGraph();
    return () => { window.cancelAnimationFrame(animationFrame); observer.disconnect(); };
  }, [collapsedSections]);
  const graphNodes: Node[] = visibleLayout.map((node) => {
    const concept = view.concepts.find((item) => item.concept_id === node.id);
    const section = view.document_tree.sections.find((item) => item.section_id === node.id);
    const isFocus = node.id === selected.concept_id;
    const label = concept?.label ?? section?.title ?? "教材";
    const open = concept
      ? () => openConcept(node.id)
      : section ? () => toggleSection(node.id) : () => undefined;
    return {
      id: node.id,
      position: { x: node.x, y: node.y },
      data: {
        label: <><small>{node.kind === "material" ? "教材" : node.kind === "section" ? "教材段落" : isFocus ? "目前位置" : "教材概念"}</small><strong>{label}</strong></>,
      },
      width: node.width,
      height: node.height,
      style: { width: node.width, height: node.height },
      className: `concept-flow-node is-${node.kind}${isFocus ? " is-focus" : ""}`,
      ariaLabel: `${node.kind === "section" ? "教材段落" : "教材概念"}：${label}`,
      ariaRole: node.kind === "material" ? "heading" : "button",
      domAttributes: node.kind === "material" ? undefined : nodeKeyboardAction(
        open,
        node.kind === "section" ? "按 Enter 或空白鍵展開或收合段落" : "按 Enter 或空白鍵查看概念",
      ),
      draggable: false,
      selectable: node.kind !== "material",
      focusable: node.kind !== "material",
    };
  });
  const visibleIds = new Set(visibleLayout.map((node) => node.id));
  const treeEdges: Edge[] = documentTreeConnectors(view)
    .filter((connector) => visibleIds.has(connector.source) && visibleIds.has(connector.target))
    .map((connector) => ({
      id: connector.id,
      source: connector.source,
      target: connector.target,
      type: "smoothstep",
      className: "concept-flow-edge is-structural",
      ariaLabel: "教材階層連接線",
      focusable: false,
      selectable: false,
      interactionWidth: 0,
    }));
  const relationEdges: Edge[] = relationConnectors(view)
    .filter((connector) => visibleIds.has(connector.source) && visibleIds.has(connector.target))
    .map((connector) => ({
      id: connector.id,
      source: connector.source,
      target: connector.target,
      type: "smoothstep",
      label: connector.type,
      data: { reason: connector.reason, relationType: connector.type },
      className: `concept-flow-edge is-relation is-${connector.type}`,
      ariaLabel: `${connector.type}：${connector.reason}`,
      focusable: true,
      selectable: true,
    }));
  const graphEdges = [...treeEdges, ...relationEdges];
  const selectedRelation = view.relations.find((relation) => relation.relation_id === selectedRelationId);
  return (
    <section aria-labelledby="focus-title">
      <div className="view-heading">
        <div><h2 id="focus-title">概念地圖</h2><p>教材階層決定位置；彩色線條顯示五種學習關係。</p></div>
        <label className="concept-select">焦點概念
          <select value={selected.concept_id} onChange={(event) => setSelectedConceptId(event.currentTarget.value)}>
            {view.concepts.map((concept) => <option key={concept.concept_id} value={concept.concept_id}>{concept.label}</option>)}
          </select>
        </label>
      </div>
      <div className="flat-group-list" aria-label="教材段落">
        {view.document_tree.sections.map((section) => (
          <article className={section.concept_ids.includes(selected.concept_id) ? "is-current" : undefined} key={section.section_id}>
            <small>教材段落 {section.order + 1}</small>
            <strong>{section.title}</strong>
            <button type="button" aria-expanded={!collapsedSections.has(section.section_id)} onClick={() => toggleSection(section.section_id)}>
              {collapsedSections.has(section.section_id) ? "展開" : "收合"} {section.concept_ids.length} 個概念
            </button>
          </article>
        ))}
      </div>
      <div className="focus-graph" ref={graphElement} aria-label={`「${selected.label}」所在的教材概念階層圖`}>
        <ReactFlow
          aria-label="可平移、縮放、選擇與置中的概念階層圖"
          autoPanOnNodeFocus={false}
          edges={graphEdges}
          edgesReconnectable={false}
          elementsSelectable
          fitView
          fitViewOptions={{ padding: 0.16, maxZoom: 1 }}
          maxZoom={1.8}
          minZoom={0.2}
          nodes={graphNodes}
          nodesConnectable={false}
          nodesDraggable={false}
          onInit={(instance) => { graphInstance.current = instance; void instance.fitView({ padding: 0.16, maxZoom: 1 }); }}
          onNodeClick={(_, node) => {
            if (view.concepts.some((concept) => concept.concept_id === node.id)) openConcept(node.id);
            else if (view.document_tree.sections.some((section) => section.section_id === node.id)) toggleSection(node.id);
          }}
          onEdgeClick={(_, edge) => {
            if (edge.className?.includes("is-relation")) setSelectedRelationId(edge.id);
          }}
          onEdgeMouseEnter={(_, edge) => {
            if (edge.className?.includes("is-relation")) setSelectedRelationId(edge.id);
          }}
        >
          <Background color="#dfe8fb" gap={32} size={1} />
          <Controls aria-label="概念地圖縮放與置中控制" showInteractive={false} />
        </ReactFlow>
      </div>
      <div className="relation-legend" aria-label="概念關係圖例">
        {(["prerequisite", "part_of", "application", "example", "contrast"] as const).map((type) => (
          <span className={`is-${type}`} key={type}>{type}</span>
        ))}
      </div>
      <ul className="relation-list" aria-label="概念關係">
        {view.relations.map((relation) => {
          const source = view.concepts.find((concept) => concept.concept_id === relation.source_concept_id);
          const target = view.concepts.find((concept) => concept.concept_id === relation.target_concept_id);
          return (
            <li key={relation.relation_id}>
              <button type="button" onClick={() => setSelectedRelationId(relation.relation_id)}>
                <span className={`is-${relation.type}`}>{relation.type}</span>
                <strong>{source?.label} → {target?.label}</strong>
                <small>{relation.learner_reason}</small>
              </button>
            </li>
          );
        })}
      </ul>
      {selectedRelation && (
        <aside className="relation-detail" role="status">
          <strong>{selectedRelation.type}</strong>
          <p>{selectedRelation.learner_reason}</p>
          <button type="button" onClick={() => setSelectedRelationId(null)}>關閉</button>
        </aside>
      )}
      <TreeFallback
        collapsedSections={collapsedSections}
        openConcept={openConcept}
        selectedConceptId={selected.concept_id}
        toggleSection={toggleSection}
        view={view}
      />
    </section>
  );
}

function ReviewView({ openConcept, view }: { openConcept: (id: string) => void; view: KnowledgeStructureView }) {
  return (
    <section aria-labelledby="review-title">
      <div className="view-heading"><div><h2 id="review-title">教材內容複核</h2><p>這裡顯示教材整理狀態，不代表你的學習弱點。</p></div></div>
      {view.excluded_pages.length > 0 && (
        <div className="surface partial-notice"><Icon name="warning" /><div><strong>{view.excluded_pages.length} 個頁面未安全納入</strong><p>其內容不會被用來建立學生看到的概念。</p></div></div>
      )}
      <div className="review-list">
        {view.concepts.map((concept) => (
          <article className="surface" key={concept.concept_id}>
            <span className="status-badge is-review">內容待複核</span>
            <div><strong>{concept.label}</strong><p>{concept.claims.length} 個教材重點 · 第 {concept.source_pages.join("、")} 頁</p></div>
            <button className="secondary-button" type="button" onClick={() => openConcept(concept.concept_id)}>查看概念</button>
          </article>
        ))}
      </div>
    </section>
  );
}

export function KnowledgeMapWorkspace({ apiClient, isStartingStudy, onReturnToRun, onStartStudy, sourceArtifactId, startMessage, view }: {
  apiClient: StudydyApiClient;
  isStartingStudy: boolean;
  onReturnToRun: () => void;
  onStartStudy: (conceptId: string) => void;
  sourceArtifactId: string;
  startMessage: string | null;
  view: KnowledgeStructureView;
}) {
  const initialConceptId = view.initial_learning_path[0]?.concept_id ?? view.concepts[0]?.concept_id ?? "";
  const [mode, setMode] = useState<Mode>("focus");
  const [selectedConceptId, setSelectedConceptId] = useState(initialConceptId);
  const [detailConceptId, setDetailConceptId] = useState<string | null>(null);
  const selectedConcept = useMemo(() => view.concepts.find((concept) =>
    concept.concept_id === detailConceptId) ?? null, [detailConceptId, view.concepts]);

  if (view.concepts.length === 0) return (
    <StateView
      action={<button className="secondary-button" type="button" onClick={onReturnToRun}><Icon name="arrow-left" />查看處理狀態</button>}
      description="這份教材目前沒有可安全顯示的概念。可以返回處理狀態查看結果。"
      image="/assets/studydy/empty-disappointed.png"
      title="知識地圖目前是空的"
      tone="empty"
    />
  );
  const openConcept = (id: string) => { setSelectedConceptId(id); setDetailConceptId(id); };
  const focusConcept = (id: string) => { setSelectedConceptId(id); setDetailConceptId(null); setMode("focus"); };
  const selectMode = (nextMode: Mode) => {
    setMode(nextMode);
    setDetailConceptId(null);
    window.requestAnimationFrame(() => document.getElementById(`map-tab-${nextMode}`)?.focus());
  };
  return (
    <section className={`map-workspace${selectedConcept ? " has-detail" : ""}`}>
      <header className="map-header">
        <div><p className="eyebrow">你的教材地圖</p><h1>知識地圖</h1><p>依教材段落探索概念，再查看獨立的建議學習順序。</p></div>
        <div className="map-header-actions">
          <div className="map-facts" aria-label="地圖摘要">
            <span><strong>{view.concepts.length}</strong>概念</span>
            <span><strong>{view.document_tree.sections.length}</strong>段落</span>
            <span><strong>{view.concepts.reduce((count, concept) => count + concept.resources.length, 0)}</strong>資源</span>
          </div>
          <button className="primary-button" disabled={isStartingStudy} type="button" onClick={() => onStartStudy(initialConceptId)}>
            <Icon name="learning" />{isStartingStudy ? "正在開始…" : "開始本次學習"}
          </button>
        </div>
      </header>
      {startMessage && <p className="map-start-error" role="alert">{startMessage}</p>}
      {view.status.processing === "partial" && (
        <div className="partial-banner" role="status"><Icon name="warning" /><span>部分教材內容未安全納入；目前畫面只顯示已發布內容。</span></div>
      )}
      <div className="map-tabs" role="tablist" aria-label="知識地圖檢視">
        {modes.map((item) => (
          <button
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
      <div className="map-content">
        <div aria-labelledby={`map-tab-${mode}`} className="map-view" id={`map-panel-${mode}`} role="tabpanel" tabIndex={0}>
          {mode === "overview" && <Overview openConcept={openConcept} view={view} />}
          {mode === "path" && (
            <PathView
              isStartingStudy={isStartingStudy}
              onFocusConcept={focusConcept}
              onStartStudy={onStartStudy}
              selectedConceptId={selectedConceptId}
              view={view}
            />
          )}
          {mode === "focus" && (
            <FocusView openConcept={openConcept} selectedConceptId={selectedConceptId} setSelectedConceptId={setSelectedConceptId} view={view} />
          )}
          {mode === "review" && <ReviewView openConcept={openConcept} view={view} />}
        </div>
        {selectedConcept && (
          <ConceptDetail
            apiClient={apiClient}
            close={() => setDetailConceptId(null)}
            concept={selectedConcept}
            isStartingStudy={isStartingStudy}
            onStartStudy={onStartStudy}
            sourceArtifactId={sourceArtifactId}
          />
        )}
      </div>
    </section>
  );
}
