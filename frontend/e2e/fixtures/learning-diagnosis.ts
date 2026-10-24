import type { ConceptDifficultyView, LearningDiagnosisView } from "../../src/api/contracts";

export function difficultyView(materialId: string, revision: string): ConceptDifficultyView {
  return { schema: "concept-difficulty/v1", material_id: materialId, knowledge_structure_revision: revision,
    status: "ready", mode: "model", concepts: [], failure_reason: null };
}

export function diagnosisView(materialId: string, revision: string, setId: string | null = null): LearningDiagnosisView {
  return { schema: "learning-diagnosis/v1", material_id: materialId, knowledge_structure_revision: revision,
    source_resolver: `/v1/materials/${materialId}/knowledge-structures/${revision}/evidence`,
    scope: setId ? "set" : "material", set_id: setId, status: "no_answers", mode: "template", event_watermark: 0,
    strengths: [], weaknesses: [], recommendations: [], failure_reason: null };
}
