import assert from "node:assert/strict";
import test from "node:test";

import { assessmentFallbackClaim } from "./assessment-claims.ts";

test("no-safe fallback prefers a different Claim with smaller Evidence scope", () => {
  const claims = [
    { claim_id: "definition", text: "Long definition", evidence: [1, 2, 3] },
    { claim_id: "long", text: "A longer grounded point", evidence: [1] },
    { claim_id: "short", text: "Short point", evidence: [1] },
  ];

  assert.equal(
    assessmentFallbackClaim(claims, "definition")?.claim_id,
    "short",
  );
  assert.equal(assessmentFallbackClaim([claims[0]], "definition"), null);
});
