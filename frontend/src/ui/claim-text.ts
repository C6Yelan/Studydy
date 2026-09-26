import type { KnowledgeStructureView } from "../api/contracts";

type Claim = KnowledgeStructureView["concepts"][number]["claims"][number];

// Restore only source whitespace, never infer code or add source content to a claim.
export function claimText(claim: Claim): string {
  if (claim.text.includes("\n")) return claim.text;
  const normalize = (text: string) => text.replace(/\s+/g, " ").trim();
  const quotes = [...new Set(claim.evidence.map((item) => item.quote))];
  const candidates = [...quotes, quotes.join("\n")];
  return (
    candidates.find((text) => text.includes("\n") && normalize(text) === normalize(claim.text)) ??
    claim.text
  );
}
