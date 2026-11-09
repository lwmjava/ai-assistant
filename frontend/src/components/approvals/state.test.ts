import { describe, expect, it } from 'vitest'

import { approvalChoices, visibleApprovals } from './state'

describe('approval inbox state', () => {
  it('keeps the same open item and hides finished ones from the chat strip', () => {
    const rows = [
      { id: 'same', status: 'pending' },
      { id: 'done', status: 'rejected' },
    ]
    expect(visibleApprovals(rows, true).map((row) => row.id)).toEqual(['same'])
    expect(visibleApprovals(rows, false)).toEqual(rows)
  })

  it('does not offer execution after rejection or expiry', () => {
    expect(approvalChoices('pending')).toEqual({ execute: true, reject: true })
    expect(approvalChoices('approved')).toEqual({ execute: true, reject: false })
    expect(approvalChoices('executed')).toEqual({ execute: true, reject: false })
    expect(approvalChoices('rejected')).toEqual({ execute: false, reject: false })
    expect(approvalChoices('expired')).toEqual({ execute: false, reject: false })
    expect(approvalChoices('revoked')).toEqual({ execute: false, reject: false })
  })
})
