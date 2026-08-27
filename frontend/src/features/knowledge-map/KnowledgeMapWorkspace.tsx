import { useMemo, useState } from "react";

import type { StudydyApiClient } from "../../api/client";
import type { KnowledgeMapView } from "../../api/contracts";
import { Icon } from "../../ui/Icon";
import { StateView } from "../../ui/StateView";
import { relationPresentation, safeExternalUrl } from "./knowledge-map";

type Concept = KnowledgeMapView["concepts"][number];
type Relation = KnowledgeMapView["relations"][number];
type Mode = "overview" | "path" | "focus" | "review";
type Detail = { kind: "concept"; id: string } | { kind: "relation"; id: string };

const modes: { id: Mode; label: string }[] = [
  { id: "overview", label: "總覽" },
  { id: "path", label: "教材順序" },
  { id: "focus", label: "焦點探索" },
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

function ConceptDetail({ apiClient, concept, close, sourceArtifactId }: {
  apiClient: StudydyApiClient;
  concept: Concept;
  close: () => void;
  sourceArtifactId: string;
}) {
  return (
    <aside className="detail-panel" aria-label="概念詳情">
      <header>
        <div><span className="detail-kicker">Concept</span><h2>{concept.label}</h2></div>
        <button aria-label="關閉概念詳情" className="panel-close" type="button" onClick={close}>×</button>
      </header>
      <span className="status-badge is-review">內容待複核</span>
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
        <div><span className="detail-kicker">Relation</span><h2>{presentation.label}</h2></div>
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
      <div className="view-heading"><div><h2 id="overview-title">概念總覽</h2><p>從教材正式發布的概念開始探索。</p></div></div>
      <div className="concept-grid">
        {view.concepts.map((concept, index) => (
          <button
            className={`concept-card accent-${index % 4}`}
            key={concept.formal_concept_id}
            type="button"
            onClick={() => openConcept(concept.formal_concept_id)}
          >
            <span className="concept-card__icon"><Icon name="book" /></span>
            <span className="status-badge is-review">內容待複核</span>
            <strong>{concept.label}</strong>
            <span>{concept.claims[0]?.text}</span>
            <small>{concept.claims.length} 個教材重點 · 第 {concept.source_page_numbers.join("、")} 頁</small>
          </button>
        ))}
      </div>
    </section>
  );
}

function PathView({ openConcept, view }: {
  openConcept: (id: string) => void;
  view: KnowledgeMapView;
}) {
  return (
    <section aria-labelledby="path-title">
      <div className="view-heading">
        <div><h2 id="path-title">教材建議學習順序</h2><p>這是 Knowledge Map 的固定起始順序，不會因本次作答而改寫。</p></div>
      </div>
      <ol className="learning-path">
        {view.initial_learning_path.map((id, index) => {
          const concept = view.concepts.find((item) => item.formal_concept_id === id)!;
          return (
            <li key={id}>
              <button type="button" onClick={() => openConcept(id)}>
                <span>{index + 1}</span>
                <div><strong>{concept.label}</strong><small>{concept.claims.length} 個教材重點 · 第 {concept.source_page_numbers.join("、")} 頁</small></div>
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
  const adjacentRelations = view.relations.filter((relation) =>
    relation.source_formal_concept_id === selected.formal_concept_id
    || relation.target_formal_concept_id === selected.formal_concept_id);
  return (
    <section aria-labelledby="focus-title">
      <div className="view-heading">
        <div><h2 id="focus-title">焦點探索</h2><p>選擇概念或正式 Relation，查看教材依據。</p></div>
        <label className="concept-select">焦點概念
          <select value={selected.formal_concept_id} onChange={(event) => setSelectedConceptId(event.currentTarget.value)}>
            {view.concepts.map((concept) => <option key={concept.formal_concept_id} value={concept.formal_concept_id}>{concept.label}</option>)}
          </select>
        </label>
      </div>
      <div className="focus-center">
        <button type="button" onClick={() => openConcept(selected.formal_concept_id)}>
          <span>目前焦點</span><strong>{selected.label}</strong><small>{selected.claims[0]?.text}</small>
        </button>
      </div>
      <div className="relation-legend" aria-label="Relation 圖例">
        {(["prerequisite", "contains", "related"] as const).map((type) => {
          return <RelationConnector key={type} relation={{ type }} />;
        })}
      </div>
      {adjacentRelations.length === 0 ? (
        <div className="surface compact-empty"><Icon name="map" /><div><strong>目前沒有已發布的 Relation</strong><p>這個概念仍可從教材重點與 Evidence 開始閱讀。</p></div></div>
      ) : (
        <div className="relation-list">
          {adjacentRelations.map((relation) => (
            <button key={relation.relation_id} type="button" onClick={() => openRelation(relation.relation_id)}>
              <span className={relation.source_formal_concept_id === selected.formal_concept_id ? "is-selected" : ""}>{conceptLabel(view.concepts, relation.source_formal_concept_id)}</span>
              <RelationConnector relation={relation} />
              <span className={relation.target_formal_concept_id === selected.formal_concept_id ? "is-selected" : ""}>{conceptLabel(view.concepts, relation.target_formal_concept_id)}</span>
            </button>
          ))}
        </div>
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
      <div className="view-heading"><div><h2 id="review-title">教材內容複核</h2><p>這裡是 Map 品質狀態，不代表你的學習弱點。</p></div></div>
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

export function KnowledgeMapWorkspace({ apiClient, sourceArtifactId, view }: {
  apiClient: StudydyApiClient;
  sourceArtifactId: string;
  view: KnowledgeMapView;
}) {
  const initialConceptId = view.initial_learning_path[0] ?? view.concepts[0]?.formal_concept_id ?? "";
  const [mode, setMode] = useState<Mode>("overview");
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
  return (
    <section className={`map-workspace${detail ? " has-detail" : ""}`}>
      <header className="map-header">
        <div><p className="eyebrow">Knowledge Map</p><h1>知識地圖</h1><p>探索教材概念、正式 Relation、Evidence 與建議順序。</p></div>
        <div className="map-facts" aria-label="地圖摘要">
          <span><strong>{view.concepts.length}</strong>概念</span>
          <span><strong>{view.relations.length}</strong>Relation</span>
          <span><strong>{view.resource_diagnostics.promoted_resources}</strong>資源</span>
        </div>
      </header>

      {view.status.processing === "partial" && (
        <div className="partial-banner" role="status"><Icon name="warning" /><span>部分教材內容未安全納入；目前畫面只顯示已發布內容。</span></div>
      )}

      <div className="map-tabs" role="tablist" aria-label="知識地圖檢視">
        {modes.map((item) => (
          <button
            aria-selected={mode === item.id}
            className={mode === item.id ? "is-active" : undefined}
            key={item.id}
            role="tab"
            type="button"
            onClick={() => {
              setMode(item.id);
              setDetail(null);
            }}
          >{item.label}</button>
        ))}
      </div>

      <div className="map-content">
        <div className="map-view" role="tabpanel">
          {mode === "overview" && <Overview openConcept={openConcept} view={view} />}
          {mode === "path" && <PathView openConcept={openConcept} view={view} />}
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
          <ConceptDetail apiClient={apiClient} close={() => setDetail(null)} concept={selectedConcept} sourceArtifactId={sourceArtifactId} />
        )}
        {selectedRelation && (
          <RelationDetail apiClient={apiClient} close={() => setDetail(null)} relation={selectedRelation} sourceArtifactId={sourceArtifactId} view={view} />
        )}
      </div>
    </section>
  );
}
