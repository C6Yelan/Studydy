import type { EvidenceView } from '../../api/contracts';
import type { VoiceTurn } from './types';

export function answerSources(answer: VoiceTurn['answer']) {
  const sources: EvidenceView[] = [];
  const numbers = new Map<string, number>();
  const handles = new Map<string, string>();
  const numeric = new Map<number,string>();
  for (const [index,citation] of (answer?.citations ?? []).entries()) {
    const references: string[] = [];
    for (const evidence of citation.evidence) {
      let number = numbers.get(evidence.evidence_id);
      if (!number) { number = sources.length + 1; numbers.set(evidence.evidence_id, number); sources.push(evidence); }
      const label = `來源 ${number}`;
      handles.set(evidence.evidence_id, label);
      if (!references.includes(label)) references.push(label);
    }
    handles.set(citation.claim_id, references.join('、'));
    const sourceIndex=answer?.citation_indices?.[index];
    if (typeof sourceIndex==='number') numeric.set(sourceIndex,references.join('、'));
  }
  let text = answer?.text ?? '';
  // 只替換已驗證引用的 ID；後端 Evidence 與頁碼保持原樣。
  for (const [handle, label] of [...handles].sort((a,b) => b[0].length-a[0].length)) {
    if (handle) text = text.split(handle).join(label);
  }
  text = text.replace(/\[(\d+(?:\s*[,，]\s*\d+)*)\]/g,(original, ids:string)=>{
    const values=ids.split(/[,，]/).map(Number);
    return values.every(id=>numeric.has(id)) ? `[${[...new Set(values.map(id=>numeric.get(id)))].join('、')}]` : original;
  });
  if (sources.length && !/來源 \d/.test(text)) text += ` [${sources.map((_,index)=>`來源 ${index+1}`).join('、')}]`;
  text = text.replace(/\b(?:evidence|claim|page|source|knowledge-structure):sha256:[a-f0-9]{64}\b/gi, '教材來源');
  return {text, sources};
}
