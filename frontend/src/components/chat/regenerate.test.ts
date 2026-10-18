import * as React from 'react'
import { createElement } from 'react'
import { renderToString } from 'react-dom/server'
import { beforeEach, expect, test, vi } from 'vitest'
import { MessageList, type MessageListProps } from './MessageList'

beforeEach(() => vi.stubGlobal('React', React))
const messages = [
  { id: 'question', role: 'user', content: '原问题', model: null, created_at: '2026-10-18T00:00:00Z', sources: [] },
  { id: 'old-reply', role: 'assistant', content: '原回复', model: 'synthetic', created_at: '2026-10-18T00:00:01Z', sources: [] },
]
const render = (extra: Partial<MessageListProps> = {}) => renderToString(createElement(MessageList, {
  messages, streamingText: '', stageStages: [], currentStage: null, tools: [], streaming: false,
  streamError: null, onRegenerate: vi.fn(), ...extra,
} as MessageListProps))

test('last assistant offers regeneration with explicit append and quota semantics, keeping old content', () => {
  const html = render()
  expect(html).toContain('重新生成')
  expect(html).toContain('保留原问答，作为新一轮请求重新生成')
  expect(html).toContain('配额')
  expect(html).toContain('原问题')
  expect(html).toContain('原回复')
  expect(html).toContain('old-reply')
  expect(html).toContain('2026')
})

test('read-only view and a trailing user request have no regenerate action', () => {
  expect(render({ onRegenerate: undefined })).not.toContain('重新生成')
  expect(render({ messages: [...messages, { ...messages[0], id: 'pending-user' }] })).not.toContain('重新生成')
})

test('busy or rate-limited regeneration disables the action while preserving the original reply', () => {
  for (const props of [{ regenerateDisabled: true }, { awaiting: true, regenerateDisabled: true }]) {
    const html = render(props)
    expect(html).toContain('disabled=""')
    expect(html).toContain('原回复')
  }
})

test('only the latest assistant gets an action and all saved replies remain distinguishable', () => {
  const html = render({ messages: [...messages, { ...messages[0], id: 'new-question' },
    { ...messages[1], id: 'new-reply', content: '新回复' }] })
  expect(html.match(/>重新生成<\/button>/g)).toHaveLength(1)
  expect(html).toContain('old-reply')
  expect(html).toContain('new-reply')
  expect(html).toContain('原回复')
  expect(html).toContain('新回复')
})
