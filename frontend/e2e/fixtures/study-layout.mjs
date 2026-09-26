// 合成資料只驗證版面與公開契約；真實交卷／補強語意由隔離 API browser tests 驗證。
export async function studyLayoutFixture(page, initialStage = "preparation", scenario = null) {
  const uuid = (seedNumber) => `00000000-0000-4000-8000-${String(seedNumber).padStart(12, "0")}`;
  const makeRevision = (kind, seedNumber) =>
    `${kind}:sha256:${seedNumber.toString(16).padStart(64, "0")}`;
  const materialId = uuid(1);
  const sessionId = uuid(2);
  const runId = uuid(3);
  const artifactId = uuid(4);
  const setId = uuid(5);
  const historySetId = uuid(6);
  const structureRevision = makeRevision("knowledge-structure", 1);
  const conceptId = makeRevision("concept", 1);
  const sectionId = makeRevision("section", 1);
  const timestamp = "2026-09-01T00:00:00Z";
  const claimTexts = [
    "伺服器接收請求並提供服務。",
    "網站程式依請求回傳網頁內容。",
    "用戶端與伺服器是互動角色。",
    "同一台主機可以同時執行不同服務。",
    "郵件伺服器接受、轉送與保存郵件。",
    "轉送郵件時可成為下一段連線的用戶端。",
  ];
  const claims = claimTexts.map((text, index) => ({
    claim_id: makeRevision("claim", index + 1),
    text,
    evidence: [
      {
        evidence_id: makeRevision("evidence", index + 1),
        page_ref: makeRevision("page", 1),
        page: 1,
        normalized_page: 1,
        source_id: artifactId,
        source_name: scenario?.longContent
          ? "網路服務角色與多階段資料傳输來源_".repeat(5) + ".pptx"
          : "01_網路模型與資料傳輸.pptx",
        block_order: index,
        kind: "paragraph",
        source: "native_text",
        source_locator: {
          page: 1,
          block_id: makeRevision("block", index + 1),
          region: [1, 2, 30, 40],
        },
        quote: text,
      },
    ],
  }));
  const knowledgeStructure = {
    schema: "knowledge-structure-view/v1",
    material_id: makeRevision("material", 1),
    knowledge_structure_revision: structureRevision,
    source_resolver: `/v1/materials/${materialId}/knowledge-structures/${structureRevision}/evidence`,
    status: { processing: "succeeded", quality: "accepted", decision: "retain", reason_codes: [] },
    document_tree: {
      material_id: makeRevision("material", 1),
      sections: [
        {
          section_id: sectionId,
          title: "網路通訊",
          order: 0,
          heading_evidence_id: null,
          concept_ids: [conceptId],
        },
      ],
    },
    concepts: [
      {
        concept_id: conceptId,
        label: "伺服器",
        aliases: ["Server"],
        section_ids: [sectionId],
        source_pages: [1],
        claims,
      },
    ],
    relations: [],
    initial_learning_path: [{ position: 1, concept_id: conceptId, reason: "document_order" }],
    excluded_pages: [],
  };
  const nextConceptId = makeRevision("concept", 2);
  if (scenario?.navigation) {
    knowledgeStructure.concepts.push({
      ...knowledgeStructure.concepts[0],
      concept_id: nextConceptId,
      label: scenario.nextLabel ?? "使用者端",
      claims: claims.map((claim, index) => ({
        ...claim,
        claim_id: makeRevision("claim", 50 + index),
      })),
    });
    knowledgeStructure.document_tree.sections[0].concept_ids.push(nextConceptId);
    knowledgeStructure.initial_learning_path.push({
      position: 2,
      concept_id: nextConceptId,
      reason: "document_order",
    });
  }
  let guidanceApplied = false;
  let nextAction = scenario?.nextAction ?? "advance";
  let guidanceVersion = 1;
  let latestProgress = null;
  let submittedAnswers = null;
  const buildAssessment = (index, offset = 0) => ({
    schema: "single-choice-assessment/v1",
    assessment_revision: makeRevision("assessment", index + offset + 1),
    study_session_id: sessionId,
    knowledge_structure_revision: structureRevision,
    question_id: makeRevision("question", index + offset + 1),
    target_concept_id: conceptId,
    target_claim_id: claims[index].claim_id,
    source_evidence_ids: [claims[index].evidence[0].evidence_id],
    question_type: "single_choice",
    prompt:
      `情境 ${index + 1}：${claimTexts[index]}下列哪一項符合教材所述的角色？` +
      (scenario?.longContent && index === 0
        ? "請比較各階段由誰提出請求、誰提供回應，並依照具體通訊情境判斷角色；同一主機可以在不同階段擔任不同角色。".repeat(
            3,
          )
        : ""),
    options: [
      "接收請求並依需求提供服務",
      "只要發出請求就一定是伺服器",
      "每台主機只能固定擔任單一角色",
      "所有通訊都不需要目的位址",
    ].map((text, optionIndex) => ({
      option_id: makeRevision("option", index * 4 + optionIndex + offset + 1),
      text,
    })),
  });
  const buildFeedback = (assessment, index, historical = false) => {
    // 預載結果使用情境資料；經 UI 交卷後，答案與結果必須反映實際送出的選項。
    const wrong = scenario?.wrong ?? (scenario ? [2, 3] : [5]);
    const selectedOptionId =
      !historical && submittedAnswers
        ? submittedAnswers.get(assessment.assessment_revision)
        : assessment.options[wrong.includes(index) ? 1 : 0].option_id;
    const isCorrect = selectedOptionId === assessment.options[0].option_id;
    return {
      schema: "answer-feedback/v1",
      answer_event_id: uuid(100 + index + (historical ? 20 : 0)),
      study_session_id: sessionId,
      assessment_revision: assessment.assessment_revision,
      question_id: assessment.question_id,
      selected_option_id: selectedOptionId,
      is_correct: isCorrect,
      rationale:
        "請依照教材描述的通訊角色判斷。" +
        (scenario?.longContent && index === 0
          ? "判斷時要檢查請求的方向及對應服務，不以裝置名稱或硬體外形決定角色。".repeat(6)
          : ""),
      source_evidence_ids: assessment.source_evidence_ids,
      event_number: index + 1,
      created_at: timestamp,
    };
  };
  let assessmentStage = initialStage;
  let isPartialPublication = false;
  let setVersion = 1;
  let verifiedCount = 2;
  const requests = [];
  const buildAssessmentSet = (historical = false) => {
    const status = historical ? "completed" : assessmentStage;
    const isCompleted = status === "completed";
    const kind = historical ? "diagnostic" : (scenario?.kind ?? "diagnostic");
    const groupId = historical ? historySetId : setId;
    // 基本流程使用六題；結果與 rail 的自訂情境預設四題，也可指定 count。
    const count = scenario?.count ?? (scenario ? 4 : 6);
    const availableCount = count - (scenario?.unavailable ?? 0);
    const publicationLimit = isPartialPublication && !historical ? 4 : availableCount;
    const publishedCount = ["ready", "in_progress", "completed"].includes(status)
      ? Math.min(availableCount, publicationLimit)
      : 0;
    const answerLimit = isCompleted ? publishedCount - (scenario?.unanswered ?? 0) : 0;
    const items = claims.slice(0, count).map((claim, index) => {
      const assessment =
        index < publishedCount ? buildAssessment(index, historical ? 20 : 0) : null;
      let itemState = "failed";
      if (index >= availableCount) itemState = "omitted";
      else if (assessment) itemState = "published";
      else if (status === "preparing") {
        if (index < verifiedCount) itemState = "verified";
        else if (index === verifiedCount) itemState = "generating";
        else itemState = "pending";
      } else if (status === "partial_ready" && index < 4) itemState = "verified";
      return {
        ordinal: index + 1,
        target_claim_id: claim.claim_id,
        state: itemState,
        attempts: 1,
        failure_reason: null,
        assessment,
        feedback:
          assessment && index < answerLimit ? buildFeedback(assessment, index, historical) : null,
        created_at: assessment ? timestamp : null,
        can_submit: !!assessment && !isCompleted,
      };
    });
    // 統計與事件關係只由 items 推導，未發布／未作答的題目沒有答案事件。
    const points = items.map((item) => {
      let result = "unanswered";
      if (item.state === "omitted" || (isCompleted && !item.assessment)) result = "unavailable";
      else if (item.feedback)
        result = !item.feedback.is_correct
          ? "needs_review"
          : kind === "remediation"
            ? "remediation_pass"
            : "diagnostic_pass";
      return {
        claim_id: item.target_claim_id,
        result,
        latest_answer_event_id: item.feedback?.answer_event_id ?? null,
        latest_set_id: item.feedback ? groupId : null,
      };
    });
    const answeredCount = items.filter((item) => item.feedback).length;
    const passedCount = items.filter((item) => item.feedback?.is_correct).length;
    const pendingCount = points.filter((point) => point.result === "needs_review").length;
    const unansweredCount = points.filter((point) => point.result === "unanswered").length;
    const unavailableCount = points.filter((point) => point.result === "unavailable").length;
    const outcome = !isCompleted
      ? "in_progress"
      : pendingCount
        ? "needs_review"
        : unansweredCount || unavailableCount
          ? "incomplete"
          : "passed";
    return {
      schema: "assessment-set/v1",
      kind,
      diagnostic_set_id: kind === "remediation" ? historySetId : null,
      set_id: groupId,
      target_concept_id: conceptId,
      study_session_id: sessionId,
      material_id: materialId,
      knowledge_structure_revision: structureRevision,
      status,
      set_version: historical ? 1 : setVersion,
      requested_count: count,
      point_count: count,
      excluded_count: 0,
      published_count: publishedCount,
      answered_count: answeredCount,
      passed_count: passedCount,
      verified_count: items.filter((item) => ["published", "verified"].includes(item.state)).length,
      assessment_revisions: items.flatMap((item) =>
        item.assessment ? [item.assessment.assessment_revision] : [],
      ),
      created_at: timestamp,
      completed_at: isCompleted ? timestamp : null,
      selection_policy:
        kind === "remediation" ? "needs-review-points/v1" : "single-concept-grounded-points/v1",
      can_retry: ["partial_ready", "failed"].includes(status),
      can_publish_partial: status === "partial_ready",
      can_complete: publishedCount > 0 && !isCompleted,
      cycle: {
        diagnostic_set_id: kind === "remediation" ? historySetId : groupId,
        concept_id: conceptId,
        set_version: historical ? 1 : setVersion,
        outcome,
        active_set_id: isCompleted ? null : groupId,
        passed_count: passedCount,
        remediation_passed_count: kind === "remediation" ? passedCount : 0,
        pending_count: pendingCount,
        unanswered_count: unansweredCount,
        unavailable_count: unavailableCount,
        can_create_remediation: isCompleted && pendingCount > 0,
        points,
      },
      items,
    };
  };
  const basePath = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}/study-sessions/${sessionId}`;
  const apiSession = `/v1/study-sessions/${sessionId}`;
  const setPath = `${apiSession}/assessment-sets/${setId}`;
  const historyPath = `${apiSession}/assessment-sets/${historySetId}`;
  const mapPath = `/v1/materials/${materialId}/knowledge-structures/${structureRevision}`;
  const methods = new Map([
    ["/v1/session/refresh", "POST"],
    ["/v1/session", "GET"],
    ["/v1/source-capabilities", "GET"],
    [`${mapPath}/study-sessions/${sessionId}/resume`, "GET"],
    [`${apiSession}/guidance/apply`, "POST"],
    [`${apiSession}/assessment-plan`, "GET"],
    [`${apiSession}/assessment-sets`, "POST"],
    [setPath, "GET"],
    [historyPath, "GET"],
    [`${setPath}/retry`, "POST"],
    [`${setPath}/publish-partial`, "POST"],
    [`${setPath}/submissions`, "POST"],
  ]);
  await page.route(/\/v[12]\//, async (route) => {
    const request = route.request();
    const address = new URL(request.url());
    const path = decodeURIComponent(address.pathname);
    const query = address.searchParams;
    const send = (json, status = 200) => route.fulfill({ json, status });
    const reject = (reason_code, status) =>
      send(
        {
          schema: "api-error/v1",
          request_id: uuid(9),
          reason_code,
          retryable: false,
          message: "Request could not be completed.",
        },
        status,
      );
    const sourcePrefix = `${mapPath}/evidence/`;
    const isSource = path.startsWith(sourcePrefix) && path.endsWith("/source");
    const method = isSource ? "GET" : methods.get(path);
    if (!method) return reject("RESOURCE_NOT_FOUND", 404);
    if (request.method() !== method) return reject("METHOD_NOT_ALLOWED", 405);
    if (request.method() !== "GET")
      requests.push({ path, body: request.postData() });
    if (path === "/v1/session/refresh")
      return route.fulfill({
        json: { schema: "learner-identity/v1", learner_id: uuid(9) },
      });
    if (path === "/v1/session") return send({ schema: "learner-identity/v1", learner_id: uuid(9) });
    if (path === "/v1/source-capabilities")
      return send({ schema: "source-capabilities/v1", quality_notice: "PDF", formats: [] });
    if (isSource) {
      const evidenceId = path.slice(sourcePrefix.length, -"/source".length);
      const evidence = claims
        .flatMap((claim) => claim.evidence)
        .find((item) => item.evidence_id === evidenceId);
      if (!evidence) return reject("RESOURCE_NOT_FOUND", 404);
      return send({
        schema: "evidence-source/v1",
        format: "pptx",
        original_name: evidence.source_name,
        original_url: `/v1/artifacts/${artifactId}/download`,
        preview_url: `/v1/artifacts/${artifactId}#page=${evidence.normalized_page}`,
        normalized_page: evidence.normalized_page,
        accuracy: "exact",
        origin_locators: [],
        label: `PDF 第 ${evidence.normalized_page} 頁`,
      });
    }
    if (path.endsWith("/resume")) {
      const selectedId = query.get("set_id");
      if (selectedId && ![setId, historySetId].includes(selectedId))
        return reject("RESOURCE_NOT_FOUND", 404);
      if (query.has("run_id") && query.get("run_id") !== runId)
        return reject("RESOURCE_NOT_FOUND", 404);
      const active = !["preparation", "no-safe"].includes(assessmentStage);
      const historical = selectedId === historySetId;
      const selected = historical
        ? buildAssessmentSet(true)
        : active && (!guidanceApplied || nextAction === "complete")
          ? buildAssessmentSet()
          : null;
      const current = guidanceApplied && nextAction === "advance" ? nextConceptId : conceptId;
      const sessionCompleted = guidanceApplied && nextAction === "complete";
      let assessmentCycles = [];
      if (scenario?.navigation) assessmentCycles = [buildAssessmentSet().cycle];
      else if (selected) assessmentCycles = [selected.cycle];

      const hasPendingNavigation = scenario?.navigation && !guidanceApplied;
      let targetConceptId = current;
      if (hasPendingNavigation) targetConceptId = nextAction === "advance" ? nextConceptId : null;

      let assessmentSets = [];
      if (!scenario?.noHistory) {
        if (scenario?.preparingHistory) {
          assessmentSets.push({
            ...buildAssessmentSet(),
            set_id: uuid(7),
            status: "preparing",
            published_count: 0,
            answered_count: 0,
            passed_count: 0,
            assessment_revisions: [],
          });
        }
        if (active) assessmentSets.push(buildAssessmentSet());
        assessmentSets.push(buildAssessmentSet(true));
      }

      const progress = {
        schema: "learner-progress/v1",
        assessment_cycles: assessmentCycles,
        study_session_id: sessionId,
        knowledge_structure_revision: structureRevision,
        event_watermark: 1,
        current_concept_id: current,
        deferred_concept_ids: [],
        concept_states: knowledgeStructure.concepts.map((concept) => ({
          concept_id: concept.concept_id,
          label: concept.label,
          status: "learning",
          attempts: 1,
          correct_answers: 1,
          qualified_correct_items: 1,
          covered_claim_ids: [concept.claims[0].claim_id],
          mastered_claim_ids: [],
          weak_claim_ids: [],
          latest_is_correct: true,
        })),
        weaknesses: [],
        next_action: {
          action: hasPendingNavigation ? nextAction : "assess",
          target_concept_id: targetConceptId,
          target_claim_id: null,
          prerequisite_concept_ids: [],
          reason: "current_concept",
        },
        guidance_revision: makeRevision("learner-guidance", guidanceVersion),
      };
      latestProgress = progress;
      return send({
        schema: "study-resume/v1",
        session: {
          schema: "study-session/v1",
          study_session_id: sessionId,
          material_id: materialId,
          knowledge_structure_revision: structureRevision,
          current_concept_id: current,
          deferred_concept_ids: [],
          no_safe_claim_ids: [],
          status: sessionCompleted ? "completed" : "active",
          started_at: timestamp,
          completed_at: sessionCompleted ? timestamp : null,
          event_watermark: 1,
        },
        progress,
        knowledge_structure: knowledgeStructure,
        source_artifact_id: artifactId,
        run_id: runId,
        assessment_sets: assessmentSets,
        selected_set_id: selected?.set_id ?? null,
      });
    }
    if (path.endsWith("/guidance/apply")) {
      const body = request.postDataJSON();
      if (
        !latestProgress ||
        body?.schema !== "guidance-apply/v1" ||
        body.guidance_revision !== makeRevision("learner-guidance", guidanceVersion)
      )
        return reject("LEARNER_GUIDANCE_STALE", 409);
      guidanceApplied = true;
      return send({
        ...latestProgress,
        current_concept_id: nextAction === "advance" ? nextConceptId : conceptId,
      });
    }
    if (path.endsWith("/assessment-plan")) {
      const concept = knowledgeStructure.concepts.find(
        (item) => item.concept_id === query.get("concept_id"),
      );
      if (!concept) return reject("RESOURCE_NOT_FOUND", 404);
      const targets = concept.claims;
      return send({
        schema: "assessment-plan/v1",
        study_session_id: sessionId,
        knowledge_structure_revision: structureRevision,
        policy: "single-concept-grounded-points/v1",
        concept_id: concept.concept_id,
        point_count: targets.length,
        requested_count: assessmentStage === "no-safe" ? 0 : targets.length,
        targets:
          assessmentStage === "no-safe"
            ? []
            : targets.map((claim) => ({
                claim_id: claim.claim_id,
                covered_claim_ids: [claim.claim_id],
                reason: "distinct_grounded_point",
              })),
        excluded:
          assessmentStage === "no-safe"
            ? targets.map((claim) => ({ claim_id: claim.claim_id, reason: "no_content_evidence" }))
            : [],
      });
    }
    if (path.endsWith("/assessment-sets") && request.method() === "POST") {
      const body = request.postDataJSON();
      if (body?.schema !== "assessment-set-create/v1" || body.target_concept_id !== conceptId)
        return reject("RESOURCE_NOT_FOUND", 404);
      assessmentStage = "preparing";
      setVersion++;
      return send(buildAssessmentSet(), 202);
    }
    if (path.endsWith("/retry")) {
      assessmentStage = "preparing";
      setVersion++;
      return send(buildAssessmentSet());
    }
    if (path.endsWith("/publish-partial")) {
      assessmentStage = "ready";
      isPartialPublication = true;
      setVersion++;
      return send(buildAssessmentSet());
    }
    if (path.endsWith("/submissions")) {
      submittedAnswers = new Map(
        request.postDataJSON().answers.map((answer) => [
          answer.assessment_revision,
          answer.selected_option_id,
        ]),
      );
      assessmentStage = "completed";
      setVersion++;
      return send(buildAssessmentSet());
    }
    if (path.endsWith(`/assessment-sets/${historySetId}`)) return send(buildAssessmentSet(true));
    if (path.endsWith(`/assessment-sets/${setId}`)) return send(buildAssessmentSet());
    throw new Error(`Unexpected layout fixture request: ${request.method()} ${path}`);
  });
  return {
    path: basePath,
    historyPath: `${basePath}/assessment-sets/${historySetId}`,
    requests,
    setGuidance(action, version) {
      nextAction = action;
      guidanceVersion = version;
    },
    setStage(next) {
      assessmentStage = next;
      setVersion++;
    },
    setPreparedCount(count) {
      verifiedCount = count;
      setVersion++;
    },
    async open() {
      await page.goto(basePath);
      await page
        .locator(".assessment-set-header, .assessment-cycle, .assessment-card")
        .first()
        .waitFor();
    },
  };
}
