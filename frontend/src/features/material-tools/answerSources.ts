import type { EvidenceView } from '../../api/contracts';
import type { VoiceTurn } from './types';

export function answerSources(answer: VoiceTurn['answer']) {
  const sources: EvidenceView[] = [];
  const numbers = new Map<string, number>();
  const fragments = new Map<string, {start:number;end:number;quote:string}[]>();
  for (const citation of answer?.citations ?? []) {
    for (const evidence of citation.evidence) {
      let number = numbers.get(evidence.evidence_id);
      if (!number) { number = sources.length + 1; numbers.set(evidence.evidence_id, number); sources.push({...evidence}); }
      if (evidence.quote_start != null && evidence.quote_end != null) {
        const parts=fragments.get(evidence.evidence_id)??[];
        if(!parts.some(part=>part.start===evidence.quote_start&&part.end===evidence.quote_end))
          parts.push({start:evidence.quote_start,end:evidence.quote_end,quote:evidence.quote});
        fragments.set(evidence.evidence_id,parts);
      }
    }
  }
  // 同一 Evidence 的不同檢索片段仍共用來源編號，但不能丟掉後段的條件或否定。
  for(const source of sources) {
    const parts=fragments.get(source.evidence_id)?.sort((a,b)=>a.start-b.start);
    if(!parts?.length)continue;
    let quote='',end=parts[0].start;
    for(const part of parts) {
      if(part.start>end)quote+='\n…\n';
      quote+=Array.from(part.quote).slice(Math.max(0,end-part.start)).join('');
      end=Math.max(end,part.end);
    }
    source.quote=quote;
  }
  let text = answer?.text ?? '';
  // 模型只接收本輪索引，來源由結構欄位呈現；不可把程式碼／向量中的 [0,1] 改成引用。
  if (sources.length && !/來源 \d/.test(text)) text += ` [${sources.map((_,index)=>`來源 ${index+1}`).join('、')}]`;
  return {text, sources};
}
