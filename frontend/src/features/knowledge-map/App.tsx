import { useEffect, useMemo, useState } from "react";
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
import type { Concept, Evidence, KnowledgeMapView, Relation, ReviewItem } from "./types";

const fixtures = {
  programming: {
    label: "程式設計｜陣列與字串",
    url: "/local-fixtures/programming-07b1c1c1-knowledge-map-view.json",
  },
  finance: {
    label: "財政學｜所得稅",
    url: "/local-fixtures/finance-01547e2c-knowledge-map-view.json",
  },
} as const;

const relationLabels: Record<Relation["type"], string> = {
  prerequisite: "先備",
  contains: "包含",
  similar: "相似",
  confusing: "易混淆",
  application: "應用",
  example: "例子",
};

type FixtureName = keyof typeof fixtures;
type Selection = { kind: "concept"; item: Concept } | { kind: "relation"; item: Relation } | { kind: "review"; item: ReviewItem };

function isKnowledgeMapView(value: unknown): value is KnowledgeMapView {
  if (!value || typeof value !== "object") return false;
  const view = value as Partial<KnowledgeMapView>;
  return (
    view.schema === "knowledge-map-view/v1" &&
    Array.isArray(view.concepts) &&
    Array.isArray(view.relations) &&
    Array.isArray(view.review_items) &&
    Array.isArray(view.limitations) &&
    !!view.path &&
    Array.isArray(view.path.ordered_concept_ids)
  );
}

function EvidenceList({ evidence }: { evidence: Evidence[] }) {
  if (evidence.length === 0) return <p className="muted">沒有 Evidence locator</p>;
  return (
    <ul className="evidence-list">
      {evidence.map((item) => (
        <li key={item.evidence_id}>
          <strong>第 {item.page_number} 頁</strong>
          <span>{item.element_id}</span>
          <code>[{item.region.bbox.join(", ")}]</code>
        </li>
      ))}
    </ul>
  );
}

function Inspector({ selection, onClose }: { selection: Selection; onClose: () => void }) {
  return (
    <aside className="inspector" aria-label="所選項目詳情">
      <div className="inspector-heading">
        <span>{selection.kind === "concept" ? "CONCEPT" : selection.kind === "relation" ? "RELATION" : "REVIEW"}</span>
        <button type="button" onClick={onClose} aria-label="關閉詳情">×</button>
      </div>
      {selection.kind === "concept" ? (
        <>
          <h2>{selection.item.label}</h2>
          <p>{selection.item.definition}</p>
          {selection.item.members.length > 1 && (
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
        </>
      ) : (
        <>
          <p className="eyebrow">
            {selection.kind === "relation" ? relationLabels[selection.item.type] : `待複核 · ${selection.item.kind}`}
          </p>
          <h2>{selection.item.statement}</h2>
          <code className="reason-code">{selection.item.reason_code}</code>
        </>
      )}
      <section>
        <h3>Evidence locator</h3>
        <EvidenceList evidence={selection.item.evidence} />
      </section>
    </aside>
  );
}

export default function App() {
  const [fixtureName, setFixtureName] = useState<FixtureName>("programming");
  const [view, setView] = useState<KnowledgeMapView | null>(null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [isLocal, setIsLocal] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setView(null);
    setSelection(null);
    setIsLocal(false);
    setError(null);
    fetch(fixtures[fixtureName].url, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json() as Promise<unknown>;
      })
      .then((nextView) => {
        if (!isKnowledgeMapView(nextView)) throw new Error("knowledge-map-view/v1 格式不符");
        setView(nextView);
      })
      .catch((cause: unknown) => {
        if ((cause as { name?: string }).name !== "AbortError") {
          setError(cause instanceof Error ? cause.message : "無法載入知識地圖");
        }
      });
    return () => controller.abort();
  }, [fixtureName]);

  const conceptById = useMemo(
    () => new Map(view?.concepts.map((concept) => [concept.id, concept]) ?? []),
    [view],
  );
  const selectedConceptId = selection?.kind === "concept" ? selection.item.id : null;
  const visibleConceptIds = useMemo(() => {
    if (!view || !isLocal || !selectedConceptId) return null;
    const ids = new Set([selectedConceptId]);
    for (const relation of view.relations) {
      if (relation.source === selectedConceptId) ids.add(relation.target);
      if (relation.target === selectedConceptId) ids.add(relation.source);
    }
    return ids;
  }, [isLocal, selectedConceptId, view]);

  const nodes: Node[] = useMemo(
    () =>
      view?.concepts
        .filter((concept) => !visibleConceptIds || visibleConceptIds.has(concept.id))
        .map((concept) => ({
          id: concept.id,
          position: concept.position,
          data: { label: concept.label },
          className: concept.quality === "needs_review" ? "needs-review" : "",
          ariaLabel: `概念：${concept.label}`,
        })) ?? [],
    [view, visibleConceptIds],
  );
  const edges: Edge[] = useMemo(
    () =>
      view?.relations
        .filter(
          (relation) =>
            !visibleConceptIds ||
            (visibleConceptIds.has(relation.source) && visibleConceptIds.has(relation.target)),
        )
        .map((relation) => ({
          id: relation.id,
          source: relation.source,
          target: relation.target,
          label: relationLabels[relation.type],
          data: { relation },
          markerEnd: { type: MarkerType.ArrowClosed },
          className: `relation-${relation.type}`,
          ariaLabel: `${relationLabels[relation.type]}：${relation.statement}`,
        })) ?? [],
    [view, visibleConceptIds],
  );

  return (
    <main className="workspace">
      <header className="topbar">
        <div className="brand"><span>STUDYDY</span><strong>知識地圖</strong></div>
        <label>
          <span className="sr-only">選擇教材</span>
          <select value={fixtureName} onChange={(event) => setFixtureName(event.target.value as FixtureName)}>
            {Object.entries(fixtures).map(([name, fixture]) => <option key={name} value={name}>{fixture.label}</option>)}
          </select>
        </label>
        <div className="map-actions">
          <span>{view ? `${view.concepts.length} 概念 · ${view.relations.length} 關聯` : "載入中"}</span>
          <button
            type="button"
            className={isLocal ? "active" : ""}
            disabled={!selectedConceptId}
            onClick={() => setIsLocal((value) => !value)}
            aria-pressed={isLocal}
          >
            {isLocal ? "顯示完整地圖" : "僅顯示一跳"}
          </button>
        </div>
      </header>

      <section className="canvas" aria-label="知識地圖畫布">
        {error && <div className="state-message error" role="alert"><strong>載入失敗</strong><span>{error}</span></div>}
        {!error && !view && <div className="state-message" role="status">正在整理知識地圖…</div>}
        {view && (
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
              if (concept) {
                event.preventDefault();
                setSelection({ kind: "concept", item: concept });
              }
            }}
            onNodeClick={(_, node) => {
              const concept = conceptById.get(node.id);
              if (concept) setSelection({ kind: "concept", item: concept });
            }}
            onEdgeClick={(_, edge) => {
              setIsLocal(false);
              setSelection({ kind: "relation", item: edge.data!.relation as Relation });
            }}
            onPaneClick={() => {
              setIsLocal(false);
              setSelection(null);
            }}
          >
            <Background variant={BackgroundVariant.Dots} gap={24} size={1} color="#c7c2b6" />
            <MiniMap ariaLabel="知識地圖縮圖" pannable zoomable />
            <Controls aria-label="地圖縮放控制" showInteractive={false} />
          </ReactFlow>
        )}
      </section>

      {view && (
        <section className="status-rail" aria-label="地圖狀態與待複核項目">
          <div>
            <span className="status-dot" />
            <strong>{view.status.quality === "accepted" ? "已驗證" : "可瀏覽，仍需複核"}</strong>
            <code>{view.status.reason_code}</code>
          </div>
          {view.review_items.length > 0 && (
            <details>
              <summary>{view.review_items.length} 個關聯線索待複核</summary>
              <ul>
                {view.review_items.map((item) => (
                  <li key={item.id}>
                    <button type="button" onClick={() => {
                      setIsLocal(false);
                      setSelection({ kind: "review", item });
                    }}>{item.statement}</button>
                  </li>
                ))}
              </ul>
            </details>
          )}
          {view.limitations.map((limitation) => (
            <details key={limitation.reason_code}>
              <summary>{limitation.affected_page_count} 頁受限 · {limitation.reason_code}</summary>
              <p>{limitation.page_numbers.length ? `頁碼：${limitation.page_numbers.join("、")}` : "沒有可顯示的頁碼"}</p>
            </details>
          ))}
        </section>
      )}

      {view && (
        <nav className="path-rail" aria-label="初始學習路徑">
          <strong>INITIAL PATH</strong>
          {view.path.reason_code === "PREREQUISITE_CYCLE" ? (
            <span className="path-error" role="alert">路徑被 cycle 阻擋 · PREREQUISITE_CYCLE</span>
          ) : (
            <ol>
              {view.path.ordered_concept_ids.map((id, index) => {
                const concept = conceptById.get(id);
                return concept ? (
                  <li key={id}>
                    <button type="button" onClick={() => setSelection({ kind: "concept", item: concept })}>
                      <span>{String(index + 1).padStart(2, "0")}</span>{concept.label}
                    </button>
                  </li>
                ) : null;
              })}
            </ol>
          )}
        </nav>
      )}

      {selection && <Inspector selection={selection} onClose={() => {
        setIsLocal(false);
        setSelection(null);
      }} />}
    </main>
  );
}
