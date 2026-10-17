import { afterEach, beforeEach, expect, test, vi } from 'vitest'

import { useChatAttachments } from '@/hooks/useChatAttachments'
import { readAttachment } from '@/lib/chat-attachments'
import type { AttachmentDraft } from '@/lib/chat-attachments'
import type { ChatAttachmentInput } from '@/types/api'

const host = vi.hoisted(() => ({ state: [] as AttachmentDraft[], cleanups: [] as (() => void)[] }))
vi.mock('react', () => ({
  useState: (initial: AttachmentDraft[]) => {
    host.state = initial
    return [initial, (next: AttachmentDraft[] | ((previous: AttachmentDraft[]) => AttachmentDraft[])) => {
      host.state = typeof next === 'function' ? next(host.state) : next
    }]
  },
  useRef: (current: unknown) => ({ current }),
  useCallback: (callback: unknown) => callback,
  useEffect: (effect: () => void | (() => void)) => {
    const cleanup = effect()
    if (typeof cleanup === 'function') host.cleanups.push(cleanup)
  },
}))
vi.mock('@/lib/chat-attachments', async (original) => ({
  ...await original<typeof import('@/lib/chat-attachments')>(), readAttachment: vi.fn(),
}))

const file = (name = 'notes.md') => ({ name, size: 3, type: 'text/markdown' }) as File
const deferred = () => {
  let resolve!: (value: ChatAttachmentInput) => void
  const promise = new Promise<ChatAttachmentInput>((complete) => { resolve = complete })
  return { promise, resolve }
}
beforeEach(() => {
  host.state = []
  host.cleanups = []
  vi.mocked(readAttachment).mockReset()
  let sequence = 0
  vi.stubGlobal('crypto', { randomUUID: () => `draft-${++sequence}` })
})
afterEach(() => {
  host.cleanups.forEach((cleanup) => cleanup())
  vi.unstubAllGlobals()
})

test('removing an in-flight draft prevents a late reader from restoring it', async () => {
  const pending = deferred()
  vi.mocked(readAttachment).mockReturnValueOnce(pending.promise)
  const drafts = useChatAttachments()
  drafts.add([file()])
  drafts.remove(host.state[0]!.id)
  pending.resolve({ name: 'notes.md', content_base64: 'YWJj' })
  await pending.promise
  expect(host.state).toEqual([])
})

test('switching conversation invalidates old readers without completing the new draft', async () => {
  const old = deferred()
  const current = deferred()
  vi.mocked(readAttachment).mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise)
  const drafts = useChatAttachments()
  drafts.add([file('old.md')])
  drafts.clear()
  drafts.add([file('new.md')])
  old.resolve({ name: 'old.md', content_base64: 'b2xk' })
  await old.promise
  expect(host.state).toHaveLength(1)
  expect(host.state[0]).toMatchObject({ name: 'new.md', status: 'reading' })
  current.resolve({ name: 'new.md', content_base64: 'bmV3' })
  await current.promise
  expect(host.state[0]).toMatchObject({ name: 'new.md', status: 'ready', content_base64: 'bmV3' })
})

test('stop invalidates pending reads and leaves their errors removable', async () => {
  const pending = deferred()
  vi.mocked(readAttachment).mockReturnValueOnce(pending.promise)
  const drafts = useChatAttachments()
  drafts.add([file()])
  const id = host.state[0]!.id
  drafts.cancelReading()
  pending.resolve({ name: 'notes.md', content_base64: 'YWJj' })
  await pending.promise
  expect(host.state[0]).toMatchObject({ status: 'error' })
  expect(host.state[0]?.content_base64).toBeUndefined()
  drafts.remove(id)
  expect(host.state).toEqual([])
})

test('completion removes only the sent snapshot and preserves newly selected files', async () => {
  vi.mocked(readAttachment).mockResolvedValue({ name: 'notes.md', content_base64: 'YWJj' })
  const drafts = useChatAttachments()
  drafts.add([file()])
  await Promise.resolve()
  const sent = new Set(host.state.map((item) => item.id))
  drafts.add([file('next.md')])
  await Promise.resolve()
  drafts.removeSent(sent)
  expect(host.state).toHaveLength(1)
  expect(host.state[0]?.name).toBe('next.md')
})

test('invalid drafts remain visible and an allowed batch has no file-count cap', async () => {
  vi.mocked(readAttachment).mockResolvedValue({ name: 'notes.md', content_base64: 'YWJj' })
  const drafts = useChatAttachments()
  drafts.add([file('blocked.tsx')])
  expect(readAttachment).not.toHaveBeenCalled()
  expect(host.state[0]).toMatchObject({ name: 'blocked.tsx', status: 'error' })
  drafts.add(Array.from({ length: 35 }, (_, index) => file(`${index}.md`)))
  await Promise.resolve()
  expect(host.state).toHaveLength(36)
  expect(readAttachment).toHaveBeenCalledTimes(35)
})
