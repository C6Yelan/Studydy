import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import {
  Background,
  Controls,
  MarkerType,
  ReactFlow,
  type Edge,
  type Node,
  type ReactFlowInstance,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import type { StudydyApiClient } from "../../api/client";
import type { KnowledgeMapView } from "../../api/contracts";
import { Icon } from "../../ui/Icon";
import { StateView } from "../../ui/StateView";
import {
  hierarchyLayout,
  isPrimaryHierarchyRelation,
  relationPresentation,
  safeExternalUrl,
} from "./knowledge-map";

type Concept = KnowledgeMapView["concepts"][number];
type Relation = KnowledgeMapView["relations"][number];
type Mode = "overview" | "path" | "focus" | "review";
type Detail = { kind: "concept"; id: string } | { kind: "relation"; id: string };

const modes: { id: Mode; label: string }[] = [
  { id: "focus", label: "概念地圖" },
  { id: "path", label: "學習順序" },
  { id: "overview", label: "總覽" },
  { id: "review", label: "內容複核" },
];

function conceptLabel(concepts: Concept[], id: string): string {
  return concepts.find((concept) => concept.formal_concept_id === id)?.label ?? "未知概念";
}

function nodeKeyboardAction(open: () => void) {
  return {
    title: "按 Enter 或空白鍵查看概念",
    onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      open();
    },
  };
}

function edgeKeyboardAction(open: () => void) {
  return {
    onKeyDown: (event: KeyboardEvent<SVGGElement>) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      open();
    },
  };
}

function RelationConnector({ relation, label }: {
  relation: Pick<Relation, "type">;
  label?: string;
}) {
  const presentation = relationPresentation(relation.type);
  const visibleLabel = label ?? presentation.label;
  return (
    <span
      aria-label={`${visibleLabel}，${presentation.directional ? "有方向" : "雙向"}`}
      className={`relation-connector ${presentation.className}`}
      data-directional={String(presentation.directional)}
    >
      <i aria-hidden="true" />
      <span>{visibleLabel}</span>
    </span>
  );
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
            <strong>原始教材第 {item.page_number} 頁</strong>
            <span>{item.kind} · 已保留頁面定位</span>
          </div>
          <button
            className="text-button"
            type="button"
            onClick={() => window.open(
              apiClient.sourceArtifactUrl(sourceArtifactId, item.page_number),
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
        onClick={() => onStartStudy(concept.formal_concept_id)}
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
        <p className="page-list">第 {concept.source_page_numbers.join("、")} 頁</p>
      </section>
      {concept.aliases.length > 0 && (
        <section>
          <h3>教材中的其他名稱</h3>
          <p className="page-list">{concept.aliases.join("、")}</p>
        </section>
      )}
      <section>
        <h3>補充資源</h3>
        {concept.supplementary_resources.length === 0 ? (
          <p className="muted-copy">目前沒有已發布的補充資源。</p>
        ) : (
          <ul className="resource-list">
            {concept.supplementary_resources.map((resource) => {
              const sourceUrl = safeExternalUrl(resource.source_url);
              const licenseUrl = safeExternalUrl(resource.license_url);
              return (
                <li key={resource.promotion_id}>
                  <strong>{resource.title}</strong>
                  <span>{resource.authors.join("、")}</span>
                  <small>{resource.citation}</small>
                  <small>{resource.license} · {resource.use_boundary}</small>
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

function RelationDetail({ apiClient, close, relation, sourceArtifactId, view }: {
  apiClient: StudydyApiClient;
  close: () => void;
  relation: Relation;
  sourceArtifactId: string;
  view: KnowledgeMapView;
}) {
  const presentation = relationPresentation(relation.type);
  const evidenceClaims = relation.relation_evidence.flatMap((reference) => {
    const owner = view.concepts.find((concept) => concept.formal_concept_id === reference.owner_formal_concept_id);
    const claim = owner?.claims.find((item) => item.claim_id === reference.claim_id);
    if (!owner || !claim) return [];
    return [{ owner, claim, evidence: claim.evidence.filter((item) => reference.evidence_ids.includes(item.evidence_id)) }];
  });
  return (
    <aside className="detail-panel" aria-label="關係詳情">
      <header>
        <div><span className="detail-kicker">概念連結</span><h2>{presentation.label}</h2></div>
        <button aria-label="關閉關係詳情" className="panel-close" type="button" onClick={close}>×</button>
      </header>
      <div className={`relation-detail-route ${presentation.className}`}>
        <strong>{conceptLabel(view.concepts, relation.source_formal_concept_id)}</strong>
        <RelationConnector relation={relation} />
        <strong>{conceptLabel(view.concepts, relation.target_formal_concept_id)}</strong>
      </div>
      <section>
        <h3>關係說明</h3>
        <p>{relation.reason}</p>
        <p className="detail-meta">推論依據：{{
          claim_semantics: "教材敘述",
          document_structure: "教材結構",
          combined: "教材敘述與結構",
        }[relation.inference_basis]}</p>
        {relation.needs_review && (
          <p className="inline-warning"><Icon name="warning" />此關係需要進一步複核。</p>
        )}
        {relation.is_in_prerequisite_cycle && (
          <p className="inline-warning"><Icon name="warning" />這條先備關係位於待複核循環中。</p>
        )}
      </section>
      <section>
        <h3>教材依據</h3>
        {evidenceClaims.map(({ owner, claim, evidence }) => (
          <article className="claim-card" key={`${owner.formal_concept_id}:${claim.claim_id}`}>
            <strong>{owner.label}</strong>
            <p>{claim.text}</p>
            <EvidenceList apiClient={apiClient} evidence={evidence} sourceArtifactId={sourceArtifactId} />
          </article>
        ))}
      </section>
    </aside>
  );
}

function Overview({ openConcept, view }: {
  openConcept: (id: string) => void;
  view: KnowledgeMapView;
}) {
  return (
    <section aria-labelledby="overview-title">
      <div className="view-heading"><div><h2 id="overview-title">概念總覽</h2><p>從已整理的教材概念開始探索。</p></div></div>
      <div className="concept-grid">
        {view.concepts.map((concept, index) => (
          <button
            className={`concept-card accent-${index % 4}`}
            key={concept.formal_concept_id}
            type="button"
            onClick={() => openConcept(concept.formal_concept_id)}
          >
            <span className="concept-card__icon"><Icon name="book" /></span>
            <span className="status-badge is-review">可查看來源</span>
            <strong>{concept.label}</strong>
            <span>{concept.claims[0]?.text}</span>
            <small>{concept.claims.length} 個教材重點 · 第 {concept.source_page_numbers.join("、")} 頁</small>
          </button>
        ))}
      </div>
    </section>
  );
}

function PathView({ isStartingStudy, onStartStudy, openConcept, view }: {
  isStartingStudy: boolean;
  onStartStudy: (id: string) => void;
  openConcept: (id: string) => void;
  view: KnowledgeMapView;
}) {
  return (
    <section aria-labelledby="path-title">
      <div className="view-heading">
        <div><h2 id="path-title">教材建議學習順序</h2><p>這是教材的固定起始順序，不會因本次作答而改寫。</p></div>
        <button
          className="primary-button"
          disabled={isStartingStudy}
          type="button"
          onClick={() => onStartStudy(view.initial_learning_path[0].formal_concept_id)}
        ><Icon name="learning" />{isStartingStudy ? "正在開始…" : "從第一步開始"}</button>
      </div>
      <ol className="learning-path">
        {view.initial_learning_path.map((step) => {
          const concept = view.concepts.find((item) =>
            item.formal_concept_id === step.formal_concept_id)!;
          return (
            <li key={step.formal_concept_id}>
              <button type="button" onClick={() => openConcept(step.formal_concept_id)}>
                <span>{step.step_number}</span>
                <div><strong>{concept.label}</strong><small>{step.placement_reason}</small></div>
                <Icon name="chevron-right" />
              </button>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function SemanticMapFallback({ openConcept, openRelation, selectedConceptId, view }: {
  openConcept: (id: string) => void;
  openRelation: (id: string) => void;
  selectedConceptId: string;
  view: KnowledgeMapView;
}) {
  const orderedNodes: KnowledgeMapView["topology"]["nodes"] = [];
  const visit = (conceptId: string) => {
    const node = view.topology.nodes.find((item) => item.formal_concept_id === conceptId);
    if (!node) return;
    orderedNodes.push(node);
    view.topology.nodes
      .filter((item) => item.primary_parent_formal_concept_id === conceptId)
      .forEach((child) => visit(child.formal_concept_id));
  };
  view.topology.roots.forEach(visit);
  const secondaryRelations = view.relations.filter((relation) =>
    !isPrimaryHierarchyRelation(view, relation));
  return (
    <div className="semantic-map-fallback" aria-label="概念階層清單">
      {view.topology.flat_groups.map((group) => {
        const groupNodes = orderedNodes.filter((node) =>
          node.flat_group_id === group.flat_group_id);
        const isCurrentGroup = group.formal_concept_ids.includes(selectedConceptId);
        return (
          <section className={`semantic-group${isCurrentGroup ? " is-current" : ""}`} key={group.flat_group_id}>
            <header>
              <div><small>教材平面段落</small><h3>{group.label}</h3></div>
              <span>第 {group.source_order.page_number} 頁</span>
            </header>
            <ul className="semantic-tree" role="tree">
              {groupNodes.map((node) => {
                const isCurrent = node.formal_concept_id === selectedConceptId;
                return (
                  <li aria-level={node.depth + 1} key={node.formal_concept_id} role="treeitem">
                    <button
                      aria-current={isCurrent ? "true" : undefined}
                      style={{ marginInlineStart: `${node.depth * 18}px` }}
                      type="button"
                      onClick={() => openConcept(node.formal_concept_id)}
                    >
                      <small>{isCurrent ? "目前位置" : node.depth === 0 ? "根概念" : `第 ${node.depth + 1} 層`}</small>
                      <strong>{conceptLabel(view.concepts, node.formal_concept_id)}</strong>
                    </button>
                  </li>
                );
              })}
            </ul>
          </section>
        );
      })}
      {secondaryRelations.length > 0 && (
        <div className="semantic-links">
          <h3>其他教材連結</h3>
          {secondaryRelations.map((relation) => {
            const label = relation.type === "prerequisite"
              ? "先備順序"
              : relation.type === "contains" ? "次要組成" : "相關連結";
            return (
              <button key={relation.relation_id} type="button" onClick={() => openRelation(relation.relation_id)}>
                <span>{conceptLabel(view.concepts, relation.source_formal_concept_id)}</span>
                <RelationConnector label={label} relation={relation} />
                <span>{conceptLabel(view.concepts, relation.target_formal_concept_id)}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

function FocusView({ openConcept, openRelation, selectedConceptId, setSelectedConceptId, view }: {
  openConcept: (id: string) => void;
  openRelation: (id: string) => void;
  selectedConceptId: string;
  setSelectedConceptId: (id: string) => void;
  view: KnowledgeMapView;
}) {
  const selected = view.concepts.find((concept) =>
    concept.formal_concept_id === selectedConceptId) ?? view.concepts[0];
  const layout = hierarchyLayout(view);
  const graphElement = useRef<HTMLDivElement>(null);
  const graphInstance = useRef<ReactFlowInstance | null>(null);
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
    return () => {
      window.cancelAnimationFrame(animationFrame);
      observer.disconnect();
    };
  }, [layout.length]);
  useEffect(() => {
    const node = layout.find((item) => item.conceptId === selected.formal_concept_id);
    if (node && graphInstance.current) {
      void graphInstance.current.setCenter(
        node.x + node.width / 2,
        node.y + node.height / 2,
        { zoom: 1.2, duration: 280 },
      );
    }
  }, [selected.formal_concept_id]);
  const graphNodes: Node[] = layout.map((node) => {
    const concept = view.concepts.find((item) => item.formal_concept_id === node.conceptId)!;
    const isFocus = node.conceptId === selected.formal_concept_id;
    const topologyNode = view.topology.nodes.find((item) =>
      item.formal_concept_id === node.conceptId)!;
    const flatGroup = view.topology.flat_groups.find((group) =>
      group.flat_group_id === topologyNode.flat_group_id)!;
    return {
      id: node.conceptId,
      position: { x: node.x, y: node.y },
      data: {
        label: <><small>{isFocus ? "目前位置" : flatGroup.label}</small><strong>{concept.label}</strong></>,
      },
      width: node.width,
      height: node.height,
      style: { width: node.width, height: node.height },
      className: `concept-flow-node${isFocus ? " is-focus" : ""}`,
      ariaLabel: `${isFocus ? "目前焦點" : "教材概念"}：${concept.label}`,
      ariaRole: "button",
      domAttributes: nodeKeyboardAction(() => openConcept(node.conceptId)),
      draggable: false,
      selectable: true,
      focusable: true,
    };
  });
  const graphEdges: Edge[] = view.relations.map((relation) => {
    const presentation = relationPresentation(relation.type);
    const isPrimary = isPrimaryHierarchyRelation(view, relation);
    const label = relation.type === "prerequisite"
      ? "先備順序"
      : relation.type === "contains"
        ? isPrimary ? "主要階層" : "次要組成"
        : "相關連結";
    const color = relation.type === "prerequisite"
      ? "#0757ff"
      : relation.type === "contains" ? isPrimary ? "#00845c" : "#b15c00" : "#7c3aed";
    return {
      id: relation.relation_id,
      source: relation.source_formal_concept_id,
      target: relation.target_formal_concept_id,
      label,
      type: "smoothstep",
      className: `concept-flow-edge ${presentation.className}${isPrimary ? " is-primary-hierarchy" : relation.type === "contains" ? " is-secondary-contains" : ""}`,
      ariaLabel: `${conceptLabel(view.concepts, relation.source_formal_concept_id)}，${label}，${conceptLabel(view.concepts, relation.target_formal_concept_id)}`,
      ariaRole: "button",
      domAttributes: edgeKeyboardAction(() => openRelation(relation.relation_id)),
      markerEnd: presentation.directional ? { type: MarkerType.ArrowClosed, color } : undefined,
      style: { stroke: color },
      labelStyle: { fill: color, fontWeight: 700 },
      interactionWidth: 24,
      focusable: true,
      selectable: true,
    };
  });
  return (
    <section aria-labelledby="focus-title">
      <div className="view-heading">
        <div><h2 id="focus-title">概念地圖</h2><p>主要階層由上往下排列；先備順序與其他教材連結保留不同線型。</p></div>
        <label className="concept-select">焦點概念
          <select value={selected.formal_concept_id} onChange={(event) => setSelectedConceptId(event.currentTarget.value)}>
            {view.concepts.map((concept) => <option key={concept.formal_concept_id} value={concept.formal_concept_id}>{concept.label}</option>)}
          </select>
        </label>
      </div>
      <div className="relation-legend" aria-label="概念連結圖例">
        <RelationConnector label="主要階層" relation={{ type: "contains" }} />
        <RelationConnector label="先備順序" relation={{ type: "prerequisite" }} />
        <RelationConnector label="相關連結" relation={{ type: "related" }} />
      </div>
      <div className="flat-group-list" aria-label="教材平面段落">
        {view.topology.flat_groups.map((group) => {
          const currentIndex = group.formal_concept_ids.indexOf(selected.formal_concept_id);
          return (
            <article className={currentIndex >= 0 ? "is-current" : undefined} key={group.flat_group_id}>
              <small>教材第 {group.source_order.page_number} 頁</small>
              <strong>{group.label}</strong>
              <span>{currentIndex >= 0
                ? `目前位於本段第 ${currentIndex + 1} 個概念`
                : `${group.formal_concept_ids.length} 個概念`}</span>
            </article>
          );
        })}
      </div>
      <div className="focus-graph" ref={graphElement} aria-label={`「${selected.label}」所在的概念階層圖`}>
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
          onEdgeClick={(_, edge) => openRelation(edge.id)}
          onInit={(instance) => {
            graphInstance.current = instance;
            void instance.fitView({ padding: 0.16, maxZoom: 1 });
          }}
          onNodeClick={(_, node) => openConcept(node.id)}
        >
          <Background color="#dfe8fb" gap={32} size={1} />
          <Controls aria-label="概念地圖縮放與置中控制" showInteractive={false} />
        </ReactFlow>
      </div>
      <SemanticMapFallback
        openConcept={openConcept}
        openRelation={openRelation}
        selectedConceptId={selected.formal_concept_id}
        view={view}
      />
    </section>
  );
}

function ReviewView({ openConcept, view }: {
  openConcept: (id: string) => void;
  view: KnowledgeMapView;
}) {
  return (
    <section aria-labelledby="review-title">
      <div className="view-heading"><div><h2 id="review-title">教材內容複核</h2><p>這裡顯示教材整理狀態，不代表你的學習弱點。</p></div></div>
      {view.excluded_pages.length > 0 && (
        <div className="surface partial-notice"><Icon name="warning" /><div><strong>{view.excluded_pages.length} 個頁面未安全納入</strong><p>其內容不會被用來建立學生看到的概念或關係。</p></div></div>
      )}
      <div className="review-list">
        {view.concepts.map((concept) => (
          <article className="surface" key={concept.formal_concept_id}>
            <span className="status-badge is-review">內容待複核</span>
            <div><strong>{concept.label}</strong><p>{concept.claims.length} 個教材重點 · 第 {concept.source_page_numbers.join("、")} 頁</p></div>
            <button className="secondary-button" type="button" onClick={() => openConcept(concept.formal_concept_id)}>查看概念</button>
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
  view: KnowledgeMapView;
}) {
  const initialConceptId = view.initial_learning_path[0]?.formal_concept_id
    ?? view.concepts[0]?.formal_concept_id ?? "";
  const [mode, setMode] = useState<Mode>("focus");
  const [selectedConceptId, setSelectedConceptId] = useState(initialConceptId);
  const [detail, setDetail] = useState<Detail | null>(null);
  const selectedConcept = useMemo(() => detail?.kind === "concept"
    ? view.concepts.find((concept) => concept.formal_concept_id === detail.id) ?? null
    : null, [detail, view.concepts]);
  const selectedRelation = useMemo(() => detail?.kind === "relation"
    ? view.relations.find((relation) => relation.relation_id === detail.id) ?? null
    : null, [detail, view.relations]);

  if (view.concepts.length === 0) return (
    <StateView
      action={<button className="secondary-button" type="button" onClick={onReturnToRun}><Icon name="arrow-left" />查看處理狀態</button>}
      description="這份教材目前沒有可安全顯示的概念。可以返回處理狀態查看結果。"
      image="/assets/studydy/empty-disappointed.png"
      title="知識地圖目前是空的"
      tone="empty"
    />
  );

  const openConcept = (id: string) => {
    setSelectedConceptId(id);
    setDetail({ kind: "concept", id });
  };
  const selectMode = (nextMode: Mode) => {
    setMode(nextMode);
    setDetail(null);
    window.requestAnimationFrame(() => document.getElementById(`map-tab-${nextMode}`)?.focus());
  };
  return (
    <section className={`map-workspace${detail ? " has-detail" : ""}`}>
      <header className="map-header">
        <div><p className="eyebrow">你的教材地圖</p><h1>知識地圖</h1><p>從概念連結開始探索，接著查看學習順序與教材來源。</p></div>
        <div className="map-header-actions">
          <div className="map-facts" aria-label="地圖摘要">
            <span><strong>{view.concepts.length}</strong>概念</span>
            <span><strong>{view.relations.length}</strong>連結</span>
            <span><strong>{view.resource_diagnostics.promoted_resources}</strong>資源</span>
          </div>
          <button
            className="primary-button"
            disabled={isStartingStudy}
            type="button"
            onClick={() => onStartStudy(initialConceptId)}
          ><Icon name="learning" />{isStartingStudy ? "正在開始…" : "開始本次學習"}</button>
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
        <div
          aria-labelledby={`map-tab-${mode}`}
          className="map-view"
          id={`map-panel-${mode}`}
          role="tabpanel"
          tabIndex={0}
        >
          {mode === "overview" && <Overview openConcept={openConcept} view={view} />}
          {mode === "path" && (
            <PathView
              isStartingStudy={isStartingStudy}
              onStartStudy={onStartStudy}
              openConcept={openConcept}
              view={view}
            />
          )}
          {mode === "focus" && (
            <FocusView
              openConcept={openConcept}
              openRelation={(id) => setDetail({ kind: "relation", id })}
              selectedConceptId={selectedConceptId}
              setSelectedConceptId={setSelectedConceptId}
              view={view}
            />
          )}
          {mode === "review" && <ReviewView openConcept={openConcept} view={view} />}
        </div>
        {selectedConcept && (
          <ConceptDetail
            apiClient={apiClient}
            close={() => setDetail(null)}
            concept={selectedConcept}
            isStartingStudy={isStartingStudy}
            onStartStudy={onStartStudy}
            sourceArtifactId={sourceArtifactId}
          />
        )}
        {selectedRelation && (
          <RelationDetail apiClient={apiClient} close={() => setDetail(null)} relation={selectedRelation} sourceArtifactId={sourceArtifactId} view={view} />
        )}
      </div>
    </section>
  );
}
