export interface MaterialSummary {
  id: number;
  title: string;
  subject: string;
  chapter_range: string;
  file_name: string | null;
  upload_status: string;
  processing_status: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface ConceptScore {
  score_value: number | null;
  score_level: string | null;
  decision: string;
  score_detail: Record<string, unknown> | null;
  score_reason: string | null;
}

export interface ConceptSummary {
  id: number;
  name: string;
  summary: string | null;
  keywords: string[];
  difficulty_level: string | null;
  importance_level: string | null;
  status: string;
  score: ConceptScore;
  needs_review: boolean;
  review_reason: string | null;
  scope_note: string | null;
}

export interface KnowledgeMapNode {
  id: string;
  type: string;
  position: {
    x: number;
    y: number;
  };
  data: {
    label: string;
    summary: string | null;
    difficulty_level: string | null;
    importance_level: string | null;
    needs_review: boolean;
    score_value: number | null;
  };
}

export interface KnowledgeMapEdge {
  id: string;
  source: string;
  target: string;
  type: string;
  label: string;
  data: {
    relation_type: string;
    reason: string | null;
    score_value: number | null;
    needs_review: boolean;
  };
}

export interface KnowledgeMapResponse {
  nodes: KnowledgeMapNode[];
  edges: KnowledgeMapEdge[];
  warnings: string[];
}

export interface EvidenceSummary {
  id: number;
  material_id: number;
  block_id: number | null;
  page_number: number | null;
  quote_text: string | null;
  evidence_type: string;
  metadata: Record<string, unknown> | null;
}

export interface RelationSummary {
  id: number;
  source_concept_id: number;
  target_concept_id: number;
  relation_type: string;
  reason: string | null;
  score: ConceptScore;
  needs_review: boolean;
}

export interface ConceptDetailResponse {
  concept: ConceptSummary;
  evidence_list: EvidenceSummary[];
  resource_list: unknown[];
  incoming_relations: RelationSummary[];
  outgoing_relations: RelationSummary[];
  learning_path_position: number | null;
  mastery_status: string;
  warnings: string[];
}

export interface LearningPathNode {
  order_index: number;
  concept_id: number;
  concept_name: string;
  reason: string | null;
  is_required: boolean;
}

export interface LearningPathResponse {
  id: number | null;
  path_type: string;
  status: string;
  nodes: LearningPathNode[];
  needs_review: boolean;
  review_reason: string | null;
}
