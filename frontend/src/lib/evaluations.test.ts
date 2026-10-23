import { expect, test } from 'vitest'

import { formatReportMetric, reportStatus } from './evaluations'
import { can } from './permissions'

test('missing, zero, failure and absent verdict remain distinct', () => {
  expect(formatReportMetric({ value: null, unit: 'ratio' })).toBe('缺测')
  expect(formatReportMetric({ value: 0, unit: 'ratio' })).toBe('0.00%')
  expect(reportStatus.failed).toBe('失败')
  expect(reportStatus.unmeasured).toBe('未给出通过结论')
})

test('only approved reader roles have navigation access', () => {
  for (const role of ['system_admin', 'system_viewer', 'tenant_admin'] as const) {
    expect(can(role, 'evaluations', 'read')).toBe(true)
  }
  expect(can('system_admin', 'evaluations', 'write')).toBe(true)
  expect(can('tenant_admin', 'evaluations', 'write')).toBe(true)
  expect(can('system_viewer', 'evaluations', 'write')).toBe(false)
  expect(can('system_admin', 'evaluations', 'delete')).toBe(true)
  expect(can('tenant_admin', 'evaluations', 'delete')).toBe(false)
  for (const role of ['member', 'viewer', undefined] as const) {
    expect(can(role, 'evaluations', 'read')).toBe(false)
  }
})
