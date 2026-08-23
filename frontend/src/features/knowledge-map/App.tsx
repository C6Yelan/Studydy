import { useEffect, useState } from "react";

import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { KnowledgeMapView } from "../../api/contracts";
import { writeRoute, type AppRoute } from "../../app/routes";
import "./styles.css";


export default function KnowledgeMap({ apiClient, route }: {
  apiClient: StudydyApiClient;
  route: Extract<AppRoute, { name: "knowledge-map" }>;
}) {
  const [view, setView] = useState<KnowledgeMapView | null>(null);
  const [sourceArtifactId, setSourceArtifactId] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void Promise.all([
      apiClient.getKnowledgeMap({
        materialId: route.materialId,
        runId: route.runId,
        mapRevision: route.mapRevision,
      }),
      apiClient.getMaterialRun(route.runId),
    ]).then(
      ([map, run]) => {
        if (cancelled) return;
        if (run.material_id !== route.materialId) throw new Error("RUN_MATERIAL_MISMATCH");
        setView(map);
        setSourceArtifactId(run.source_artifact_id);
      },
      (error) => {
        if (!cancelled) setMessage(errorMessage(error));
      },
    );
    return () => { cancelled = true; };
  }, [apiClient, route.mapRevision, route.materialId, route.runId]);

  if (message) return (
    <section className="state-page failure-page" role="alert">
      <h1>無法讀取複核地圖</h1><p>{message}</p>
    </section>
  );
  if (!view) return (
    <section className="state-page" aria-live="polite">
      <div className="loading-ring" /><h1>正在讀取複核地圖</h1>
    </section>
  );

  const openSourcePdf = (pageNumber: number) => {
    if (!sourceArtifactId) return;
    window.open(apiClient.sourceArtifactUrl(sourceArtifactId, pageNumber), "_blank", "noopener,noreferrer");
  };

  return (
    <section className="knowledge-review-page">
      <header className="knowledge-review-header">
        <div>
          <p className="eyebrow">Knowledge Map v3 · review-only</p>
          <h1>教材概念與 Evidence 複核</h1>
          <p>Formal Concept、Relation 與初始學習路徑都保留待複核狀態。</p>
        </div>
        <button className="secondary-button" type="button" onClick={() => writeRoute({
          name: "material-run", materialId: route.materialId, runId: route.runId,
        })}>返回處理狀態</button>
      </header>

      <div className="review-summary surface">
        <div><strong>{view.concepts.length}</strong><span>個待複核概念</span></div>
        <div><strong>{view.excluded_pages.length}</strong><span>個排除頁面</span></div>
        <div><strong>{view.relations.length}</strong><span>條待複核 Relation</span></div>
      </div>

      {view.excluded_pages.length > 0 && (
        <section className="surface excluded-pages" aria-label="排除頁面">
          <h2>未納入概念地圖的頁面</h2>
          <ul>{view.excluded_pages.map((page) => (
            <li key={page.page_ref}>
              第 {page.page_number} 頁 · <code>{page.reason_codes.join(", ")}</code>
            </li>
          ))}</ul>
        </section>
      )}

      <div className="concept-review-grid">
        {view.concepts.map((concept) => (
          <article className="surface concept-review-card" key={concept.formal_concept_id}>
            <span className="outcome-badge is-review">待複核</span>
            <h2>{concept.label}</h2>
            {concept.claims.map((claim) => (
              <section key={claim.claim_id}>
                <p>{claim.text}</p>
                <h3>PDF Evidence locator</h3>
                <ul className="evidence-list">{claim.evidence.map((evidence) => (
                  <li key={evidence.evidence_id}>
                    <strong>第 {evidence.page_number} 頁 · {evidence.kind}</strong>
                    <code>[{evidence.region.bbox.join(", ")}] · {evidence.region.coordinate_space}</code>
                  </li>
                ))}</ul>
                <button
                  className="evidence-open"
                  type="button"
                  onClick={() => openSourcePdf(claim.evidence[0].page_number)}
                >開啟來源 PDF 第 {claim.evidence[0].page_number} 頁</button>
              </section>
            ))}
            <code className="reason-code">{concept.reason_codes.join(" · ")}</code>
          </article>
        ))}
      </div>
    </section>
  );
}
