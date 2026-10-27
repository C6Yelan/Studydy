import "./card-practice.css";
import { MathText } from "../../ui/MathText";
import { DifficultyBadge } from "../learning-diagnosis/LearningDiagnosis";
import type { StudydyApiClient } from "../../api/client";
import type { ConceptCard, ConceptCardContext, ConceptDifficulty } from "../../api/contracts";
import { claimText } from "../../ui/claim-text";
import { VisualConceptCard, useConceptArtwork } from "./VisualConceptCard";
import { evidencePlace, SourceButton, sourceLinks } from "../../ui/SourceButton";
import { cardContent } from "./card-content";
import { cardRelations, cardSourceIndex, hasCardEvidence } from "./card-relation";

export function Flashcard({ card, structure, materialName, apiClient, sourceResolver, pageIndex, difficulty, back, onFlip, downloadName }: {
  card: ConceptCard;
  structure: ConceptCardContext;
  materialName: string;
  apiClient: StudydyApiClient;
  sourceResolver: string;
  pageIndex: number;
  difficulty?: ConceptDifficulty;
  back: boolean;
  onFlip: () => void;
  downloadName: () => Promise<string>;
}) {
  const relations = cardRelations(card, structure);
  const content = cardContent(card, structure);
  const shownEvidence = [...card.claims.flatMap(claim => claim.evidence), ...(content?.kind === "group" ? content.items.flatMap(item => item.evidence) : [])];
  const unresolved = structure.relations.filter(r => (r.source_concept_id === card.concept_id || r.target_concept_id === card.concept_id)
    && !relations.some(item => item.relation.relation_id === r.relation_id));
  const review = structure.status.quality === "needs_review" || structure.status.processing !== "succeeded" || structure.status.decision !== "retain";
  const designed = card.design?.pages[pageIndex];
  const pageEvidence = designed ? [...new Set([designed.takeaway, ...designed.blocks, ...designed.links].flatMap(item => item.evidence_ids))]
    .flatMap(id => { const evidence = cardSourceIndex(structure).evidence.get(id); return evidence ? [evidence] : []; }) : [];
  const { artworkModule } = useConceptArtwork();
  const legacy = !designed ? artworkModule?.conceptCardPages(card, structure)[pageIndex] : null;
  const headings = designed?.layout === "compare" ? designed.blocks.slice(0, 2).map(block => block.heading)
    : legacy && "relation" in legacy && legacy.relation?.relation.type === "contrast" ? [legacy.relation.source.label, legacy.relation.target.label] : [];
  return (
    <div className={`concept-card card-practice-face ${back ? "is-back" : "is-front"}`}>
      {!back ? <button type="button" className="card-practice-front" onClick={onFlip} aria-label="翻到背面">
        <span>先回想，再翻面</span><h2>{card.label}</h2>
        {headings.length > 0 ? <div className="card-practice-comparison">{headings.map((heading, i) => <strong key={i}><MathText text={heading} /></strong>)}</div>
          : <p>這個概念的重點是什麼？先在心裡說說看</p>}
        <small>點卡片或按空白鍵查看重點</small>
      </button> : <>
      <button className="text-button card-practice-flip" type="button" onClick={onFlip}>翻回正面</button>
      <DifficultyBadge concept={difficulty} detail />
      <VisualConceptCard card={card} structure={structure} pageIndex={pageIndex} cacheOwner={apiClient} downloadName={downloadName} onFlip={onFlip}
        sources={designed && <div className="flashcard-source-links" aria-label="本張卡面來源">{sourceLinks(pageEvidence).map(evidence =>
          <SourceButton key={evidence.evidence_id} apiClient={apiClient} resolver={sourceResolver} evidence={evidence} label={`本張引用 · ${evidencePlace(evidence)}`} />)}</div>} />
      <details className="concept-card-details">
        <summary>完整重點與來源</summary>
        <div className="flashcard">
          <article className="flashcard-details" aria-label={`${card.label}的重點`}>
            <span className="flashcard-kicker">概念重點</span>
            {review && <span className="flashcard-review">教材版本待複核</span>}
            <h2>{card.label}</h2>
            {card.claims.some(claim => !claim.evidence.length || claim.evidence.some(e => !hasCardEvidence(e))) && <p className="flashcard-review">部分重點缺少可回查來源。</p>}
            <div className="flashcard-content" tabIndex={0} aria-label="卡片重點內容">
              {card.claims.map((claim, index) => (
                <section className="flashcard-point" key={claim.claim_id}>
                  <span className="flashcard-point-number" aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>
                  <p
                    className={claim.evidence.some((item) => item.kind === "code") ? "is-code" : undefined}
                    tabIndex={claim.evidence.some((item) => item.kind === "code") ? 0 : undefined}
                  ><MathText text={claimText(claim)} code={claim.evidence.some(item => item.kind === "code")}/></p>
                </section>
              ))}
              {relations.map(relation => <section key={relation.relation.relation_id} className="flashcard-relation" aria-label="圖卡關係與來源">
                <h3>教材關係</h3>
                <p>{relation.source.label} ／ {relation.target.label}</p>
                <p>{relation.relation.learner_reason}</p>
                <div className="flashcard-source-links">{sourceLinks(relation.evidence).map(evidence => <SourceButton key={evidence.evidence_id}
                  apiClient={apiClient} resolver={sourceResolver} evidence={evidence} label={`關係來源 · ${evidencePlace(evidence)}`} />)}</div>
              </section>)}
              {unresolved.map(relation => <section key={relation.relation_id} className="flashcard-relation" aria-label="待複核的教材關係">
                <h3>教材關係 · 待複核</h3>
                <p>{relation.learner_reason || "此關係尚無說明。"}</p>
                <p>此關係的方向或來源需要確認；以下列出可回查的來源。</p>
                <div className="flashcard-source-links">{sourceLinks(relation.evidence_refs.flatMap(id => {
                  const evidence = cardSourceIndex(structure).evidence.get(id);
                  return evidence && hasCardEvidence(evidence) ? [evidence] : [];
                })).map(evidence =>
                  <SourceButton key={evidence.evidence_id} apiClient={apiClient} resolver={sourceResolver} evidence={evidence} />)}</div>
              </section>)}
              <section className="flashcard-sources" aria-label="概念重點來源">
                <h3>來源</h3>
                <div className="flashcard-source-links">
                  {sourceLinks(shownEvidence).map(evidence => (
                    <SourceButton key={evidence.evidence_id} apiClient={apiClient} resolver={sourceResolver} evidence={evidence}
                      label={evidence.source_name ? `${evidence.source_name} · ${evidencePlace(evidence)}` : undefined} />
                  ))}
                </div>
              </section>
            </div>
          </article>
        </div>
      </details>
      </>}
    </div>
  );
}
