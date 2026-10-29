import type { Candidate } from './SourceCandidates';

export type TaskDraft = { selected: string[]; editing: boolean; sourceQuery: string };
export type ResearchDraft = { query: string; tasks: Record<string, TaskDraft> };

export type ResearchSummary = {
  research_id: string; query: string; selection: string[]; status: string; run_id: string | null;
  candidate_count: number; run_status: string | null; created_at: string;
};
// 後端只送畫面用到的欄位；candidates 只含可選或已選的來源。
export type Research = {
  research_id: string; material_id: string | null; query: string; status: string; candidates: Candidate[];
  selection: string[]; error_code: string | null; run_id: string | null; created_at: string;
  is_current_revision?: boolean | null; deleted?: boolean; run?: { status: string; output_binding: { knowledge_structure_revision?: string } | null } | null;
  progress?: ResearchProgress | null;
  approved_scope?: {title:string;topics:string[];goals:string[]}|null;
};
export type ResearchProgress = {
  stage: string;
  topic_progress?: { topic_index: number; topic: string; status: "waiting" | "searching" | "reading" | "completed"; available_source_count: number }[];
  completed_topics?: number;
  total_topics?: number;
  started_at?: string | null;
  summary: string; finish_reason: string | null; waiting_until: number | null;
  topics: { topic: string; status: string }[];
};
export const researchActive = new Set(['searching', 'acquiring', 'normalizing']);
const labels: Record<string, string> = {
  searching: '搜尋中', selecting: '待選來源', acquiring: '正在取得來源',
  normalizing: '正在整理文件', ready: '待確認加入', submitted: '正在建立教材',
  failed: '需要處理', cancelled: '已停止',
};
export function researchTaskInfo(record: ResearchSummary) {
  if (record.status === 'selecting' && record.candidate_count === 0) return { label: '未找到來源', tone: 'muted', cta: '查看搜尋結果', description: '這次沒有找到可加入的來源，可重新確認搜尋範圍。' };
  if (record.status === 'submitted') {
    if (record.run_status === 'succeeded') return { label: '已完成', tone: 'complete', cta: '查看完成結果', description: '補充來源已加入教材，可查看更新後的學習內容。' };
    if (record.run_status === 'partial') return { label: '教材可查看', tone: 'complete', cta: '查看補充結果', description: '已產生補充內容，可查看知識地圖與原文來源。' };
    if (['failed', 'cancelled'].includes(record.run_status ?? '')) return { label: record.run_status === 'failed' ? '分析未完成' : '分析已停止', tone: 'attention', cta: '查看處理詳情', description: '已保留選取來源，可查看原因並接續處理。' };
    return { label: '正在建立教材', tone: 'processing', cta: '查看處理進度', description: '來源已送入分析，完成後會更新教材。' };
  }
  const tone = researchActive.has(record.status) ? 'processing' : record.status === 'failed' ? 'attention' : record.status === 'cancelled' ? 'muted' : 'pending';
  const cta = researchActive.has(record.status) ? '查看處理進度' : record.status === 'ready' ? '確認加入來源' : ['failed', 'cancelled'].includes(record.status) ? '接續處理' : '選擇補充來源';
  const description = record.status === 'ready' ? '來源已準備，但尚未納入目前知識地圖；確認加入後才會分析。' : record.status === 'selecting' ? '挑選想加入教材的來源，再確認加入。' : researchActive.has(record.status) ? '處理結果會保存在這筆查詢，可先進行其他搜尋。' : record.status === 'cancelled' ? '此查詢已停止，結果已保留；可接續處理未完成的部分。' : '開啟這筆查詢，查看已保留的結果與下一步。';
  return { label: labels[record.status] ?? '等待更新', tone, cta, description };
}
