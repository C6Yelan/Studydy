import { SourceButton, sourceLinks } from "./SourceButton";
import type { StudydyApiClient } from "../api/client";
import type { KnowledgeStructureView } from "../api/contracts";
import { claimText } from "./claim-text";

type Claim = KnowledgeStructureView["concepts"][number]["claims"][number];

export function ConceptContent({
  claims,
  apiClient,
  sourceResolver,
}: {
  claims: Claim[];
  apiClient: StudydyApiClient;
  sourceResolver: string;
}) {
  const references = sourceLinks(claims.flatMap((claim) => claim.evidence));
  return (
    <>
      {claims.map((claim, index) => {
        const text = claimText(claim);
        const hasCode =
          claim.evidence.some((item) => item.kind === "code") ||
          /^\s*(?:\/\/|(?:const\s+)?(?:int|float|double|char|bool|void)\s+[A-Za-z_]|(?:for|if|while)\s*\()/m.test(
            text,
          );
        return (
          <section
            className="concept-claim"
            key={claim.claim_id}
            aria-label={`教材重點 ${index + 1}`}
          >
            {claims.length > 1 && <strong className="claim-number">重點 {index + 1}</strong>}
            <p className={`claim-text${hasCode ? " is-code" : ""}`}>{text}</p>
          </section>
        );
      })}
      <section className="concept-sources" aria-label="教材來源">
        <h3>教材來源</h3>
        <div className="claim-sources">
          {references.map((evidence) => (
            <SourceButton
              key={evidence.evidence_id}
              apiClient={apiClient}
              resolver={sourceResolver}
              evidence={evidence}
            />
          ))}
        </div>
      </section>
    </>
  );
}
