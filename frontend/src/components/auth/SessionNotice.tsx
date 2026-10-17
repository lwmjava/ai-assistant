import { useEffect } from 'react'
import { useSessionNotice } from '@/lib/session-notice'

/** HTTP 已撤掉本地会话；全屏说明先于登录表单出现，原因保留在登录页。 */
export function SessionNotice() {
  const suspended = useSessionNotice((s) => s.suspended)
  const message = useSessionNotice((s) => s.message)
  useEffect(() => {
    if (!suspended) return
    const timer = setTimeout(() => useSessionNotice.setState({ suspended: false }), 2500)
    return () => clearTimeout(timer)
  }, [suspended])
  if (!suspended) return null
  return <div role="alertdialog" aria-modal="true" aria-label="租户已停用" className="fixed inset-0 z-[100] grid place-items-center bg-bg p-6"><div className="max-w-lg space-y-4 text-center"><h1 className="text-xl font-semibold">{message}</h1><p className="text-text-muted">正在退出登录…</p><button className="text-primary" onClick={() => useSessionNotice.setState({ suspended: false })}>前往登录</button></div></div>
}
