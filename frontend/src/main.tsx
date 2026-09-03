import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'

// 字体本地打包，避免运行时依赖外部 CDN
import '@fontsource/space-grotesk/400.css'
import '@fontsource/space-grotesk/500.css'
import '@fontsource/space-grotesk/600.css'
import '@fontsource/space-grotesk/700.css'
import '@fontsource/plus-jakarta-sans/400.css'
import '@fontsource/plus-jakarta-sans/500.css'
import '@fontsource/plus-jakarta-sans/600.css'
import './styles/index.css'

import App from './App'
import { ToastProvider } from '@/components/ui/Toast'
import { setSessionExpiredHandler, setTokensRefreshedHandler } from '@/lib/http'
import { useAuthStore } from '@/store/auth'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // 失败只重试一次：后端 4xx（权限/未启用）无需重试
      retry: 1,
      refetchOnWindowFocus: false,
      staleTime: 15_000,
    },
  },
})

// 刷新令牌失效时清空本地会话，由路由守卫跳转登录页
setSessionExpiredHandler(() => {
  useAuthStore.getState().reset()
})

// 令牌自动刷新成功后同步回 store，否则从 store 读令牌的调用方（SSE 流式请求）
// 会一直持有登录时的旧令牌，在 access token 过期后必然 401
setTokensRefreshedHandler((tokens) => {
  useAuthStore.setState({
    access_token: tokens.access_token,
    refresh_token: tokens.refresh_token,
  })
})

// 已存在令牌时先补拉用户信息，避免刷新后短暂显示空侧边栏
void useAuthStore.getState().loadMe()

const container = document.getElementById('root')
if (!container) throw new Error('未找到 #root 挂载点')

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <ToastProvider>
          <App />
        </ToastProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
