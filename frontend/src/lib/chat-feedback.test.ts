import { expect, test } from 'vitest'

import { feedbackKind } from './chat-feedback'
import type { MessageOut } from '@/types/api'

const message: MessageOut = { id: 'm', role: 'assistant', content: '', model: null, created_at: '',
  attempt_id: 'a', attempt_outcome: 'success', status: 'complete' }

test('only completed successful assistant messages may be rated', () => {
  expect(feedbackKind(message)).toBe('rating')
  expect(feedbackKind({ ...message, status: 'stopped' })).toBeNull()
  expect(feedbackKind({ ...message, attempt_outcome: 'pending' })).toBeNull()
  expect(feedbackKind({ ...message, attempt_id: null })).toBeNull()
})

test('only explicit server failure outcomes permit failure feedback', () => {
  for (const outcome of ['api_error', 'no_response']) {
    expect(feedbackKind({ ...message, role: 'user', attempt_outcome: outcome })).toBe('failure')
  }
  for (const outcome of ['pending', 'success', 'stopped']) {
    expect(feedbackKind({ ...message, role: 'user', attempt_outcome: outcome })).toBeNull()
  }
})
