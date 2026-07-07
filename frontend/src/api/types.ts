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
