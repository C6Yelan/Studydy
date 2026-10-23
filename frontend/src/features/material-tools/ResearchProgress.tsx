import './research.css';
import type { ResearchProgress as Progress } from './research';
import { OtherResults, type Candidate } from './SourceCandidates';

const stages = ['需求拆解', '收集來源與摘要', '摘要初篩', '重點閱讀與核對', '整理與缺口檢查'];
const index: Record<string, number> = { planning: 0, searching: 1, collecting: 1, screening: 2, reading: 3, locating: 3, assessing: 3, following_up: 4, organizing: 4, complete: 4 };
const actions: Record<string, string> = {
  planning: '正在拆解學習需求與查找方向', searching: '正在依子題查找中英文資料', collecting: '正在取得摘要、導讀或目錄', screening: '正在依摘要初篩來源並保存判斷理由',
  reading: '正在取得正文並核對公開全文與替代入口', locating: '正在定位相關章節與必要上下文', assessing: '正在閱讀重點區段並核對正文依據',
  following_up: '正在檢查涵蓋程度，規劃缺口的下一輪查找', organizing: '正在去重、排序並整理來源用途',
};

export function ResearchProgress({ progress, running, others }: { progress: Progress; running: boolean; others?: Candidate[] }) {
  const current = index[progress.stage] ?? 0;
  const uncovered = progress.topics.filter(topic => ['partial', 'missing'].includes(topic.status));
  const mainGaps = [...new Set(uncovered.map(topic => topic.topic.split(/[：:]/, 1)[0]))];
  const countable = progress.total !== null && progress.total > 0 && progress.completed !== null;
  return <section className="research-progress" aria-label="背景研究進度">
    <div className="research-progress-heading"><strong>{progress.stage === 'complete' ? progress.importable_count ? '研究已完成，可選擇要加入的來源' : '本次未找到適合加入的補充' : !running ? `研究停在：${stages[current]}` : actions[progress.stage] ?? '正在研究'}</strong></div>
    {running && <>
      <progress aria-label="目前研究階段進度" max={countable ? progress.total! : 1} value={countable ? progress.completed! : undefined} />
      <p role="status">{progress.waiting_until && progress.waiting_until * 1000 > Date.now()
        ? `已保存進度，依來源要求等待至 ${new Date(progress.waiting_until * 1000).toLocaleTimeString('zh-TW')} 後接續。`
        : countable ? `${progress.completed!.toLocaleString()} / ${progress.total!.toLocaleString()} ${progress.unit === 'characters' ? '字元已閱讀' : progress.unit === 'queries' ? '組查詢已處理' : '份來源已處理'}` : '此階段尚無可計數的總量，完成後會繼續下一階段。'}</p>
    </>}
    <details className="research-progress-details"><summary>研究詳情</summary>
    <ol aria-label="研究階段">{stages.map((stage, i) => <li key={stage} className={i < current ? 'is-done' : undefined} aria-current={i === current ? 'step' : undefined}><span>{i + 1}</span>{stage}</li>)}</ol>
    <p className="research-progress-counts">搜尋找到 {progress.candidate_count} 份候選 · 初篩評估 {progress.screened_count ?? 0} 份 · 已核對正文 {progress.read_count} 份 · 可選來源 {progress.importable_count} 份</p>
    {progress.stage === 'complete' && <p>本輪排定的查找與核對已結束。以上是實際處理數量，不是全部候選都已讀完；可選來源的用途在各來源的詳細資訊，未列入的項目與原因在下方。</p>}
    {progress.stage !== 'complete' && progress.summary && <p>{progress.summary}</p>}
    {progress.stage === 'complete' && mainGaps.length > 0 && <p>本次尚未完整涵蓋：{mainGaps.join('、')}。</p>}
    {progress.finish_reason === 'references_only' && <p>目前已找到支持內容，但尚無可直接匯入的全文；可先查看原始來源。</p>}
    {others && <OtherResults candidates={others} />}
    {running && <small>研究會在背景繼續，可離開頁面；返回後能查看進度或停止。</small>}
    </details>
  </section>;
}
