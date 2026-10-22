import type { MessageOut } from '@/types/api'

export function feedbackKind(message: MessageOut): 'failure' | 'rating' | null {
  if (!message.attempt_id) return null
  if (message.role === 'user' && ['api_error', 'no_response'].includes(message.attempt_outcome ?? '')) return 'failure'
  if (message.role === 'assistant' && message.status === 'complete' && message.attempt_outcome === 'success') return 'rating'
  return null
}
