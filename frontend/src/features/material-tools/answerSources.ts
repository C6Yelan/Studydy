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
  // 正文保留原文；來源在回答下方顯示為「第 N 頁」小標籤（可開啟原頁），摘錄收在「引用原文」展開區。
  return {text: answer?.text ?? '', sources};
}
