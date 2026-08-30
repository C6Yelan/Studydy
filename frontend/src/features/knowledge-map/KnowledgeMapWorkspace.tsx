import { useMemo, useState } from "react";

import type { StudydyApiClient } from "../../api/client";
import type { KnowledgeMapView } from "../../api/contracts";
import { Icon } from "../../ui/Icon";
import { StateView } from "../../ui/StateView";
import {
  focusNeighborhood,
  learningPathReason,
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

function RelationConnector({ relation }: { relation: Pick<Relation, "type"> }) {
  const presentation = relationPresentation(relation.type);
  return (
    <span
      aria-label={`${presentation.label}，${presentation.directional ? "有方向" : "雙向"}`}
      className={`relation-connector ${presentation.className}`}
      data-directional={String(presentation.directional)}
    >
      <i aria-hidden="true" />
      <span>{presentation.label}</span>
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
        <p>{presentation.explanation}</p>
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
          onClick={() => onStartStudy(view.initial_learning_path[0])}
        ><Icon name="learning" />{isStartingStudy ? "正在開始…" : "從第一步開始"}</button>
      </div>
      <ol className="learning-path">
        {view.initial_learning_path.map((id, index) => {
          const concept = view.concepts.find((item) => item.formal_concept_id === id)!;
          return (
            <li key={id}>
              <button type="button" onClick={() => openConcept(id)}>
                <span>{index + 1}</span>
                <div><strong>{concept.label}</strong><small>{learningPathReason(view, id)}</small></div>
                <Icon name="chevron-right" />
              </button>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function FocusView({ openConcept, openRelation, selectedConceptId, setSelectedConceptId, view }: {
  openConcept: (id: string) => void;
  openRelation: (id: string) => void;
  selectedConceptId: string;
  setSelectedConceptId: (id: string) => void;
  view: KnowledgeMapView;
}) {
  const selected = view.concepts.find((concept) => concept.formal_concept_id === selectedConceptId) ?? view.concepts[0];
  const neighborhood = focusNeighborhood(view, selected.formal_concept_id);
  const positions = new Map(neighborhood.nodes.map((node) => [node.conceptId, node]));
  const fallbackConcepts = view.initial_learning_path
    .filter((conceptId) => conceptId !== selected.formal_concept_id)
    .slice(0, 3);
  return (
    <section aria-labelledby="focus-title">
      <div className="view-heading">
        <div><h2 id="focus-title">概念地圖</h2><p>從目前概念查看直接相連的知識結構；箭頭由來源指向目標。</p></div>
        <label className="concept-select">焦點概念
          <select value={selected.formal_concept_id} onChange={(event) => setSelectedConceptId(event.currentTarget.value)}>
            {view.concepts.map((concept) => <option key={concept.formal_concept_id} value={concept.formal_concept_id}>{concept.label}</option>)}
          </select>
        </label>
      </div>
      <div className="relation-legend" aria-label="概念連結圖例">
        {(["prerequisite", "contains", "related"] as const).map((type) => {
          return <RelationConnector key={type} relation={{ type }} />;
        })}
      </div>
      <div className="focus-graph" aria-label={`「${selected.label}」的概念連結圖`}>
        <svg aria-label="可選擇的概念連結" preserveAspectRatio="none" viewBox="0 0 100 100">
          <defs>
            <marker id="focus-arrow" markerHeight="5" markerWidth="5" orient="auto" refX="4" refY="2.5">
              <path d="M0,0 L5,2.5 L0,5 Z" />
            </marker>
          </defs>
          {neighborhood.relations.map((relation) => {
            const source = positions.get(relation.source_formal_concept_id)!;
            const target = positions.get(relation.target_formal_concept_id)!;
            const presentation = relationPresentation(relation.type);
            const relationLabel = `${conceptLabel(view.concepts, relation.source_formal_concept_id)}，${presentation.label}，${conceptLabel(view.concepts, relation.target_formal_concept_id)}`;
            return (
              <g
                aria-label={relationLabel}
                className={`focus-edge ${presentation.className}`}
                key={relation.relation_id}
                role="button"
                tabIndex={0}
                onClick={() => openRelation(relation.relation_id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    openRelation(relation.relation_id);
                  }
                }}
              >
                <line className="focus-edge-hit" x1={source.x} x2={target.x} y1={source.y} y2={target.y} />
                <line
                  markerEnd={presentation.directional ? "url(#focus-arrow)" : undefined}
                  x1={source.x}
                  x2={target.x}
                  y1={source.y}
                  y2={target.y}
                />
                <text x={(source.x + target.x) / 2} y={(source.y + target.y) / 2}>{presentation.label}</text>
              </g>
            );
          })}
        </svg>
        {neighborhood.nodes.map((node) => {
          const concept = view.concepts.find((item) => item.formal_concept_id === node.conceptId)!;
          const isSelected = node.conceptId === selected.formal_concept_id;
          return (
            <button
              aria-label={`${isSelected ? "目前焦點" : "相連概念"}：${concept.label}`}
              className={`focus-node${isSelected ? " is-selected" : ""}`}
              key={node.conceptId}
              style={{ left: `${node.x}%`, top: `${node.y}%` }}
              type="button"
              onClick={() => openConcept(node.conceptId)}
            >
              <small>{isSelected ? "目前焦點" : "相連概念"}</small>
              <strong>{concept.label}</strong>
            </button>
          );
        })}
        {neighborhood.relations.length === 0 && (
          <div className="focus-fallback">
            <strong>目前沒有已發布的直接概念連結</strong>
            <p>我們不會用頁面相鄰假造關係。你仍可查看教材重點，或依建議順序繼續。</p>
            <div>
              {fallbackConcepts.map((conceptId) => (
                <button className="text-button" key={conceptId} type="button" onClick={() => setSelectedConceptId(conceptId)}>
                  查看「{conceptLabel(view.concepts, conceptId)}」
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
      {neighborhood.relations.length > 0 && (
        <details className="relation-alternate">
          <summary>以清單查看這些概念連結</summary>
          <div className="relation-list">
            {neighborhood.relations.map((relation) => (
              <button key={relation.relation_id} type="button" onClick={() => openRelation(relation.relation_id)}>
                <span className={relation.source_formal_concept_id === selected.formal_concept_id ? "is-selected" : ""}>{conceptLabel(view.concepts, relation.source_formal_concept_id)}</span>
                <RelationConnector relation={relation} />
                <span className={relation.target_formal_concept_id === selected.formal_concept_id ? "is-selected" : ""}>{conceptLabel(view.concepts, relation.target_formal_concept_id)}</span>
              </button>
            ))}
          </div>
        </details>
      )}
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
  const initialConceptId = view.initial_learning_path[0] ?? view.concepts[0]?.formal_concept_id ?? "";
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
