import './research.css';
import { useEffect, useState } from 'react';
import { materialElapsedLabel } from '../material-flow/material-flow';
import { SourceCandidates, type Candidate } from './SourceCandidates';
import { Progress } from '../../ui/Progress';
import type { ResearchProgress as ResearchProgressView } from './research';

// 進行中只說明目前在做什麼；內部階段、計數與除錯資訊留在後端紀錄。
const headings: Record<string, string> = {
  planning: '正在規劃查找方向', searching: '正在搜尋來源', collecting: '正在讀取來源摘要', screening: '正在挑選切題的來源',
  reading: '正在閱讀來源內容', locating: '正在閱讀來源內容', assessing: '正在閱讀來源內容',
  following_up: '正在檢查還缺哪些觀念', organizing: '正在整理可用的來源',
};

export function ResearchProgress({ progress, running, candidates }: { progress: ResearchProgressView; running: boolean; candidates: Candidate[] }) {
  const [now,setNow]=useState(Date.now);
  useEffect(()=>{if(!running||!progress.started_at)return;const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer);},[running,progress.started_at]);
  const availableSources=candidates.filter(candidate=>candidate.eligible);
  const available=availableSources.length;
  if (running) {
    const countable = progress.total_topics !== undefined && progress.total_topics > 0 && progress.completed_topics !== undefined;
    const waiting = progress.waiting_until && progress.waiting_until * 1000 > now;
    const labels={waiting:'等待中',searching:'搜尋中',reading:'閱讀候選來源中'};
    return <section className="research-progress research-topic-progress" aria-label="研究進度">
      <div className="research-topic-summary"><div className="research-progress-heading"><strong>{headings[progress.stage] ?? '正在找來源'}</strong>
        {countable && <span>已完成 {progress.completed_topics} / {progress.total_topics} 個觀念</span>}</div>
      <Progress aria-label="研究進度" max={progress.total_topics} value={countable ? progress.completed_topics : undefined} />
      {progress.started_at && <p>已耗時 {materialElapsedLabel(progress.started_at,now)}</p>}
      {progress.topic_progress && <ol className="research-topic-statuses">{progress.topic_progress.map(topic=><li key={topic.topic_index}>
        <strong>{topic.topic}</strong><span>{topic.status==='completed'?`完成 · 找到 ${topic.available_source_count} 份`:labels[topic.status]}</span>
      </li>)}</ol>}
      {waiting && <p role="status">來源網站要求稍候，會在 {new Date(progress.waiting_until! * 1000).toLocaleTimeString('zh-TW')} 後自動接續。</p>}
      {progress.summary && <details className="research-search-method"><summary>搜尋方式說明</summary><p>{progress.summary}</p></details>}
      <small>研究會在背景繼續，可離開頁面；返回後能查看進度或停止。</small></div>
      {available>0 && <section className="research-found-sources" aria-label="已核對來源"><h3>已核對來源 · {available} 份</h3>
        <SourceCandidates candidates={availableSources} selected={[]} disabled readOnly previewOnly showAcquisitionDetails={false} onChange={()=>{}}/>
      </section>}
    </section>;
  }
  const finished = progress.stage === 'complete';
  // 觀念以「主題：細節」撰寫，只列主題名稱並去除重複。
  const gaps = [...new Set(progress.topics.filter(topic => ['partial', 'missing'].includes(topic.status)).map(topic => topic.topic.split(/[：:]/, 1)[0]))];
  const result = finished
    ? available ? `找到 ${available} 份可用來源` : progress.finish_reason === 'references_only' ? '找到相關資料，但都沒有可直接加入的全文' : '沒有找到可加入的來源'
    : available ? `搜尋已停止，目前 ${available} 份來源仍可選取` : '搜尋已停止，尚未找到可選來源';
  return <section className="research-progress" aria-label="研究進度">
    <div className="research-progress-heading"><strong>{result}</strong></div>
    {gaps.length > 0 && <p>這些觀念還沒找到合適來源：{gaps.join('、')}。</p>}
  </section>;
}
