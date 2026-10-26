import { expect, test } from 'vitest'

import { formatReportMetric, reportStatus } from './evaluations'
import { can } from './permissions'

test('missing, zero, failure and absent verdict remain distinct', () => {
  expect(formatReportMetric({ value: null, unit: 'ratio' })).toBe('缺测')
  expect(formatReportMetric({ value: 0, unit: 'ratio' })).toBe('0.00%')
  expect(formatReportMetric({ value: 0.00014, unit: 'CNY' })).toBe('¥0.000140')
  expect(reportStatus.failed).toBe('失败')
  expect(reportStatus.unmeasured).toBe('未给出通过结论')
})

test('only approved reader roles have navigation access', () => {
  expect(can('system_admin', 'evaluations', 'read')).toBe(true)
  expect(can('system_admin', 'evaluations', 'write')).toBe(true)
  expect(can('system_admin', 'evaluations', 'delete')).toBe(true)
  for (const role of ['system_viewer', 'tenant_admin', 'member', 'viewer', undefined] as const) {
    expect(can(role, 'evaluations', 'read')).toBe(false)
    expect(can(role, 'evaluations', 'write')).toBe(false)
    expect(can(role, 'evaluations', 'delete')).toBe(false)
  }
})
