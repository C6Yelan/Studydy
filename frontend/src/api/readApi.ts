import type {
  ConceptDetailResponse,
  KnowledgeMapResponse,
  LearningPathResponse,
  MaterialSummary,
} from "./types";

async function requestJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {
    headers: {
      Accept: "application/json",
    },
    signal,
  });

  if (!response.ok) {
    throw new Error(`Request failed: ${response.status} ${response.statusText}`);
  }

  return response.json() as Promise<T>;
}

export function getMaterial(materialId: number, signal?: AbortSignal) {
  return requestJson<MaterialSummary>(`/api/materials/${materialId}`, signal);
}

export function getKnowledgeMap(materialId: number, signal?: AbortSignal) {
  return requestJson<KnowledgeMapResponse>(
    `/api/materials/${materialId}/knowledge-map`,
    signal,
  );
}

export function getConceptDetail(conceptId: number, signal?: AbortSignal) {
  return requestJson<ConceptDetailResponse>(`/api/concepts/${conceptId}`, signal);
}

export function getLearningPath(materialId: number, signal?: AbortSignal) {
  return requestJson<LearningPathResponse>(
    `/api/materials/${materialId}/learning-path`,
    signal,
  );
}
