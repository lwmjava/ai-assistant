/** 全局鉴权状态：令牌、当前用户、登录/登出动作。 */

import { create } from 'zustand'
import { persist } from 'zustand/middleware'

import { api, ApiError } from '@/lib/http'
import type { LoginRequest, Token, UserInfo } from '@/types/api'

interface AuthState {
  access_token: string | null
  refresh_token: string | null
  user: UserInfo | null
  /** 首次挂载时用已有令牌拉取用户信息的状态。 */
  bootstrapped: boolean
  login: (payload: LoginRequest) => Promise<void>
  logout: () => void
  loadMe: () => Promise<void>
  reset: () => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      access_token: null,
      refresh_token: null,
      user: null,
      bootstrapped: false,

      async login(payload) {
        const token = await api.post<Token>('/auth/login', payload, { anonymous: true })
        set({ access_token: token.access_token, refresh_token: token.refresh_token })
        // 登录成功后立刻补全用户信息，供侧边栏与权限判定使用
        const user = await api.get<UserInfo>('/auth/me')
        set({ user, bootstrapped: true })
      },

      logout() {
        set({ access_token: null, refresh_token: null, user: null, bootstrapped: false })
      },

      async loadMe() {
        if (!get().access_token) {
          set({ bootstrapped: true })
          return
        }
        try {
          const user = await api.get<UserInfo>('/auth/me')
          set({ user, bootstrapped: true })
        } catch (err) {
          // 令牌失效：401 已被 http 层处理并清空，这里只兜住其余错误
          if (err instanceof ApiError && err.isUnauthorized) {
            set({ access_token: null, refresh_token: null, user: null, bootstrapped: true })
            return
          }
          set({ bootstrapped: true })
        }
      },

      reset() {
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
