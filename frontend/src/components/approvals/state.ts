/** 只根据服务端状态决定按钮，不在页面上重新计算风险或权限。 */

export interface ApprovalEvent {
  id: string
  event: string
  status: string
  subject: string
  parameter_summary: string
  expires_at: string
  execution_count: number
  result_code: string
}

const OPEN = new Set(['pending', 'approved'])

export function visibleApprovals<T extends { status: string }>(rows: T[], openOnly: boolean): T[] {
  if (!openOnly) return rows
  return rows.filter((row) => OPEN.has(row.status))
}

export function approvalChoices(status: string): { execute: boolean; reject: boolean } {
  if (status === 'pending') return { execute: true, reject: true }
  if (status === 'approved' || status === 'executed') return { execute: true, reject: false }
  return { execute: false, reject: false }
}

export function approvalStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    pending: '待处理',
    approved: '已批准，尚未执行',
    executed: '已执行',
    rejected: '已拒绝',
    expired: '已过期',
    revoked: '已撤销',
    cancelled: '已取消',
  }
  return labels[status] ?? status
}
