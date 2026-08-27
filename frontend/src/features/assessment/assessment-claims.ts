export type AssessmentClaim = {
  claim_id: string;
  text: string;
  evidence: unknown[];
};

export function assessmentFallbackClaim(
  claims: AssessmentClaim[],
  currentClaimId: string,
  attemptedClaimIds: string[] = [],
): AssessmentClaim | null {
  const attempted = new Set(attemptedClaimIds);
  return [...claims]
    .filter((claim) => claim.claim_id !== currentClaimId)
    .sort((left, right) =>
      Number(attempted.has(left.claim_id)) - Number(attempted.has(right.claim_id))
      || left.evidence.length - right.evidence.length
      || left.text.length - right.text.length
      || left.claim_id.localeCompare(right.claim_id))[0] ?? null;
}
