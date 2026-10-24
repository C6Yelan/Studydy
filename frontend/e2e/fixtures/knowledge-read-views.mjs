// 同一組合成教材可產生完整 API 與按需 API 的 fixture；不發出額外網路請求。
export function outlineView(view, materialId, runId) {
  return { schema: 'knowledge-outline/v1', material_id: materialId, run_id: runId,
    knowledge_structure_revision: view.knowledge_structure_revision,
    source_resolver: decodeURIComponent(view.source_resolver), status: view.status,
    concepts: view.concepts.map(c => ({ concept_id: c.concept_id, label: c.label, aliases: c.aliases,
      claim_count: c.claims.length })),
    relations: view.relations.map(r => ({ relation_id:r.relation_id, source_concept_id:r.source_concept_id,
      target_concept_id:r.target_concept_id, type:r.type })), section_count: view.document_tree.sections.length,
    initial_learning_path: view.initial_learning_path, excluded_pages: view.excluded_pages };
}

export function conceptsView(view, materialId, ids, neighbors = false) {
  const relations = view.relations.filter(r => ids.includes(r.source_concept_id) || ids.includes(r.target_concept_id));
  const selected = new Set(ids);
  if (neighbors) for (const r of relations) { selected.add(r.source_concept_id); selected.add(r.target_concept_id); }
  const refs = new Set(relations.flatMap(r => r.evidence_refs));
  const evidence = new Map(view.concepts.flatMap(c => c.claims.flatMap(c => c.evidence)).map(e => [e.evidence_id, e]));
  return { schema: 'knowledge-concepts/v1', material_id: materialId,
    knowledge_structure_revision: view.knowledge_structure_revision, requested_concept_ids: ids,
    source_resolver: decodeURIComponent(view.source_resolver), status: view.status,
    concepts: view.concepts.filter(c => selected.has(c.concept_id)), relations,
    relation_evidence: [...evidence.values()].filter(e => refs.has(e.evidence_id)) };
}

export function mapProgressView(progress) {
  return { schema: 'map-progress/v1', study_session_id: progress.study_session_id,
    starting_level: progress.starting_level, learning_path: progress.learning_path,
    knowledge_structure_revision: progress.knowledge_structure_revision, current_concept_id: progress.current_concept_id,
    assessment_cycles: progress.assessment_cycles, next_action: progress.next_action,
    concept_states: progress.concept_states.map(s => ({ concept_id: s.concept_id, status: s.status,
      attempts: s.attempts, assessable: s.assessable_claim_ids?.length !== 0 })) };
}

export function workspaceView(resume) {
  const ids = [...new Set([resume.selected_set?.target_concept_id, resume.session.current_concept_id,
    resume.progress.next_action.target_concept_id, ...(resume.selected_set?.kind === "material_diagnostic" ? resume.selected_set.items.map(item => item.target_concept_id) : [])].filter(Boolean))];
  const { knowledge_structure: view, progress, ...rest } = resume;
  const navigation = new Set([...ids, ...resume.assessment_sets.map(s => s.target_concept_id),
    ...progress.assessment_cycles.map(c => c.concept_id), ...progress.next_action.prerequisite_concept_ids]);
  const overview = outlineView(view, resume.session.material_id, resume.run_id);
  const outline = { schema: 'study-navigation/v1', material_id: overview.material_id,
    knowledge_structure_revision: overview.knowledge_structure_revision, run_id: overview.run_id,
    source_resolver: overview.source_resolver, status: overview.status, concept_count: view.concepts.length,
    concepts: view.concepts.filter(c => navigation.has(c.concept_id)).map(c => ({concept_id:c.concept_id,label:c.label})),
    initial_learning_path: view.initial_learning_path.filter(s => navigation.has(s.concept_id)) };
  return { ...rest, schema: 'study-workspace/v1', outline, starting_level: progress.starting_level,
    context: ids.length ? conceptsView(view, resume.session.material_id, ids) : null,
    progress: { ...progress, schema: 'study-progress/v1', concept_count: view.concepts.length,
      concept_states: progress.concept_states.filter(s => ids.includes(s.concept_id)) } };
}

export function readResponse(url, body, runId) {
  const path = new URL(url).pathname;
  if (path.endsWith('/outline') && body?.schema === 'knowledge-structure-view/v1')
    return outlineView(body, path.split('/')[3], runId);
  if (path.endsWith('/map-progress') && ['learner-progress/v1', 'study-progress/v1'].includes(body?.schema))
    return mapProgressView(body);
  if (path.endsWith('/workspace') && body?.schema === 'study-resume/v1') return workspaceView(body);
  return body;
}
