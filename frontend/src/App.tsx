/** 应用根组件：路由表 + 鉴权/权限守卫。 */

import { lazy, Suspense, useEffect, useState } from 'react'
import type { ReactElement, ReactNode } from 'react'
import { Navigate, Outlet, Route, Routes, useLocation } from 'react-router-dom'

import { AppLayout } from '@/components/layout/AppLayout'
import { api } from '@/lib/http'
import { can } from '@/lib/permissions'
import { useAuthStore } from '@/store/auth'
import type { Role, SetupStatus } from '@/types/api'

// 登录页随首屏加载；功能页按路由拆分，避免首屏拖入 Markdown 解析器等重依赖
import LoginPage from '@/pages/Login'
import RegisterPage from '@/pages/Register'
import SetupPage from '@/pages/Setup'
const ChatPage = lazy(() => import('@/pages/Chat'))
const KnowledgePage = lazy(() => import('@/pages/Knowledge'))
const ToolsPage = lazy(() => import('@/pages/Tools'))
const WorkflowsPage = lazy(() => import('@/pages/Workflows'))
const SkillsPage = lazy(() => import('@/pages/Skills'))
const AuditPage = lazy(() => import('@/pages/Audit'))
const TenantsPage = lazy(() => import('@/pages/Tenants'))
const TenantCreatePage = lazy(() => import('@/pages/TenantCreate'))
const UsersPage = lazy(() => import('@/pages/Users'))
const UserCreatePage = lazy(() => import('@/pages/UserCreate'))
const StatusPage = lazy(() => import('@/pages/Status'))
const InvitationsPage = lazy(() => import('@/pages/Invitations'))

/** 路由切换时的降级视图：保持布局稳定，避免白屏。 */
function PageFallback() {
  return (
    <div className="space-y-5" aria-busy="true" aria-label="页面加载中">
      <div className="space-y-2">
        <div className="skeleton h-8 w-48 rounded-lg" />
        <div className="skeleton h-4 w-72 rounded-md" />
      </div>
      <div className="panel p-4">
        <div className="space-y-3">
          <div className="skeleton h-4 w-1/3 rounded-md" />
          <div className="skeleton h-4 w-2/3 rounded-md" />
          <div className="skeleton h-4 w-1/2 rounded-md" />
        </div>
      </div>
    </div>
  )
}

function Lazy({ children }: { children: ReactNode }) {
  return <Suspense fallback={<PageFallback />}>{children}</Suspense>
}

/** 未登录时跳转登录页，并记录来源路径以便登录后回跳。 */
function RequireAuth({ children }: { children: ReactElement }) {
  const location = useLocation()
  const token = useAuthStore((s) => s.access_token)
  if (!token) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }
  return children
}

/** 资源级守卫：角色不在后端权限矩阵内时重定向到默认页（后端仍会二次校验）。 */
function RequirePermission({
  resource,
  action,
  children,
}: {
  resource: string
  action: 'read' | 'write' | 'delete'
  children: ReactElement
}) {
  const role = useAuthStore((s) => s.user?.role)
  if (!can(role, resource, action)) return <Navigate to="/chat" replace />
  return children
}

/** 未登录且库中没有系统管理员时，只允许进入一次性向导。 */
function FirstRunGate() {
  const token = useAuthStore((s) => s.access_token)
  const location = useLocation()
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(token ? false : null)

  useEffect(() => {
    if (token) {
      setNeedsSetup(false)
      return
    }
    let cancelled = false
    api
      .get<SetupStatus>('/auth/setup-status', { anonymous: true })
      .then((body) => {
        if (!cancelled) setNeedsSetup(body.needs_setup)
      })
      .catch(() => {
        if (!cancelled) setNeedsSetup(false)
      })
    return () => {
      cancelled = true
    }
  }, [token])

  if (needsSetup === null) {
    return (
      <div
        aria-busy="true"
        aria-label="正在检查初始化状态"
        className="grid min-h-screen place-items-center text-sm text-text-muted"
      >
        正在检查是否需要初始化…
      </div>
    )
  }
  if (needsSetup && location.pathname !== '/setup') {
    return <Navigate to="/setup" replace />
  }
  if (!needsSetup && location.pathname === '/setup') {
    return <Navigate to={token ? '/chat' : '/login'} replace />
  }
  return <Outlet />
}

/** 根路径：按角色落到第一个可访问的功能页。 */
function HomeRedirect() {
  const role = useAuthStore((s) => s.user?.role) as Role | undefined
  if (can(role, 'conversations', 'read')) return <Navigate to="/chat" replace />
  if (can(role, 'knowledge_bases', 'read')) return <Navigate to="/knowledge" replace />
  if (can(role, 'agents', 'read')) return <Navigate to="/tools" replace />
  return <Navigate to="/login" replace />
}

function NotFound() {
  return (
    <div className="panel mx-auto max-w-md">
      <div className="px-6 py-12 text-center">
        <p className="font-display text-4xl font-semibold text-text">404</p>
        <p className="mt-2 text-sm text-text-muted">页面不存在或无权访问</p>
      </div>
    </div>
  )
}

export default function App() {
  return (
    <Routes>
      <Route element={<FirstRunGate />}>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route path="/setup" element={<SetupPage />} />
      <Route
        element={
          <RequireAuth>
            <AppLayout />
          </RequireAuth>
        }
      >
        <Route path="/" element={<HomeRedirect />} />
        <Route
          path="/chat"
          element={
            <RequirePermission resource="conversations" action="read">
              <Lazy>
                <ChatPage />
              </Lazy>
            </RequirePermission>
          }
        />
        <Route
          path="/knowledge"
          element={
            <RequirePermission resource="knowledge_bases" action="read">
              <Lazy>
                <KnowledgePage />
              </Lazy>
            </RequirePermission>
          }
        />
        <Route
          path="/tools"
          element={
            <RequirePermission resource="agents" action="read">
              <Lazy>
                <ToolsPage />
              </Lazy>
            </RequirePermission>
          }
        />
        <Route
          path="/workflows"
          element={
            <RequirePermission resource="workflows" action="read">
              <Lazy>
                <WorkflowsPage />
              </Lazy>
            </RequirePermission>
          }
        />
        <Route
          path="/skills"
          element={
            <RequirePermission resource="skills" action="read">
              <Lazy>
                <SkillsPage />
              </Lazy>
            </RequirePermission>
          }
        />
        <Route
          path="/audit"
          element={
            <Lazy>
              <AuditPage />
            </Lazy>
          }
        />
        <Route
          path="/invitations"
          element={
            <Lazy>
              <InvitationsPage />
            </Lazy>
          }
        />
        <Route
          path="/tenants/new"
          element={
            <Lazy>
              <TenantCreatePage />
            </Lazy>
          }
        />
        <Route
          path="/tenants"
          element={
            <Lazy>
              <TenantsPage />
            </Lazy>
          }
        />
        <Route
          path="/users/new"
          element={
            <Lazy>
              <UserCreatePage />
            </Lazy>
          }
        />
        <Route
          path="/users"
          element={
            <Lazy>
              <UsersPage />
            </Lazy>
          }
        />
        <Route
          path="/status"
          element={
            <Lazy>
              <StatusPage />
            </Lazy>
          }
        />
        <Route path="*" element={<NotFound />} />
      </Route>
      </Route>
    </Routes>
  )
}
