import type { ReportMetric, ReportStatus } from '@/types/api'

export const reportStatus: Record<ReportStatus, string> = {
  passed: '通过', failed: '失败', unmeasured: '未给出通过结论', unsupported: '协议不支持', unavailable: '报告不可用',
}

export const reportMetrics = {
  retrieval: ['recall_at_1', 'recall_at_5', 'recall_at_10', 'mrr', 'ndcg_at_10', 'quote_coverage', 'violation_rate', 'latency_p95_ms'],
  generation: ['answer_point_accuracy', 'citation_accuracy', 'citation_id_validity', 'refusal_correct_rate', 'cases_failed', 'judge_faithfulness', 'judge_correctness', 'cost_yuan', 'external_calls_used'],
  agent: ['route_accuracy', 'followup_recall', 'tool_selection_recall', 'tool_call_validity_recall'],
  workflow: ['completion_rate', 'deterministic_assertion_rate', 'latency_p95_ms'],
}

export function formatReportMetric(metric: Pick<ReportMetric, 'value' | 'unit'>): string {
  if (metric.value === null) return '缺测'
  if (metric.unit === 'ratio') return `${(metric.value * 100).toFixed(2)}%`
  if (metric.unit === 'ms') return `${metric.value.toFixed(2)} ms`
  if (metric.unit === 'CNY') return `¥${metric.value.toFixed(6)}`
  return String(metric.value)
}
