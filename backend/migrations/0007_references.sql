-- 所有表與唯一鍵建立後補齊跨領域外鍵，保留循環參照的 deferred 語意。

ALTER TABLE ONLY public.answer_events
    ADD CONSTRAINT answer_events_assessment_revision_fkey FOREIGN KEY (assessment_revision) REFERENCES public.assessments(assessment_revision);

ALTER TABLE ONLY public.answer_events
    ADD CONSTRAINT answer_events_study_session_id_knowledge_structure_revisio_fkey FOREIGN KEY (study_session_id, knowledge_structure_revision) REFERENCES public.study_sessions(study_session_id, knowledge_structure_revision);

ALTER TABLE ONLY public.artifacts
    ADD CONSTRAINT artifacts_learner_id_material_id_fkey FOREIGN KEY (learner_id, material_id) REFERENCES public.materials(learner_id, material_id);

ALTER TABLE ONLY public.assessment_set_items
    ADD CONSTRAINT assessment_set_items_assessment_revision_study_session_id__fkey FOREIGN KEY (assessment_revision, study_session_id, knowledge_structure_revision, target_concept_id, target_claim_id) REFERENCES public.assessments(assessment_revision, study_session_id, knowledge_structure_revision, target_concept_id, target_claim_id);

ALTER TABLE ONLY public.assessment_set_items
    ADD CONSTRAINT assessment_set_items_set_id_study_session_id_knowledge_str_fkey FOREIGN KEY (set_id, study_session_id, knowledge_structure_revision, target_concept_id) REFERENCES public.assessment_sets(set_id, study_session_id, knowledge_structure_revision, target_concept_id);

ALTER TABLE ONLY public.assessment_sets
    ADD CONSTRAINT assessment_sets_learner_id_material_id_study_session_id_kn_fkey FOREIGN KEY (learner_id, material_id, study_session_id, knowledge_structure_revision) REFERENCES public.study_sessions(learner_id, material_id, study_session_id, knowledge_structure_revision);

ALTER TABLE ONLY public.assessments
    ADD CONSTRAINT assessments_study_session_id_knowledge_structure_revision_fkey FOREIGN KEY (study_session_id, knowledge_structure_revision) REFERENCES public.study_sessions(study_session_id, knowledge_structure_revision);

ALTER TABLE ONLY public.card_sets
    ADD CONSTRAINT card_sets_learner_id_material_id_fkey FOREIGN KEY (learner_id, material_id) REFERENCES public.materials(learner_id, material_id);

ALTER TABLE ONLY public.card_sets
    ADD CONSTRAINT card_sets_learner_id_material_id_knowledge_structure_revis_fkey FOREIGN KEY (learner_id, material_id, knowledge_structure_revision) REFERENCES public.knowledge_structures(learner_id, material_id, structure_revision);

ALTER TABLE ONLY public.card_source_concepts
    ADD CONSTRAINT card_source_concepts_learner_id_material_id_structure_revi_fkey FOREIGN KEY (learner_id, material_id, structure_revision) REFERENCES public.card_source_indexes(learner_id, material_id, structure_revision) ON DELETE CASCADE;

ALTER TABLE ONLY public.card_source_indexes
    ADD CONSTRAINT card_source_indexes_learner_id_material_id_structure_revis_fkey FOREIGN KEY (learner_id, material_id, structure_revision) REFERENCES public.knowledge_structures(learner_id, material_id, structure_revision) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_evidence_refs
    ADD CONSTRAINT knowledge_evidence_refs_learner_id_material_id_structure_r_fkey FOREIGN KEY (learner_id, material_id, structure_revision) REFERENCES public.knowledge_read_indexes(learner_id, material_id, structure_revision) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_read_indexes
    ADD CONSTRAINT knowledge_read_indexes_learner_id_material_id_structure_re_fkey FOREIGN KEY (learner_id, material_id, structure_revision) REFERENCES public.knowledge_structures(learner_id, material_id, structure_revision) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_structures
    ADD CONSTRAINT knowledge_structures_learner_id_material_id_run_id_fkey FOREIGN KEY (learner_id, material_id, run_id) REFERENCES public.material_processing_runs(learner_id, material_id, run_id);

ALTER TABLE ONLY public.learner_sessions
    ADD CONSTRAINT learner_sessions_learner_id_fkey FOREIGN KEY (learner_id) REFERENCES public.learners(learner_id);

ALTER TABLE ONLY public.materials
    ADD CONSTRAINT material_head_fk FOREIGN KEY (learner_id, material_id, head_revision) REFERENCES public.knowledge_structures(learner_id, material_id, structure_revision) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE ONLY public.material_passages
    ADD CONSTRAINT material_passages_learner_id_material_id_structure_revisio_fkey FOREIGN KEY (learner_id, material_id, structure_revision) REFERENCES public.material_retrieval_indexes(learner_id, material_id, structure_revision) ON DELETE CASCADE;

ALTER TABLE ONLY public.material_processing_runs
    ADD CONSTRAINT material_processing_runs_learner_id_material_id_source_art_fkey FOREIGN KEY (learner_id, material_id, source_artifact_id) REFERENCES public.artifacts(learner_id, material_id, artifact_id);

ALTER TABLE ONLY public.material_research_documents
    ADD CONSTRAINT material_research_documents_research_id_fkey FOREIGN KEY (research_id) REFERENCES public.material_research(research_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.material_research
    ADD CONSTRAINT material_research_learner_id_fkey FOREIGN KEY (learner_id) REFERENCES public.learners(learner_id);

ALTER TABLE ONLY public.material_research
    ADD CONSTRAINT material_research_learner_id_material_id_fkey FOREIGN KEY (learner_id, material_id) REFERENCES public.materials(learner_id, material_id);

ALTER TABLE ONLY public.material_research
    ADD CONSTRAINT material_research_topic_id_fkey FOREIGN KEY (topic_id) REFERENCES public.topic_scopes(topic_id);

ALTER TABLE ONLY public.material_retrieval_indexes
    ADD CONSTRAINT material_retrieval_indexes_learner_id_material_id_structur_fkey FOREIGN KEY (learner_id, material_id, structure_revision) REFERENCES public.knowledge_structures(learner_id, material_id, structure_revision) ON DELETE CASCADE;

ALTER TABLE ONLY public.material_source_set_items
    ADD CONSTRAINT material_source_set_items_learner_id_material_id_source_id_fkey FOREIGN KEY (learner_id, material_id, source_id, normalization_id) REFERENCES public.source_normalizations(learner_id, material_id, source_id, normalization_id);

ALTER TABLE ONLY public.material_source_set_items
    ADD CONSTRAINT material_source_set_items_learner_id_material_id_source_se_fkey FOREIGN KEY (learner_id, material_id, source_set_id) REFERENCES public.material_source_sets(learner_id, material_id, source_set_id);

ALTER TABLE ONLY public.material_source_sets
    ADD CONSTRAINT material_source_sets_learner_id_material_id_fkey FOREIGN KEY (learner_id, material_id) REFERENCES public.materials(learner_id, material_id);

ALTER TABLE ONLY public.material_sources
    ADD CONSTRAINT material_sources_learner_id_material_id_original_artifact__fkey FOREIGN KEY (learner_id, material_id, original_artifact_id) REFERENCES public.artifacts(learner_id, material_id, artifact_id);

ALTER TABLE ONLY public.materials
    ADD CONSTRAINT materials_learner_id_fkey FOREIGN KEY (learner_id) REFERENCES public.learners(learner_id);

ALTER TABLE ONLY public.materials
    ADD CONSTRAINT materials_source_artifact_fk FOREIGN KEY (learner_id, material_id, source_artifact_id) REFERENCES public.artifacts(learner_id, material_id, artifact_id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE ONLY public.podcast_scenes
    ADD CONSTRAINT podcast_scenes_podcast_id_fkey FOREIGN KEY (podcast_id) REFERENCES public.podcasts(podcast_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.podcast_videos
    ADD CONSTRAINT podcast_videos_artifact_id_fkey FOREIGN KEY (artifact_id) REFERENCES public.artifacts(artifact_id);

ALTER TABLE ONLY public.podcast_videos
    ADD CONSTRAINT podcast_videos_podcast_id_fkey FOREIGN KEY (podcast_id) REFERENCES public.podcasts(podcast_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.podcasts
    ADD CONSTRAINT podcasts_learner_id_material_id_fkey FOREIGN KEY (learner_id, material_id) REFERENCES public.materials(learner_id, material_id);

ALTER TABLE ONLY public.podcasts
    ADD CONSTRAINT podcasts_learner_id_material_id_knowledge_structure_revisi_fkey FOREIGN KEY (learner_id, material_id, knowledge_structure_revision) REFERENCES public.knowledge_structures(learner_id, material_id, structure_revision);

ALTER TABLE ONLY public.assessment_sets
    ADD CONSTRAINT remediation_origin_scope FOREIGN KEY (diagnostic_set_id, study_session_id, knowledge_structure_revision, target_concept_id) REFERENCES public.assessment_sets(set_id, study_session_id, knowledge_structure_revision, target_concept_id);

ALTER TABLE ONLY public.material_processing_runs
    ADD CONSTRAINT run_source_set_fk FOREIGN KEY (learner_id, material_id, input_source_set_id) REFERENCES public.material_source_sets(learner_id, material_id, source_set_id);

ALTER TABLE ONLY public.source_normalizations
    ADD CONSTRAINT source_normalizations_learner_id_material_id_mapping_artif_fkey FOREIGN KEY (learner_id, material_id, mapping_artifact_id) REFERENCES public.artifacts(learner_id, material_id, artifact_id);

ALTER TABLE ONLY public.source_normalizations
    ADD CONSTRAINT source_normalizations_learner_id_material_id_normalized_ar_fkey FOREIGN KEY (learner_id, material_id, normalized_artifact_id) REFERENCES public.artifacts(learner_id, material_id, artifact_id);

ALTER TABLE ONLY public.source_normalizations
    ADD CONSTRAINT source_normalizations_learner_id_material_id_source_id_fkey FOREIGN KEY (learner_id, material_id, source_id) REFERENCES public.material_sources(learner_id, material_id, source_id);

ALTER TABLE ONLY public.study_sessions
    ADD CONSTRAINT study_sessions_learner_id_material_id_knowledge_structure__fkey FOREIGN KEY (learner_id, material_id, knowledge_structure_revision) REFERENCES public.knowledge_structures(learner_id, material_id, structure_revision);

ALTER TABLE ONLY public.topic_scopes
    ADD CONSTRAINT topic_material_owner FOREIGN KEY (learner_id, material_id) REFERENCES public.materials(learner_id, material_id);

ALTER TABLE ONLY public.topic_scopes
    ADD CONSTRAINT topic_scopes_learner_id_fkey FOREIGN KEY (learner_id) REFERENCES public.learners(learner_id);

ALTER TABLE ONLY public.topic_scopes
    ADD CONSTRAINT topic_scopes_research_id_fkey FOREIGN KEY (research_id) REFERENCES public.material_research(research_id) ON DELETE SET NULL;

ALTER TABLE ONLY public.voice_conversations
    ADD CONSTRAINT voice_conversations_learner_id_material_id_fkey FOREIGN KEY (learner_id, material_id) REFERENCES public.materials(learner_id, material_id);

ALTER TABLE ONLY public.voice_conversations
    ADD CONSTRAINT voice_conversations_learner_id_material_id_knowledge_struc_fkey FOREIGN KEY (learner_id, material_id, knowledge_structure_revision) REFERENCES public.knowledge_structures(learner_id, material_id, structure_revision);

ALTER TABLE ONLY public.voice_turns
    ADD CONSTRAINT voice_turns_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.voice_conversations(conversation_id) ON DELETE CASCADE;
