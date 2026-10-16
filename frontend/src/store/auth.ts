/** 全局鉴权状态：令牌、当前用户、登录/登出动作。 */

import { create } from 'zustand'
import { persist } from 'zustand/middleware'

import { clearSessionNotice } from '@/lib/session-notice'
import { invalidateAuthSession } from '@/lib/auth-session'
import { api, ApiError } from '@/lib/http'
import type { ChangePasswordRequest, LoginRequest, Token, UserInfo } from '@/types/api'

interface AuthState {
  access_token: string | null
  refresh_token: string | null
  user: UserInfo | null
  /** 首次挂载时用已有令牌拉取用户信息的状态。 */
  bootstrapped: boolean
  login: (payload: LoginRequest) => Promise<void>
  changePassword: (payload: ChangePasswordRequest) => Promise<void>
  /** 注册或初始化向导成功后，用返回的令牌建立会话。 */
  establish: (token: Token) => Promise<void>
  logout: () => void
  loadMe: () => Promise<void>
  reset: () => void
}

// 旧 /me 响应不能覆盖已退出或后来建立的会话。
let sessionRevision = 0

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      access_token: null,
      refresh_token: null,
      user: null,
      bootstrapped: false,

      async login(payload) {
        const token = await api.post<Token>('/auth/login', payload, { anonymous: true })
        await get().establish(token)
      },

      async establish(token) {
        invalidateAuthSession()
        clearSessionNotice()
        const revision = ++sessionRevision
        set({ access_token: token.access_token, refresh_token: token.refresh_token, user: null, bootstrapped: false })
        try {
          const user = await api.get<UserInfo>('/auth/me')
          if (revision !== sessionRevision) return
          set({ user, bootstrapped: true })
        } catch (err) {
          if (revision === sessionRevision) set({ user: null, bootstrapped: true })
          throw err
        }
      },

      async changePassword(payload) {
        const revision = sessionRevision
        const token = await api.post<Token>('/auth/change-password', payload)
        if (revision !== sessionRevision) return
        await get().establish(token)
      },

      logout() {
        invalidateAuthSession()
        sessionRevision += 1
        set({ access_token: null, refresh_token: null, user: null, bootstrapped: false })
      },

      async loadMe() {
        const revision = ++sessionRevision
        if (!get().access_token) {
          set({ bootstrapped: true })
          return
        }
        set({ user: null, bootstrapped: false })
        try {
          const user = await api.get<UserInfo>('/auth/me')
          if (revision !== sessionRevision) return
          set({ user, bootstrapped: true })
        } catch (err) {
          if (revision !== sessionRevision) return
          // 令牌失效：401 已被 http 层处理并清空，这里只兜住其余错误
          if (err instanceof ApiError && err.isUnauthorized) {
            set({ access_token: null, refresh_token: null, user: null, bootstrapped: true })
            return
          }
          set({ user: null, bootstrapped: true })
        }
      },

      reset() {
        invalidateAuthSession()
        sessionRevision += 1
        set({ access_token: null, refresh_token: null, user: null, bootstrapped: true })
      },
    }),
    {
      name: 'aa-auth',
      // 只持久化令牌与用户摘要，避免把派生状态写进 localStorage
      partialize: (state) => ({
        access_token: state.access_token,
        refresh_token: state.refresh_token,
        user: state.user,
      }),
    },
  ),
)

/** 当前访问令牌（供 SSE 请求读取，非响应式场景）。 */
export function getAccessToken(): string | null {
  return useAuthStore.getState().access_token
}
