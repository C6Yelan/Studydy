import { SourceButton } from "./SourceButton";
import type { StudydyApiClient } from "../api/client";
import type { KnowledgeStructureView } from "../api/contracts";
import { Icon } from "./Icon";
import { claimText } from "./claim-text";

type Claim = KnowledgeStructureView["concepts"][number]["claims"][number];

export function ConceptContent({ claims, apiClient, sourceArtifactId, sourceResolver }: {
  claims: Claim[];
  apiClient: StudydyApiClient;
  sourceArtifactId: string;
  sourceResolver?: string;
}) {
  const pages = [...new Set(claims.flatMap((claim) => claim.evidence.map((item) => item.page)))].sort((a, b) => a - b);
  const excerpts = new Map<string, { page: number; quote: string; points: Set<number> }>();
  claims.forEach((claim, index) => claim.evidence.forEach((item) => {
    const key = `${item.page}\0${item.quote}`;
    if (!excerpts.has(key)) excerpts.set(key, { page: item.page, quote: item.quote, points: new Set() });
    excerpts.get(key)!.points.add(index + 1);
  }));
  return <>
    {claims.map((claim, index) => {
      const text = claimText(claim);
      const hasCode = claim.evidence.some((item) => item.kind === "code")
        || /^\s*(?:\/\/|(?:const\s+)?(?:int|float|double|char|bool|void)\s+[A-Za-z_]|(?:for|if|while)\s*\()/m.test(text);
      return <section className="concept-claim" key={claim.claim_id} aria-label={`教材重點 ${index + 1}`}>
        {claims.length > 1 && <strong className="claim-number">重點 {index + 1}</strong>}
        <p className={`claim-text${hasCode ? " is-code" : ""}`}>{text}</p>
      </section>;
    })}
    <section className="concept-sources" aria-label="教材來源">
      <div className="claim-sources">{pages.map((page) => <SourceButton key={page} apiClient={apiClient} artifactId={sourceArtifactId} page={page} resolver={sourceResolver} evidenceId={claims.flatMap(c=>c.evidence).find(e=>e.page===page)?.evidence_id}>
        原始教材第 {page} 頁<Icon name="chevron-right" size={16} />
      </SourceButton>)}</div>
      <details className="source-excerpts"><summary>對照教材原文</summary>
        {[...excerpts].map(([key, item]) => <blockquote key={key}>
          <small>{claims.length > 1 ? `重點 ${[...item.points].join("、")} · ` : ""}第 {item.page} 頁</small>
          <p className="claim-text">{item.quote}</p>
        </blockquote>)}
      </details>
    </section>
  </>;
}
