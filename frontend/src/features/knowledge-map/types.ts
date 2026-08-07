export type Evidence = {
  evidence_id: string;
  page_number: number;
  element_id: string;
  region: { coordinate_space: string; bbox: number[] };
};

export type Concept = {
  id: string;
  label: string;
  definition: string;
  members: { name: string; definition: string; page_number: number }[];
  evidence: Evidence[];
  position: { x: number; y: number };
  quality: "accepted" | "needs_review";
  reason_code: string;
};

export type Relation = {
  id: string;
  type: "prerequisite" | "contains" | "similar" | "confusing" | "application" | "example";
  source: string;
  target: string;
  statement: string;
  evidence: Evidence[];
  reason_code: string;
};

export type ReviewItem = {
  id: string;
  kind: string;
  source: string;
  target: string;
  statement: string;
  evidence: Evidence[];
  reason_code: string;
};

export type KnowledgeMapView = {
  schema: "knowledge-map-view/v1";
  material_ref: string;
  knowledge_map_revision: string;
  learning_path_revision: string;
  status: {
    processing: string;
    quality: string;
    decision: string;
    reason_code: string;
  };
  concepts: Concept[];
  relations: Relation[];
  review_items: ReviewItem[];
  path: {
    ordered_concept_ids: string[];
    processing: string;
    quality: string;
    decision: string;
    reason_code: string;
  };
  limitations: {
    reason_code: string;
    page_numbers: number[];
    affected_page_count: number;
  }[];
};
