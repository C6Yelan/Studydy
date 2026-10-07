import type { Candidate } from './SourceCandidates';

export type TaskDraft = { selected: string[]; editing: boolean; sourceQuery: string };
export type ResearchDraft = { query: string; tasks: Record<string, TaskDraft> };

export type ResearchSummary = {
  research_id: string; query: string; selection: string[]; status: string; run_id: string | null;
  candidate_count: number; run_status: string | null; created_at: string;
};
export type Research = {
  research_id: string; material_id: string | null; query: string; mode: string; status: string; candidates: Candidate[];
  selection: string[]; cursor: string | null; error_code: string | null; run_id: string | null; created_at: string;
  is_current_revision?: boolean; deleted?: boolean; run?: { status: string; error_code: string | null };
};
export const researchActive = new Set(['searching', 'acquiring', 'normalizing']);
const labels: Record<string, string> = {
  searching: '搜尋中', selecting: '待選來源', acquiring: '正在取得來源',
  normalizing: '正在整理文件', ready: '待確認加入', submitted: '正在建立教材',
  failed: '需要處理', cancelled: '已停止',
};
export function researchTaskInfo(record: ResearchSummary) {
  if (record.status === 'selecting' && record.candidate_count === 0) return { label: '未找到來源', tone: 'muted', cta: '查看搜尋結果', description: '換個關鍵字再搜尋，這筆查詢紀錄會保留。' };
  if (record.status === 'submitted') {
    if (record.run_status === 'succeeded') return { label: '已完成', tone: 'complete', cta: '查看完成結果', description: '補充來源已加入教材，可查看更新後的學習內容。' };
    if (record.run_status === 'partial') return { label: '部分完成', tone: 'attention', cta: '查看處理結果', description: '教材已部分更新，仍有內容需要確認。' };
    if (['failed', 'cancelled'].includes(record.run_status ?? '')) return { label: record.run_status === 'failed' ? '分析未完成' : '分析已停止', tone: 'attention', cta: '查看處理詳情', description: '已保留選取來源，可查看原因並接續處理。' };
    return { label: '正在建立教材', tone: 'processing', cta: '查看處理進度', description: '來源已送入分析，完成後會更新教材。' };
  }
  const tone = researchActive.has(record.status) ? 'processing' : record.status === 'failed' ? 'attention' : record.status === 'cancelled' ? 'muted' : 'pending';
  const cta = researchActive.has(record.status) ? '查看處理進度' : record.status === 'ready' ? '確認加入來源' : ['failed', 'cancelled'].includes(record.status) ? '接續處理' : '選擇補充來源';
  const description = record.status === 'ready' ? '選取來源已準備好，確認後即可加入教材。' : record.status === 'selecting' ? '挑選想加入教材的來源，再確認加入。' : researchActive.has(record.status) ? '處理結果會保存在這筆查詢，可先進行其他搜尋。' : '開啟這筆查詢，查看已保留的結果與下一步。';
  return { label: labels[record.status] ?? '等待更新', tone, cta, description };
}
export function researchSummary(view: Research): ResearchSummary {
  return { research_id: view.research_id, query: view.query, status: view.status, selection: view.selection,
    run_id: view.run_id, run_status: view.run?.status ?? null, candidate_count: view.candidates.length, created_at: view.created_at };
}
