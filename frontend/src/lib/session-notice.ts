import { create } from 'zustand'
import type { ErrorCode } from './api-errors'

/** 在清会话和路由跳转期间保留原因；不存储请求 ID、原始载荷或令牌。 */
export const useSessionNotice = create<{
  code: ErrorCode | undefined
  message: string | null
  suspended: boolean
}>()(() => ({ code: undefined, message: null, suspended: false }))

export function rememberSessionNotice(code: ErrorCode | undefined, message: string) {
  useSessionNotice.setState({ code, message, suspended: code === 'tenant_suspended' })
}
export function clearSessionNotice() {
  useSessionNotice.setState({ code: undefined, message: null, suspended: false })
}
