import { api } from '@/lib/http'

export interface AttemptFeedback {
  id: string
  outcome: string
  feedback_submitted: boolean
  feedback_id: string | null
}
export interface SavedFeedback {
  id: string
  rating?: 'up' | 'down'
}

export function failureFeedback(attemptId: string, kind: string, comment: string) {
  return api.post<SavedFeedback>('/chat/feedback', { attempt_id: attemptId, kind, comment: comment || null })
}

export function messageRating(messageId: string, rating: 'up' | 'down', reason: string, comment: string) {
  return api.post<SavedFeedback>('/chat/ratings', {
    assistant_message_id: messageId, rating, reason_code: rating === 'down' && reason ? reason : null,
    comment: comment || null,
  })
}

export function removeFeedback(kind: 'failure' | 'rating', id: string) {
  return api.delete<{ deleted: boolean }>(`/chat/${kind === 'failure' ? 'feedback' : 'ratings'}/${encodeURIComponent(id)}`)
}
