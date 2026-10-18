import { beforeEach, expect, test, vi } from 'vitest'
import { useRegenerate } from '@/hooks/useRegenerate'
import { regenerateMessage } from '@/api/chat'

// 最小确定性宿主执行依赖变化的旧cleanup→新setup与卸载顺序，不冒充浏览器E2E。
const host = vi.hoisted(() => ({ state: false, writes: 0, refs: [] as { current: unknown }[], cursor: 0,
  dependency: undefined as unknown, initialized: false, cleanup: undefined as (() => void) | undefined }))
vi.mock('react', () => ({
  useState: () => [host.state, (next: boolean) => { host.state = next; host.writes++ }],
  useRef: (value: unknown) => {
    const index = host.cursor++
    return host.refs[index] ?? (host.refs[index] = { current: value })
  },
  useCallback: (callback: unknown) => callback,
  useEffect: (effect: () => (() => void), dependencies: unknown[]) => {
    if (!host.initialized || host.dependency !== dependencies[0]) {
      host.cleanup?.()
      host.dependency = dependencies[0]
      host.initialized = true
      host.cleanup = effect()
    }
  },
}))
vi.mock('@/api/chat', () => ({ regenerateMessage: vi.fn() }))
const deferred = () => {
  let resolve!: (value: never) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<never>((ok, fail) => { resolve = ok; reject = fail })
  return { promise, resolve: () => resolve(undefined as never), reject }
}
const options = () => ({ conversationId: 'original', refresh: vi.fn().mockResolvedValue(undefined),
  onSuccess: vi.fn(), onError: vi.fn(), onAbort: vi.fn() })
const HookHost = (props: Omit<ReturnType<typeof options>, 'conversationId'> & { conversationId: string | null }) => {
  host.cursor = 0
  return useRegenerate(props)
}
beforeEach(() => {
  host.state = false; host.writes = 0; host.refs = []; host.initialized = false
  host.dependency = undefined; host.cleanup = undefined
  vi.mocked(regenerateMessage).mockReset()
})

test('synchronous lock prevents duplicate requests before React renders busy; success refreshes persisted history', async () => {
  const wait = deferred()
  vi.mocked(regenerateMessage).mockReturnValue(wait.promise)
  const props = options()
  const hook = HookHost(props)
  const first = hook.run('old-assistant')
  const second = hook.run('old-assistant')
  expect(regenerateMessage).toHaveBeenCalledTimes(1)
  expect(host.state).toBe(true)
  expect(props.onSuccess).not.toHaveBeenCalled()
  wait.resolve()
  await Promise.all([first, second])
  expect(props.refresh).toHaveBeenCalledWith('original')
  expect(props.onSuccess).toHaveBeenCalledOnce()
  expect(host.state).toBe(false)
})

test('failure refreshes actual history and releases request lock for retry', async () => {
  const failure = new Error('conflict')
  vi.mocked(regenerateMessage).mockRejectedValue(failure)
  const props = options()
  const hook = HookHost(props)
  await hook.run('old-assistant')
  expect(props.onError).toHaveBeenCalledWith(failure)
  expect(props.refresh).toHaveBeenCalledWith('original')
  expect(props.onSuccess).not.toHaveBeenCalled()
  expect(hook.isRunning()).toBe(false)
  await hook.run('old-assistant')
  expect(regenerateMessage).toHaveBeenCalledTimes(2)
})

test('new conversation invalidates an old completion without changing the new view', async () => {
  const wait = deferred()
  vi.mocked(regenerateMessage).mockReturnValue(wait.promise)
  const old = options()
  const hook = HookHost(old)
  const first = hook.run('old-assistant')
  hook.reset()
  const next = { ...options(), conversationId: 'new-conversation' }
  HookHost(next)
  wait.resolve()
  await first
  expect(old.refresh).not.toHaveBeenCalled()
  expect(old.onSuccess).not.toHaveBeenCalled()
  expect(next.onSuccess).not.toHaveBeenCalled()
  expect(next.onError).not.toHaveBeenCalled()
})

test('stop only aborts waiting and exposes load-history action without claiming backend cancellation', async () => {
  const wait = deferred()
  vi.mocked(regenerateMessage).mockReturnValue(wait.promise)
  const props = options()
  const hook = HookHost(props)
  const first = hook.run('old-assistant')
  const signal = vi.mocked(regenerateMessage).mock.calls[0][2]!
  hook.cancel()
  expect(signal.aborted).toBe(true)
  wait.reject(new DOMException('aborted', 'AbortError'))
  await first
  expect(props.onAbort).toHaveBeenCalledOnce()
  expect(props.onError).not.toHaveBeenCalled()
  expect(props.onSuccess).not.toHaveBeenCalled()
  expect(host.state).toBe(false)
})

test('switch while history refresh is pending cannot notify the new conversation', async () => {
  vi.mocked(regenerateMessage).mockResolvedValue({} as never)
  const wait = deferred()
  const old = options()
  old.refresh.mockReturnValue(wait.promise)
  const hook = HookHost(old)
  const request = hook.run('old-assistant')
  await Promise.resolve()
  hook.reset()
  const next = { ...options(), conversationId: 'new-conversation' }
  HookHost(next)
  wait.resolve()
  await request
  expect(old.onSuccess).not.toHaveBeenCalled()
  expect(next.onSuccess).not.toHaveBeenCalled()
})

test('deleting active conversation aborts the old request and new effect setup clears busy before old promise settles', async () => {
  const wait = deferred()
  vi.mocked(regenerateMessage).mockReturnValue(wait.promise)
  const props = options()
  const hook = HookHost(props)
  const request = hook.run('old-assistant')
  const signal = vi.mocked(regenerateMessage).mock.calls[0][2]!
  expect(host.state).toBe(true)
  const empty = HookHost({ ...options(), conversationId: null })
  expect(signal.aborted).toBe(true)
  expect(empty.isRunning()).toBe(false)
  expect(host.state).toBe(false)
  wait.reject(new DOMException('aborted', 'AbortError'))
  await request
  expect(props.onError).not.toHaveBeenCalled()
  expect(props.onAbort).not.toHaveBeenCalled()
})

test('old completion after effect cleanup cannot release a newer conversation controller or busy state', async () => {
  const oldWait = deferred()
  const nextWait = deferred()
  vi.mocked(regenerateMessage).mockReturnValueOnce(oldWait.promise).mockReturnValueOnce(nextWait.promise)
  const oldProps = options()
  const original = HookHost(oldProps)
  const first = original.run('old-assistant')
  const nextProps = { ...options(), conversationId: 'next' }
  const next = HookHost(nextProps)
  expect(host.state).toBe(false)
  const second = next.run('next-assistant')
  const nextSignal = vi.mocked(regenerateMessage).mock.calls[1][2]!
  oldWait.resolve()
  await first
  expect(host.state).toBe(true)
  expect(next.isRunning()).toBe(true)
  expect(nextSignal.aborted).toBe(false)
  expect(oldProps.onSuccess).not.toHaveBeenCalled()
  nextWait.resolve()
  await second
  expect(nextProps.onSuccess).toHaveBeenCalledOnce()
  expect(host.state).toBe(false)
})

test('unmount cleanup aborts without updating unmounted state or emitting late notices', async () => {
  const wait = deferred()
  vi.mocked(regenerateMessage).mockReturnValue(wait.promise)
  const props = options()
  const hook = HookHost(props)
  const request = hook.run('old-assistant')
  const signal = vi.mocked(regenerateMessage).mock.calls[0][2]!
  const writes = host.writes
  host.cleanup?.()
  expect(signal.aborted).toBe(true)
  wait.reject(new DOMException('aborted', 'AbortError'))
  await request
  expect(host.writes).toBe(writes)
  expect(props.onAbort).not.toHaveBeenCalled()
  expect(props.onError).not.toHaveBeenCalled()
})
