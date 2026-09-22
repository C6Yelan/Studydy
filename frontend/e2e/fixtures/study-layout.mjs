// 合成資料只驗證版面與公開契約；真實交卷／補強語意由隔離 API browser tests 驗證。
export async function studyLayoutFixture(page, initialStage = "preparation", scenario = null) {
  const uuid = n => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
  const rev = (kind, n) => `${kind}:sha256:${n.toString(16).padStart(64, "0")}`;
  const material = uuid(1), session = uuid(2), run = uuid(3), artifact = uuid(4), setId = uuid(5), historyId = uuid(6);
  const revision = rev("knowledge-structure", 1), conceptId = rev("concept", 1), sectionId = rev("section", 1);
  const timestamp = "2026-09-01T00:00:00Z";
  const texts = ["伺服器接收請求並提供服務。", "網站程式依請求回傳網頁內容。", "用戶端與伺服器是互動角色。", "同一台主機可以同時執行不同服務。", "郵件伺服器接受、轉送與保存郵件。", "轉送郵件時可成為下一段連線的用戶端。"];
  const claims = texts.map((text, i) => ({claim_id:rev("claim",i+1),text,evidence:[{
    evidence_id:rev("evidence",i+1),page_ref:rev("page",1),page:1,normalized_page:1,source_id:artifact,
    source_name:scenario?.longContent ? "網路服務角色與多階段資料傳输來源_".repeat(5)+".pptx" : "01_網路模型與資料傳輸.pptx",block_order:i,kind:"paragraph",source:"native_text",
    source_locator:{page:1,block_id:rev("block",i+1),region:[1,2,30,40]},quote:text,
  }]}));
  const view = {schema:"knowledge-structure-view/v3",material_id:rev("material",1),knowledge_structure_revision:revision,
    source_resolver:`/v2/materials/${material}/knowledge-structures/${revision}/evidence`,
    status:{processing:"succeeded",quality:"accepted",decision:"retain",reason_codes:[]},
    document_tree:{material_id:rev("material",1),sections:[{section_id:sectionId,title:"網路通訊",order:0,heading_evidence_id:null,concept_ids:[conceptId]}]},
    concepts:[{concept_id:conceptId,label:"伺服器",aliases:["Server"],section_ids:[sectionId],source_pages:[1],claims}],
    relations:[],initial_learning_path:[{position:1,concept_id:conceptId,reason:"document_order"}],excluded_pages:[]};
  const nextConceptId=rev("concept",2);
  if(scenario?.navigation){
    view.concepts.push({...view.concepts[0],concept_id:nextConceptId,label:scenario.nextLabel??"使用者端",claims:claims.map((c,i)=>({...c,claim_id:rev("claim",50+i)}))});
    view.document_tree.sections[0].concept_ids.push(nextConceptId);
    view.initial_learning_path.push({position:2,concept_id:nextConceptId,reason:"document_order"});
  }
  let applied=false, navigationAction=scenario?.nextAction??"advance", guidanceVersion=1, latestProgress=null;
  const question = (i, offset=0) => ({schema:"single-choice-assessment/v2",assessment_revision:rev("assessment",i+offset+1),
    study_session_id:session,knowledge_structure_revision:revision,question_id:rev("question",i+offset+1),target_concept_id:conceptId,
    target_claim_id:claims[i].claim_id,source_evidence_ids:[claims[i].evidence[0].evidence_id],question_type:"single_choice",
    prompt:`情境 ${i+1}：${texts[i]}下列哪一項符合教材所述的角色？`+(scenario?.longContent && i===0 ? "請比較各階段由誰提出請求、誰提供回應，並依照具體通訊情境判斷角色；同一主機可以在不同階段擔任不同角色。".repeat(3) : ""),
    options:["接收請求並依需求提供服務", "只要發出請求就一定是伺服器", "每台主機只能固定擔任單一角色", "所有通訊都不需要目的位址"].map((text,j)=>({option_id:rev("option",i*4+j+offset+1),text}))});
  const feedback = (assessment,i) => ({schema:"answer-feedback/v2",answer_event_id:uuid(100+i),study_session_id:session,
    assessment_revision:assessment.assessment_revision,question_id:assessment.question_id,selected_option_id:assessment.options[i===5?1:0].option_id,
    is_correct:i!==5,rationale:"請依照教材描述的通訊角色判斷。"+(scenario?.longContent && i===0 ? "判斷時要檢查請求的方向及對應服務，不以裝置名稱或硬體外形決定角色。".repeat(6) : ""),source_evidence_ids:assessment.source_evidence_ids,event_number:i+1,created_at:timestamp});
  let stage=initialStage, partial=false, version=1, preparedCount=2;
  const requests=[];
  const group = (historical=false) => {
    const status=historical?"completed":stage==="submitted"?"completed":stage;
    const closed=status==="completed", published=["ready","in_progress","completed"].includes(status)?(partial&&!historical?4:6):0;
    const items=claims.map((claim,i)=>{const assessment=i<published?question(i,historical?20:0):null;
      return {ordinal:i+1,target_claim_id:claim.claim_id,state:assessment?"published":status==="preparing"?(i<preparedCount?"verified":i===preparedCount?"generating":"pending"):status==="partial_ready"&&i<4?"verified":"failed",
        attempts:1,failure_reason:null,assessment,feedback:assessment&&closed?feedback(assessment,i):null,created_at:assessment?timestamp:null,can_submit:!!assessment&&!closed};});
    const passed=closed?Math.min(5,published):0, pending=closed&&published===6?1:0;
    const result = {schema:"assessment-set/v3",kind:"diagnostic",diagnostic_set_id:null,set_id:historical?historyId:setId,target_concept_id:conceptId,
      study_session_id:session,material_id:material,knowledge_structure_revision:revision,status,set_version:version,
      requested_count:6,point_count:6,excluded_count:0,published_count:published,answered_count:closed?published:0,passed_count:passed,
      verified_count:items.filter(i=>["published","verified"].includes(i.state)).length,assessment_revisions:items.flatMap(i=>i.assessment?[i.assessment.assessment_revision]:[]),
      created_at:timestamp,completed_at:closed?timestamp:null,selection_policy:"single-concept-grounded-points/v1",
      can_retry:["partial_ready","failed"].includes(status),can_publish_partial:status==="partial_ready",can_complete:published>0&&!closed,
      cycle:{diagnostic_set_id:historical?historyId:setId,concept_id:conceptId,set_version:version,outcome:closed?"needs_review":"in_progress",active_set_id:closed?null:setId,
        passed_count:passed,remediation_passed_count:0,pending_count:pending,unanswered_count:closed?0:6,unavailable_count:closed?6-published:0,
        can_create_remediation:closed && pending > 0,points:claims.map((c,i)=>({claim_id:c.claim_id,result:closed?(i>=published?"unavailable":i===5?"needs_review":"diagnostic_pass"):"unanswered",latest_answer_event_id:closed?uuid(100+i):null,latest_set_id:closed?(historical?historyId:setId):null}))},items};
    if (scenario) {
      const count = scenario.count ?? 4;
      const available = count - (scenario.unavailable ?? 0);
      const answered = closed ? available - (scenario.unanswered ?? 0) : 0;
      const wrong = scenario.wrong ?? [2, 3];
      result.kind = historical ? "diagnostic" : scenario.kind ?? "diagnostic";
      result.selection_policy = result.kind === "remediation" ? "needs-review-points/v1" : "single-concept-grounded-points/v1";
      result.diagnostic_set_id = result.kind === "remediation" ? historyId : null;
      result.items = result.items.slice(0, count).map((item, i) => ({...item,
        assessment: i < available ? item.assessment : null,
        state: i < available ? item.state : "omitted",
        created_at: i < available ? item.created_at : null,
        feedback: i < answered && item.assessment ? {...feedback(item.assessment, i),
          is_correct: !wrong.includes(i), selected_option_id:item.assessment.options[wrong.includes(i)?1:0].option_id} : null,
        can_submit: !closed && i < available && !!item.assessment,
      }));
      result.verified_count = result.items.filter(i => ["published", "verified"].includes(i.state)).length;
      result.requested_count = result.point_count = count;
      result.published_count = published ? available : 0;
      result.answered_count = answered;
      result.passed_count = result.items.filter(i => i.feedback?.is_correct).length;
      result.cycle = {...result.cycle, diagnostic_set_id: result.diagnostic_set_id ?? result.set_id,
        passed_count: result.passed_count, remediation_passed_count: result.kind === "remediation" ? result.passed_count : 0,
        pending_count: closed ? result.items.filter(i => i.feedback && !i.feedback.is_correct).length : 0,
        unanswered_count: closed ? available - answered : available, unavailable_count: count - available,
        points: result.cycle.points.slice(0, count).map((point, i) => ({...point,
          result:i>=available?"unavailable":i>=answered?"unanswered":wrong.includes(i)?"needs_review":result.kind==="remediation"?"remediation_pass":"diagnostic_pass"})),
      };
      result.cycle.outcome = !closed ? "in_progress" : result.cycle.pending_count ? "needs_review" : result.cycle.unanswered_count || result.cycle.unavailable_count ? "incomplete" : "passed";
      result.cycle.can_create_remediation = closed && result.cycle.pending_count > 0;
      result.assessment_revisions = result.items.flatMap(i => i.assessment ? [i.assessment.assessment_revision] : []);
    }
    return result;
  };
  const basePath=`/materials/${material}/runs/${run}/knowledge-structures/${encodeURIComponent(revision)}/study-sessions/${session}`;
  await page.route(/\/v[12]\//,async route=>{
    const request=route.request(), address=request.url(), path=address.replace(/^https?:\/\/[^/]+/, "").split("?")[0];
    const query=Object.fromEntries((address.split("?")[1]??"").split("&").map(pair=>pair.split("=").map(decodeURIComponent)));
    const send=(json,status=200)=>route.fulfill({json,status});
    if(request.method()!=="GET")requests.push({path,body:request.postData(),key:request.headers()["idempotency-key"]});
    if(path==="/v1/session/refresh")return route.fulfill({json:{schema:"learner-identity/v1",learner_id:"33333333-3333-4333-8333-333333333333"}});
    if(path==="/v1/session")return send({schema:"learner-identity/v1",learner_id:uuid(9)});
    if(path==="/v2/source-capabilities")return send({schema:"source-capabilities/v1",quality_notice:"PDF",formats:[]});
    if(path.endsWith("/source"))return send({schema:"evidence-source/v1",format:"pptx",original_name:"01_網路模型與資料傳輸.pptx",original_url:`/v2/artifacts/${artifact}`,preview_url:`/v1/artifacts/${artifact}#page=1`,normalized_page:1,accuracy:"exact",origin_locators:[],label:"PDF 第 1 頁"});
    if(path.endsWith("/resume")) {
      const active=!["preparation","no-safe"].includes(stage), historical=query.set_id===historyId;
      const selected=historical?group(true):active&&(!applied||navigationAction==="complete")?group():null;
      const current=applied&&navigationAction==="advance"?nextConceptId:conceptId;
      const sessionCompleted=applied&&navigationAction==="complete";
      const progress={schema:"learner-progress/v4",assessment_cycles:scenario?.navigation?[group().cycle]:selected?[selected.cycle]:[],study_session_id:session,knowledge_structure_revision:revision,event_watermark:1,
        current_concept_id:current,deferred_concept_ids:[],concept_states:view.concepts.map(c=>({concept_id:c.concept_id,label:c.label,status:"learning",attempts:1,correct_answers:1,qualified_correct_items:1,covered_claim_ids:[c.claims[0].claim_id],mastered_claim_ids:[],weak_claim_ids:[],latest_is_correct:true})),weaknesses:[],
        next_action:{action:scenario?.navigation&&!applied?navigationAction:"assess",target_concept_id:scenario?.navigation&&!applied?(navigationAction==="advance"?nextConceptId:null):current,target_claim_id:null,prerequisite_concept_ids:[],reason:"current_concept"},guidance_revision:rev("learner-guidance",guidanceVersion)};
      latestProgress=progress;
      return send({schema:"study-resume/v5",session:{schema:"study-session/v2",study_session_id:session,material_id:material,knowledge_structure_revision:revision,current_concept_id:current,deferred_concept_ids:[],no_safe_claim_ids:[],status:sessionCompleted?"completed":"active",started_at:timestamp,completed_at:sessionCompleted?timestamp:null,event_watermark:1},
        progress,knowledge_structure:view,source_artifact_id:artifact,run_id:run,assessment_sets:scenario?.noHistory ? [] : scenario?.preparingHistory ? [{...group(),set_id:uuid(7),status:"preparing",published_count:0,answered_count:0,passed_count:0,assessment_revisions:[]},...(active?[group(),group(true)]:[group(true)])] : active?[group(),group(true)]:[group(true)],selected_set_id:selected?.set_id??null});
    }
    if(path.endsWith("/guidance/apply")){applied=true;return send({...latestProgress,current_concept_id:navigationAction==="advance"?nextConceptId:conceptId});}
    if(path.endsWith("/assessment-plan"))return send({schema:"assessment-plan/v1",study_session_id:session,knowledge_structure_revision:revision,policy:"single-concept-grounded-points/v1",concept_id:query.concept_id??conceptId,point_count:6,requested_count:stage==="no-safe"?0:6,targets:stage==="no-safe"?[]:claims.map(c=>({claim_id:c.claim_id,covered_claim_ids:[c.claim_id],reason:"distinct_grounded_point"})),excluded:stage==="no-safe"?claims.map(c=>({claim_id:c.claim_id,reason:"no_content_evidence"})):[]});
    if(path.endsWith("/assessment-sets")&&request.method()==="POST"){stage="preparing";version++;return send(group(),202);}
    if(path.endsWith("/retry")){stage="preparing";version++;return send(group());}
    if(path.endsWith("/publish-partial")){stage="ready";partial=true;version++;return send(group());}
    if(path.endsWith("/submissions")){stage="submitted";version++;return send(group());}
    if(path.endsWith(`/assessment-sets/${historyId}`))return send(group(true));
    if(path.endsWith(`/assessment-sets/${setId}`))return send(group());
    throw new Error(`Unexpected layout fixture request: ${request.method()} ${path}`);
  });
  return {path:basePath, historyPath:`${basePath}/assessment-sets/${historyId}`, requests,
    setGuidance(action,version){navigationAction=action;guidanceVersion=version;},
    setStage(next){stage=next;version++;}, setPreparedCount(count){preparedCount=count;version++;}, async open(){await page.goto(basePath);await page.locator('.assessment-set-header, .assessment-cycle, .assessment-card').first().waitFor();}};
}
