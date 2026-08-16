import { useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { ApiClientError } from "../../api/client";
import type {
  EvidenceView as Evidence,
  KnowledgeMapView,
  LearningResourceResultView,
  LearningResourceView,
  MapConceptView as Concept,
  MapRelationView as Relation,
  MapReviewView as ReviewItem,
} from "../../api/contracts";
import "./styles.css";

const relationLabels: Record<Relation["type"], string> = {
  prerequisite: "先備",
  contains: "包含",
  application: "應用",
  example: "例子",
};

function statusLabel(value: string): string {
  const labels: Record<string, string> = {
    pending: "尚未開始",
    running: "處理中",
    succeeded: "已完成",
    partial: "部分完成",
    failed: "失敗",
    accepted: "已確認",
    needs_review: "待複核",
    unsupported: "不支援",
    retain: "保留",
    review: "需複核",
    reject: "不採用",
  };
  return labels[value] ?? value;
}

type MapViewName = "path" | "focus" | "overview" | "review";

type Selection =
  | { kind: "concept"; item: Concept }
  | { kind: "relation"; item: Relation }
  | { kind: "review"; item: ReviewItem }
  | { kind: "resource"; item: LearningResourceView };

type KnowledgeMapProps = {
  view: KnowledgeMapView;
  resourceResult: LearningResourceResultView;
  onOpenSourcePdf: (signal: AbortSignal) => Promise<void>;
  onBack: () => void;
};

type EvidenceOpenState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "succeeded" }
  | { status: "failed"; message: string };

function MapIcon({ children }: { children: ReactNode }) {
  return <span className="map-icon" aria-hidden="true">{children}</span>;
}

function EvidenceList({ evidence, isOpening, onOpen }: {
  evidence: Evidence[];
  isOpening: boolean;
  onOpen: () => void;
}) {
  if (evidence.length === 0) return <p className="map-muted">沒有可回查的教材依據。</p>;
  return (
    <ul className="evidence-list">
      {evidence.map((item) => (
        <li key={item.evidence_id}>
          <strong>第 {item.page_number} 頁</strong>
          <span>教材位置：{item.element_id}</span>
          <code>頁面區域 [{item.region.bbox.join(", ")}] · {item.region.coordinate_space}</code>
          <button type="button" className="evidence-open" disabled={isOpening} onClick={onOpen}>
            {isOpening ? "正在安全讀取來源 PDF…" : "開啟來源 PDF"}
          </button>
        </li>
      ))}
    </ul>
  );
}

function ResourceDetails({ resource }: { resource: LearningResourceView }) {
  const facts = [
    ["科目", resource.subject],
    ["概念編號", resource.concept_id],
    ["來源位置", resource.source_locator],
    ["學習用途", resource.learning_use],
    ["使用邊界", resource.use_boundary],
    ["配對依據", resource.match_basis],
    ["配對詞", resource.matched_terms.length ? resource.matched_terms.join("、") : "無"],
    ["處理狀態", statusLabel(resource.processing)],
    ["內容品質", statusLabel(resource.quality)],
    ["使用判定", statusLabel(resource.decision)],
    ["原因代碼", resource.reason_code],
    ["資源識別碼", resource.resource_key],
    ["檔案 SHA-256", resource.artifact_sha256],
  ];
  return (
    <dl className="map-facts">
      {facts.map(([label, value]) => <div key={label}><dt>{label}</dt><dd><code>{value}</code></dd></div>)}
    </dl>
  );
}

function Inspector({
  selection,
  concepts,
  evidenceOpen,
  closeButtonRef,
  onOpenEvidence,
  onClose,
}: {
  selection: Selection;
  concepts: ReadonlyMap<string, Concept>;
  evidenceOpen: EvidenceOpenState;
  closeButtonRef: RefObject<HTMLButtonElement | null>;
  onOpenEvidence: () => void;
  onClose: () => void;
}) {
  const heading = selection.kind === "concept"
    ? "概念詳情"
    : selection.kind === "relation"
      ? "關聯詳情"
      : selection.kind === "review"
        ? "複核詳情"
        : "資源詳情";
  const relationSource = selection.kind === "relation" ? concepts.get(selection.item.source) : null;
  const relationTarget = selection.kind === "relation" ? concepts.get(selection.item.target) : null;

  return (
    <aside className="map-inspector" aria-label="所選項目詳情" onKeyDown={(event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
    }}>
      <div className="inspector-heading">
        <span>{heading}</span>
        <button ref={closeButtonRef} type="button" onClick={onClose} aria-label="關閉詳情">×</button>
      </div>

      {selection.kind === "concept" && (
        <>
          <span className={`map-quality is-${selection.item.quality}`}>{statusLabel(selection.item.quality)}</span>
          <h2>{selection.item.label}</h2>
          <p>{selection.item.definition}</p>
          <code className="reason-code">{selection.item.reason_code}</code>
          {selection.item.members.length > 0 && (
            <section>
              <h3>教材中的定義</h3>
              <ol className="member-list">
                {selection.item.members.map((member) => (
                  <li key={`${member.page_number}-${member.name}`}>
                    <strong>第 {member.page_number} 頁 · {member.name}</strong>
                    <p>{member.definition}</p>
                  </li>
                ))}
              </ol>
            </section>
          )}
          <section>
            <h3>教材依據</h3>
            <EvidenceList evidence={selection.item.evidence} isOpening={evidenceOpen.status === "loading"} onOpen={onOpenEvidence} />
          </section>
        </>
      )}

      {selection.kind === "relation" && (
        <>
          <div className="relation-route" aria-label="關係方向">
            <div><span>來源概念</span><strong>{relationSource?.label ?? selection.item.source}</strong></div>
            <span aria-hidden="true">→</span>
            <div><span>目標概念</span><strong>{relationTarget?.label ?? selection.item.target}</strong></div>
          </div>
          <p className={`relation-badge is-${selection.item.type}`}>{relationLabels[selection.item.type]}</p>
          <h2>{selection.item.statement}</h2>
          <code className="reason-code">{selection.item.reason_code}</code>
          <section>
            <h3>教材依據</h3>
            <EvidenceList evidence={selection.item.evidence} isOpening={evidenceOpen.status === "loading"} onOpen={onOpenEvidence} />
          </section>
        </>
      )}

      {selection.kind === "review" && (
        <>
          <p className="map-eyebrow">待複核 · {selection.item.kind}</p>
          <h2>{selection.item.statement}</h2>
          <code className="reason-code">{selection.item.reason_code}</code>
          <section>
            <h3>教材依據</h3>
            <EvidenceList evidence={selection.item.evidence} isOpening={evidenceOpen.status === "loading"} onOpen={onOpenEvidence} />
          </section>
        </>
      )}

      {selection.kind === "resource" && (
        <>
          <p className="map-eyebrow">學習資源 · 唯讀資料</p>
          <h2>{selection.item.title}</h2>
          <ResourceDetails resource={selection.item} />
          <p className="resource-boundary">此畫面只顯示資源的公開資訊，不提供不存在的資源檔案下載。</p>
        </>
      )}

      {evidenceOpen.status === "loading" && <p className="evidence-status" role="status">正在讀取來源 PDF。</p>}
      {evidenceOpen.status === "succeeded" && <p className="evidence-status is-success" role="status">來源 PDF 已在新分頁開啟。</p>}
      {evidenceOpen.status === "failed" && (
        <div className="evidence-status is-failed" role="alert">
          <span>{evidenceOpen.message}</span>
          <button type="button" onClick={onOpenEvidence}>重試開啟</button>
        </div>
      )}
    </aside>
  );
}

function SummaryCard({ tone, label, value, detail, onClick }: {
  tone: "blue" | "green" | "orange" | "purple";
  label: string;
  value: string;
  detail: string;
  onClick?: (trigger: HTMLButtonElement) => void;
}) {
  const content = (
    <>
      <MapIcon>{tone === "blue" ? "▣" : tone === "green" ? "↗" : tone === "orange" ? "!" : "◎"}</MapIcon>
      <span><small>{label}</small><strong>{value}</strong><em>{detail}</em></span>
      {onClick && <b aria-hidden="true">›</b>}
    </>
  );
  return onClick
    ? <button type="button" className={`summary-card is-${tone}`} onClick={(event) => onClick(event.currentTarget)}>{content}</button>
    : <div className={`summary-card is-${tone}`}>{content}</div>;
}

export default function KnowledgeMap({ view, resourceResult, onOpenSourcePdf, onBack }: KnowledgeMapProps) {
  const [activeView, setActiveView] = useState<MapViewName>("focus");
  const [isSummaryOpen, setIsSummaryOpen] = useState(true);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [isLocal, setIsLocal] = useState(false);
  const [isResourceListOpen, setIsResourceListOpen] = useState(false);
  const [evidenceOpen, setEvidenceOpen] = useState<EvidenceOpenState>({ status: "idle" });
  const workspaceRef = useRef<HTMLDivElement>(null);
  const inspectorCloseButtonRef = useRef<HTMLButtonElement>(null);
  const resourceCloseButtonRef = useRef<HTMLButtonElement>(null);
  const selectionTriggerRef = useRef<HTMLElement | null>(null);
  const flowTriggerRef = useRef<{ kind: "node" | "edge"; id: string } | null>(null);
  const resourceTriggerRef = useRef<HTMLElement | null>(null);
  const evidenceRequestRef = useRef<AbortController | null>(null);
  const conceptById = useMemo(() => new Map(view.concepts.map((concept) => [concept.id, concept])), [view.concepts]);
  const relationById = useMemo(() => new Map(view.relations.map((relation) => [relation.id, relation])), [view.relations]);
  const selectedConceptId = selection?.kind === "concept" ? selection.item.id : null;
  const pathConcepts = useMemo(
    () => view.path.ordered_concept_ids.map((id) => conceptById.get(id)).filter((concept): concept is Concept => !!concept),
    [conceptById, view.path.ordered_concept_ids],
  );
  const nextConcept = useMemo(() => {
    if (pathConcepts.length === 0) return null;
    if (!selectedConceptId) return pathConcepts[0];
    const selectedIndex = pathConcepts.findIndex((concept) => concept.id === selectedConceptId);
    return selectedIndex >= 0 ? pathConcepts[selectedIndex + 1] ?? null : pathConcepts[0];
  }, [pathConcepts, selectedConceptId]);
  const visibleConceptIds = useMemo(() => {
    if (!isLocal || !selectedConceptId) return null;
    const ids = new Set([selectedConceptId]);
    for (const relation of view.relations) {
      if (relation.source === selectedConceptId) ids.add(relation.target);
      if (relation.target === selectedConceptId) ids.add(relation.source);
    }
    return ids;
  }, [isLocal, selectedConceptId, view.relations]);

  const nodes: Node[] = useMemo(
    () => view.concepts
      .filter((concept) => !visibleConceptIds || visibleConceptIds.has(concept.id))
      .map((concept) => ({
        id: concept.id,
        position: concept.position,
        data: {
          label: (
            <span className="concept-node-content">
              <small>{concept.quality === "needs_review" ? "待複核" : "教材概念"}</small>
              <strong>{concept.label}</strong>
              <span>{concept.definition}</span>
            </span>
          ),
        },
        className: [concept.quality === "needs_review" ? "needs-review" : "", concept.id === selectedConceptId ? "is-current" : ""].join(" "),
        ariaLabel: `概念：${concept.label}`,
      })),
    [selectedConceptId, view.concepts, visibleConceptIds],
  );
  const edges: Edge[] = useMemo(
    () => view.relations
      .filter((relation) => !visibleConceptIds || (visibleConceptIds.has(relation.source) && visibleConceptIds.has(relation.target)))
      .map((relation) => ({
        id: relation.id,
        source: relation.source,
        target: relation.target,
        label: relationLabels[relation.type],
        data: { relation },
        markerEnd: { type: MarkerType.ArrowClosed },
        className: `relation-${relation.type}`,
        ariaLabel: `${relationLabels[relation.type]}：${relation.statement}`,
      })),
    [view.relations, visibleConceptIds],
  );

  useEffect(() => {
    if (selection) inspectorCloseButtonRef.current?.focus();
  }, [selection]);

  useEffect(() => {
    if (isResourceListOpen && !selection) resourceCloseButtonRef.current?.focus();
  }, [isResourceListOpen, selection]);

  // 同一時間只保留一個來源 PDF 請求；切換選取、關閉面板或離開頁面時都會取消舊請求。
  useEffect(() => () => evidenceRequestRef.current?.abort(), []);

  const restoreFocus = (target: HTMLElement | null, flowTrigger = flowTriggerRef.current) => {
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        if (target?.isConnected) {
          target.focus();
          return;
        }
        const selector = flowTrigger?.kind === "node" ? ".react-flow__node[data-id]" : ".react-flow__edge[data-id]";
        const currentFlowTrigger = flowTrigger
          ? Array.from(workspaceRef.current?.querySelectorAll<HTMLElement>(selector) ?? []).find((item) => item.dataset.id === flowTrigger.id)
          : null;
        if (currentFlowTrigger) currentFlowTrigger.focus();
        else workspaceRef.current?.focus();
      });
    });
  };

  const openSelection = (nextSelection: Selection, trigger?: HTMLElement | null) => {
    evidenceRequestRef.current?.abort();
    evidenceRequestRef.current = null;
    setEvidenceOpen({ status: "idle" });
    selectionTriggerRef.current = trigger ?? (document.activeElement instanceof HTMLElement ? document.activeElement : null);
    const flowTrigger = selectionTriggerRef.current?.closest<HTMLElement>(".react-flow__node[data-id], .react-flow__edge[data-id]");
    flowTriggerRef.current = flowTrigger?.dataset.id
      ? { kind: flowTrigger.classList.contains("react-flow__node") ? "node" : "edge", id: flowTrigger.dataset.id }
      : null;
    setSelection(nextSelection);
  };

  const closeSelection = () => {
    const trigger = selectionTriggerRef.current;
    evidenceRequestRef.current?.abort();
    evidenceRequestRef.current = null;
    setEvidenceOpen({ status: "idle" });
    setSelection(null);
    restoreFocus(trigger);
  };

  const closeResourceList = () => {
    const trigger = resourceTriggerRef.current;
    setIsResourceListOpen(false);
    restoreFocus(trigger);
  };

  const openEvidence = async () => {
    if (evidenceRequestRef.current) return;
    const request = new AbortController();
    evidenceRequestRef.current = request;
    setEvidenceOpen({ status: "loading" });
    try {
      await onOpenSourcePdf(request.signal);
      // 已取消的舊請求不得再改變目前畫面或開啟分頁。
      if (!request.signal.aborted) setEvidenceOpen({ status: "succeeded" });
    } catch (error) {
      if (request.signal.aborted) return;
      setEvidenceOpen({
        status: "failed",
        message: error instanceof ApiClientError ? error.message : "目前無法安全開啟來源 PDF，請稍後再試。",
      });
    } finally {
      if (evidenceRequestRef.current === request) evidenceRequestRef.current = null;
    }
  };

  const openConcept = (concept: Concept, trigger: HTMLElement) => openSelection({ kind: "concept", item: concept }, trigger);
  const reviewCountText = `${view.review_items.length} 個關聯線索待複核`;

  const focusView = (
    <section className="focus-view" aria-label="概念聚焦視圖">
      <div className="view-heading">
        <div><MapIcon>◎</MapIcon><span><h1>概念聚焦</h1><p>選取概念，查看教材依據支持的直接關係。</p></span></div>
        <div className="relation-legend" aria-label="關係圖例">
          {(Object.keys(relationLabels) as Relation["type"][]).map((type) => <span key={type} className={`is-${type}`}>{relationLabels[type]}</span>)}
        </div>
      </div>
      <div className="focus-canvas" aria-label="知識地圖畫布">
        {view.concepts.length === 0 ? (
          <div className="map-state-message" role="status"><strong>目前沒有可顯示的概念</strong><span>{view.status.reason_code}</span></div>
        ) : (
          <ReactFlow
            nodes={nodes}
            edges={edges}
            fitView
            minZoom={0.18}
            maxZoom={1.8}
            nodesDraggable={false}
            nodesConnectable={false}
            elementsSelectable
            nodesFocusable
            edgesFocusable
            onKeyDown={(event) => {
              if (event.key !== "Enter" && event.key !== " ") return;
              const node = (event.target as Element).closest<HTMLElement>(".react-flow__node[data-id]");
              const concept = conceptById.get(node?.dataset.id ?? "");
              if (concept && node) {
                event.preventDefault();
                openConcept(concept, node);
                return;
              }
              const edge = (event.target as Element).closest<HTMLElement>(".react-flow__edge[data-id]");
              const relation = relationById.get(edge?.dataset.id ?? "");
              if (relation && edge) {
                event.preventDefault();
                openSelection({ kind: "relation", item: relation }, edge);
              }
            }}
            onNodeClick={(event, node) => {
              const concept = conceptById.get(node.id);
              const trigger = (event.target as Element).closest<HTMLElement>(".react-flow__node");
              if (concept && trigger) openConcept(concept, trigger);
            }}
            onEdgeClick={(event, edge) => {
              const trigger = (event.target as Element).closest<HTMLElement>(".react-flow__edge");
              if (trigger) openSelection({ kind: "relation", item: edge.data!.relation as Relation }, trigger);
            }}
            onPaneClick={closeSelection}
          >
            <Background variant={BackgroundVariant.Dots} gap={26} size={1} color="#dce5f5" />
            <MiniMap ariaLabel="知識地圖縮圖" pannable zoomable />
            <Controls aria-label="地圖縮放控制" showInteractive={false} />
          </ReactFlow>
        )}
      </div>
    </section>
  );

  const pathView = (
    <section className="path-view" aria-label="學習路徑視圖">
      <div className="view-heading"><div><MapIcon>⌁</MapIcon><span><h1>學習路徑</h1><p>依教材整理出的初始順序查看概念。</p></span></div></div>
      {view.path.reason_code === "PREREQUISITE_CYCLE" ? (
        <p className="map-blocking-message" role="alert">路徑出現循環關係 · PREREQUISITE_CYCLE</p>
      ) : pathConcepts.length === 0 ? (
        <p className="map-empty-message">目前沒有可用的初始學習路徑。</p>
      ) : (
        <ol className="learning-path-list">
          {pathConcepts.map((concept, index) => (
            <li key={concept.id}>
              <span className="path-step">{index + 1}</span>
              <button type="button" className={concept.id === selectedConceptId ? "is-selected" : ""} onClick={(event) => openConcept(concept, event.currentTarget)}>
                <span><strong>{concept.label}</strong><small>{concept.definition}</small></span>
                <em>{concept.quality === "needs_review" ? "待複核" : "教材概念"}</em>
              </button>
            </li>
          ))}
        </ol>
      )}
      <div className="mascot-note"><img src="/assets/studydy/welcome-wave.png" alt="Studydy 機器人揮手" /><span><strong>一步一步建立理解</strong><p>沿著教材產生的初始路徑，開啟每個概念查看定義與教材依據。</p></span></div>
    </section>
  );

  const overviewView = (
    <section className="overview-view" aria-label="概念總覽視圖">
      <div className="view-heading"><div><MapIcon>▦</MapIcon><span><h1>概念總覽</h1><p>總覽這份教材中可用的概念與整理狀態。</p></span></div></div>
      <div className="artifact-status">
        <div><span className={`map-status-marker is-${view.status.quality}`} /><strong>{view.status.quality === "accepted" ? "地圖已驗證" : "地圖可瀏覽，仍需複核"}</strong><small>{statusLabel(view.status.processing)} · {statusLabel(view.status.decision)}</small></div>
        <code>{view.status.reason_code}</code>
      </div>
      <div className="concept-overview-grid">
        {view.concepts.map((concept, index) => (
          <button type="button" key={concept.id} className={concept.id === selectedConceptId ? "is-selected" : ""} onClick={(event) => openConcept(concept, event.currentTarget)}>
            <span className="concept-index">{String(index + 1).padStart(2, "0")}</span>
            <strong>{concept.label}</strong>
            <p>{concept.definition}</p>
            <span className="concept-facts">{concept.members.length} 個教材定義 · {concept.evidence.length} 個教材依據</span>
            <em>{statusLabel(concept.quality)}</em>
          </button>
        ))}
      </div>
      {view.limitations.length > 0 && (
        <div className="limitation-list"><h2>已知限制</h2>{view.limitations.map((limitation) => <p key={limitation.reason_code}><code>{limitation.reason_code}</code><span>{limitation.affected_page_count} 頁 · {limitation.page_numbers.length ? `頁碼 ${limitation.page_numbers.join("、")}` : "沒有可顯示頁碼"}</span></p>)}</div>
      )}
    </section>
  );

  const reviewView = (
    <section className="review-view" aria-label="待複核視圖">
      <div className="view-heading"><div><MapIcon>▤</MapIcon><span><h1>待複核項目</h1><p>只列出需要複核的關聯線索，不推測學習弱點或熟練度。</p></span></div></div>
      {view.review_items.length === 0 ? (
        <div className="review-empty" role="status"><img src="/assets/studydy/welcome-wave.png" alt="" /><span><strong>目前沒有關聯線索需要複核</strong><p>{view.status.reason_code}</p></span></div>
      ) : (
        <ul className="review-list">
          {view.review_items.map((item) => (
            <li key={item.id}>
              <span className="review-status">待複核</span>
              <span className="review-copy"><strong>{item.statement}</strong><small>{item.kind} · {item.evidence.length} 個教材依據</small></span>
              <code>{item.reason_code}</code>
              <button type="button" aria-label={item.statement} onClick={(event) => openSelection({ kind: "review", item }, event.currentTarget)}>查看詳情</button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );

  const activeContent = activeView === "focus" ? focusView : activeView === "path" ? pathView : activeView === "overview" ? overviewView : reviewView;

  return (
    <div className="map-workspace" ref={workspaceRef} tabIndex={-1}>
      <header className="map-topbar">
        <div className="map-title">
          <img src="/assets/studydy/welcome-wave.png" alt="" />
          <span><small>STUDYDY</small><strong>學習知識地圖</strong><em>KNOWLEDGE MAP</em></span>
        </div>
        <div className="map-actions">
          <span>{view.concepts.length} 個概念 · {view.relations.length} 個關聯</span>
          <button type="button" onClick={(event) => {
            if (isResourceListOpen) closeResourceList();
            else {
              resourceTriggerRef.current = event.currentTarget;
              setIsResourceListOpen(true);
            }
          }} aria-expanded={isResourceListOpen}>學習資源 {resourceResult.resources.length}</button>
          <button type="button" className={isLocal ? "active" : ""} disabled={!selectedConceptId || activeView !== "focus"} onClick={() => setIsLocal((value) => !value)} aria-pressed={isLocal}>
            {isLocal ? "顯示完整地圖" : "僅顯示一跳"}
          </button>
          <button type="button" onClick={onBack}>返回處理結果</button>
        </div>
      </header>

      <nav className="map-view-tabs" aria-label="知識地圖視圖">
        {([
          ["path", "⌁", "學習路徑", "依序查看"],
          ["focus", "◎", "概念聚焦", "查看關聯"],
          ["overview", "▦", "概念總覽", "查看全部"],
          ["review", "▤", "待複核", "確認線索"],
        ] as const).map(([name, icon, english, chinese]) => (
          <button key={name} type="button" className={activeView === name ? "active" : ""} aria-current={activeView === name ? "page" : undefined} onClick={() => {
            setActiveView(name);
            if (name !== "focus") setIsLocal(false);
          }} aria-label={`${english} ${chinese}`}><span aria-hidden="true">{icon}</span>{english}<small>{chinese}</small></button>
        ))}
      </nav>

      <section className="learning-summary" aria-label="學習摘要">
        <button type="button" className="summary-toggle" aria-expanded={isSummaryOpen} onClick={() => setIsSummaryOpen((value) => !value)}>
          <span>{isSummaryOpen ? "隱藏" : "顯示"}學習摘要</span><b aria-hidden="true">{isSummaryOpen ? "⌃" : "⌄"}</b>
        </button>
        {isSummaryOpen && (
          <div className="summary-grid">
            <SummaryCard tone="blue" label="目前概念" value={selection?.kind === "concept" ? selection.item.label : "尚未選取"} detail="從地圖或路徑選取" />
            <SummaryCard tone="green" label="下一個概念" value={nextConcept?.label ?? "目前沒有下一步"} detail={view.path.reason_code} onClick={nextConcept ? () => setActiveView("path") : undefined} />
            <SummaryCard tone="orange" label="待複核" value={reviewCountText} detail="查看待確認的關聯" onClick={() => setActiveView("review")} />
            <SummaryCard tone="purple" label="學習資源" value={`${resourceResult.resources.length} 個資源`} detail={statusLabel(resourceResult.quality)} onClick={(trigger) => {
              resourceTriggerRef.current = trigger;
              setIsResourceListOpen(true);
            }} />
          </div>
        )}
      </section>

      <nav className="map-quick-path" aria-label="初始學習路徑">
        <strong>學習路徑</strong>
        {pathConcepts.length === 0 ? <span>目前沒有可用的初始學習路徑。</span> : (
          <ol>{pathConcepts.map((concept, index) => <li key={concept.id}><button type="button" aria-label={`${String(index + 1).padStart(2, "0")} ${concept.label}`} onClick={(event) => openConcept(concept, event.currentTarget)}><span>{String(index + 1).padStart(2, "0")}</span>{concept.label}</button></li>)}</ol>
        )}
      </nav>

      <div className="map-artifact-meta" aria-label="知識地圖狀態">
        <span>{statusLabel(view.status.processing)} · {statusLabel(view.status.quality)} · {statusLabel(view.status.decision)}</span>
        <code>{view.status.reason_code}</code>
        {view.limitations.map((limitation) => <code key={limitation.reason_code}>{limitation.reason_code}</code>)}
      </div>

      <div className={`map-view-layout${selection ? " has-inspector" : ""}`}>
        <main className="map-view-main">{activeContent}</main>
        {selection && <Inspector selection={selection} concepts={conceptById} evidenceOpen={evidenceOpen} closeButtonRef={inspectorCloseButtonRef} onOpenEvidence={() => void openEvidence()} onClose={closeSelection} />}
      </div>

      {isResourceListOpen && (
        <aside className="resource-list" aria-label="學習資源清單" onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            closeResourceList();
          }
        }}>
          <div className="resource-list-heading"><div><span>學習資料</span><strong>學習資源</strong></div><button ref={resourceCloseButtonRef} type="button" onClick={closeResourceList} aria-label="關閉學習資源清單">×</button></div>
          <p>{statusLabel(resourceResult.processing)} · {statusLabel(resourceResult.quality)} · {statusLabel(resourceResult.decision)}</p>
          <code>{resourceResult.reason_code}</code>
          {resourceResult.resources.length === 0 ? <p className="resource-empty">目前沒有與這份教材配對的公開學習資源。</p> : (
            <ul>{resourceResult.resources.map((resource) => <li key={resource.resource_id}><button type="button" onClick={(event) => openSelection({ kind: "resource", item: resource }, event.currentTarget)}><strong>{resource.title}</strong><span>{resource.learning_use} · {statusLabel(resource.quality)}</span></button></li>)}</ul>
          )}
        </aside>
      )}
    </div>
  );
}
