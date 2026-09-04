/** 应用外壳：左侧固定导航 + 顶栏 + 内容区。移动端导航转为抽屉。 */

import { useState } from 'react'
import { Outlet } from 'react-router-dom'

import { Sidebar } from './Sidebar'
import { TopBar } from './TopBar'
import { useAuthStore } from '@/store/auth'

export function AppLayout() {
  const [navOpen, setNavOpen] = useState(false)
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)

  return (
    <div className="min-h-screen">
      <Sidebar role={user?.role} open={navOpen} onClose={() => setNavOpen(false)} />
      <div className="lg:pl-64">
        <TopBar user={user} onOpenNav={() => setNavOpen(true)} onLogout={logout} />
        <main className="mx-auto w-full max-w-[1400px] px-3 py-5 sm:px-6 sm:py-7">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
