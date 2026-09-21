import { SourceButton, sourceLinks } from "./SourceButton";
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
  const references = sourceLinks(claims.flatMap(claim => claim.evidence), sourceResolver);
  const excerpts = new Map<string, { page: number; sourceName?: string; quote: string; points: Set<number> }>();
  claims.forEach((claim, index) => claim.evidence.forEach((item) => {
    const key = `${item.evidence_id}\0${item.quote}`;
    if (!excerpts.has(key)) excerpts.set(key, { page: item.normalized_page ?? item.page, sourceName: item.source_name, quote: item.quote, points: new Set() });
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
      <div className="claim-sources">{references.map((evidence) => <SourceButton key={evidence.evidence_id} apiClient={apiClient} artifactId={sourceArtifactId} page={evidence.page} resolver={sourceResolver} evidenceId={evidence.evidence_id} evidence={evidence}>
        原始教材第 {evidence.page} 頁<Icon name="chevron-right" size={16} />
      </SourceButton>)}</div>
      <details className="source-excerpts"><summary>對照教材原文</summary>
        {[...excerpts].map(([key, item]) => <blockquote key={key}>
          <small>{claims.length > 1 ? `重點 ${[...item.points].join("、")} · ` : ""}{item.sourceName && `${item.sourceName} · PDF `}第 {item.page} 頁</small>
          <p className="claim-text">{item.quote}</p>
        </blockquote>)}
      </details>
    </section>
  </>;
}
