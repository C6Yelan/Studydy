import type { Page } from "@playwright/test";
import type { CardSetView } from "../../src/api/contracts";
import { materialId, structureRevision, structureView, mockKnowledgeMapApi, json } from "./knowledge-map";

export const listPath = `/materials/${materialId}/card-sets`;
export const apiPath = `/v1/materials/${materialId}/card-sets`;
export const cardSetId = "55555555-5555-4555-8555-555555555555";
export const studyPath = `${listPath}/${cardSetId}`;

export function savedSet(view = structureView()): CardSetView {
  return {schema:"card-set/v1",card_set_id:cardSetId,material_id:materialId,
    knowledge_structure_revision:structureRevision,name:"重點複習",ordering_policy:"published_order",
    concept_ids:view.concepts.map(c=>c.concept_id),current_position:0,version:1,
    created_at:"2026-10-02T00:00:00Z",updated_at:"2026-10-02T00:00:00Z"};
}

export function projectedSet(saved: CardSetView, view = structureView()) {
  const cards=view.concepts.filter(c=>saved.concept_ids.includes(c.concept_id));
  const relations=view.relations.filter(r=>saved.concept_ids.includes(r.source_concept_id)||saved.concept_ids.includes(r.target_concept_id)).map(r=>({
    ...r,source_label:view.concepts.find(c=>c.concept_id===r.source_concept_id)!.label,
    target_label:view.concepts.find(c=>c.concept_id===r.target_concept_id)!.label,
    evidence:r.evidence_refs.map(id=>view.concepts.flatMap(c=>c.claims.flatMap(c=>c.evidence)).find(e=>e.evidence_id===id)!),
  }));
  return {schema:"card-set-cards/v1",card_set:saved,cards:{schema:"concept-cards/v1",
    selection:{material_id:materialId,content_material_id:view.material_id,knowledge_structure_revision:structureRevision,
      concept_ids:cards.map(c=>c.concept_id),claim_ids:[...new Set(cards.flatMap(c=>c.claims.map(c=>c.claim_id)))],
      relation_ids:relations.map(r=>r.relation_id),policy:"manual-published-order/v1"},
    cards,relations,status:view.status,excluded_pages:view.excluded_pages,source_resolver:view.source_resolver}};
}

export async function mockCardSets(page: Page, view = structureView(), initial?: CardSetView) {
  await mockKnowledgeMapApi(page, view);
  const rows=new Map<string,CardSetView>(initial?[[initial.card_set_id,structuredClone(initial)]]:[]);
  const requests: {method:string;path:string}[]=[];
  await page.route(url=>url.pathname.startsWith(apiPath), async route=>{
    const request=route.request();const url=new URL(request.url());const suffix=url.pathname.slice(apiPath.length);
    requests.push({method:request.method(),path:url.pathname});
    const error=(status:number,reason="RESOURCE_NOT_FOUND")=>route.fulfill({status,json:{schema:"api-error/v1",request_id:cardSetId,reason_code:reason,retryable:false,message:"Request could not be completed."}});
    if (!suffix && request.method()==="GET") return json(route,{schema:"card-set-list/v1",material_id:materialId,card_sets:[...rows.values()]});
    if (!suffix && request.method()==="POST") {
      const body=request.postDataJSON(); const row={...savedSet(view),name:body.name,concept_ids:view.concepts.filter(c=>body.concept_ids.includes(c.concept_id)).map(c=>c.concept_id)};
      rows.set(row.card_set_id,row); return json(route,row,201);
    }
    const [,id,action]=suffix.split('/');const row=rows.get(id);
    if (!row) return error(404);
    if(request.method()==="GET") return json(route,action==="cards"?projectedSet(row,view):row);
    if(request.method()==="DELETE") {if(Number(url.searchParams.get("version"))!==row.version) return error(409,"CARD_SET_CONFLICT");rows.delete(id);return route.fulfill({status:204});}
    const body=request.postDataJSON();
    if(body.expected_version!==row.version) return error(409,"CARD_SET_CONFLICT");
    if(action==="edit") {
      const ids=view.concepts.filter(c=>body.concept_ids.includes(c.concept_id)).map(c=>c.concept_id);
      if(JSON.stringify(ids)!==JSON.stringify(row.concept_ids)) row.current_position=0;
      row.name=body.name;row.concept_ids=ids;
    } else if(action==="position") row.current_position=body.current_position;
    else return error(404);
    row.version++;return json(route,row);
  });
  return {rows,requests};
}
