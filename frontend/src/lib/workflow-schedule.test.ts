import { describe, expect, it } from 'vitest'

import { MANUAL_ONLY_NOTICE, workflowScheduleNotice } from './workflow-schedule'

describe('workflowScheduleNotice', () => {
  it('shows the manual-only sentence when croniter is missing', () => {
    expect(workflowScheduleNotice('manual-only')).toBe(MANUAL_ONLY_NOTICE)
    expect(workflowScheduleNotice('manual-only')).toContain('只剩手动执行')
  })

  it('keeps the existing copy for a running scheduler, a missing header, and the disabled switch', () => {
    expect(workflowScheduleNotice('scheduled')).toBeNull()
    expect(workflowScheduleNotice(null)).toBeNull()
    expect(workflowScheduleNotice('disabled')).toBeNull()
  })
})
