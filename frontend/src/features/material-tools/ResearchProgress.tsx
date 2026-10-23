import './research.css';
import type { ResearchProgress as Progress } from './research';

// 進行中只說明目前在做什麼；內部階段、計數與除錯資訊留在後端紀錄。
const headings: Record<string, string> = {
  planning: '正在規劃查找方向', searching: '正在搜尋來源', collecting: '正在讀取來源摘要', screening: '正在挑選切題的來源',
  reading: '正在閱讀來源內容', locating: '正在閱讀來源內容', assessing: '正在閱讀來源內容',
  following_up: '正在檢查還缺哪些觀念', organizing: '正在整理可用的來源',
};

/** 研究進度；`available` 是目前可選取的來源數。 */
export function ResearchProgress({ progress, running, available }: { progress: Progress; running: boolean; available: number }) {
  if (running) {
    const countable = progress.total !== null && progress.total > 0 && progress.completed !== null;
    const waiting = progress.waiting_until && progress.waiting_until * 1000 > Date.now();
    return <section className="research-progress" aria-label="研究進度">
      <div className="research-progress-heading"><strong>{headings[progress.stage] ?? '正在找來源'}</strong>
        {countable && <span>{progress.completed!.toLocaleString()} / {progress.total!.toLocaleString()} {progress.unit === 'queries' ? '組搜尋' : '份來源'}</span>}</div>
      {countable && <progress aria-label="研究進度" max={progress.total!} value={progress.completed!} />}
      <p role="status">{waiting
        ? `來源網站要求稍候，會在 ${new Date(progress.waiting_until! * 1000).toLocaleTimeString('zh-TW')} 後自動接續。`
        : progress.summary || '找到可用的來源後，就能選擇要加入的內容。'}</p>
      <small>研究會在背景繼續，可離開頁面；返回後能查看進度或停止。</small>
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
