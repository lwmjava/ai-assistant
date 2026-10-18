import { useCallback, useEffect, useRef, useState } from 'react'
import { regenerateMessage } from '@/api/chat'

interface Options {
  conversationId: string | null
  refresh: (id: string) => Promise<void>
  onSuccess: () => void
  onError: (error: unknown) => void
  onAbort: () => void
}

/** 独立请求锁和控制器；停止仅停止等待，服务端结果通过历史查询读取。 */
export function useRegenerate(options: Options) {
  const [regenerating, setRegenerating] = useState(false)
  const current = useRef(options)
  current.current = options
  const inFlight = useRef<AbortController | null>(null)
  const cancel = useCallback(() => {
    inFlight.current?.abort()
  }, [])
  const reset = useCallback(() => {
    inFlight.current?.abort()
    inFlight.current = null
    setRegenerating(false)
  }, [])
  useEffect(() => {
    // 删除/切换会话后旧finally不再拥有controller，必须由新视图清等待状态。
    // 卸载仅abort，不向已卸载组件提交状态。
    setRegenerating(false)
    return () => {
      inFlight.current?.abort()
      inFlight.current = null
    }
  }, [options.conversationId])

  const run = useCallback(async (messageId: string) => {
    const start = current.current
    const id = start.conversationId
    if (!id || inFlight.current) return
    const controller = new AbortController()
    inFlight.current = controller
    setRegenerating(true)
    const ownsView = () => inFlight.current === controller && current.current.conversationId === id
    try {
      await regenerateMessage(id, messageId, controller.signal)
      if (!ownsView()) return
      await start.refresh(id)
      if (ownsView()) current.current.onSuccess()
    } catch (error) {
      if (!ownsView()) return
      if (controller.signal.aborted) {
        current.current.onAbort()
      } else {
        current.current.onError(error)
        // 后端失败时可能已写入本轮用户消息，读取实际历史，不猜测回滚结果。
        try { await start.refresh(id) } catch { /* 保留原始错误反馈；查询自身展示加载错误 */ }
      }
    } finally {
      if (inFlight.current === controller) {
        inFlight.current = null
        setRegenerating(false)
      }
    }
  }, [])
  return { regenerating, run, cancel, reset, isRunning: () => inFlight.current !== null }
}
