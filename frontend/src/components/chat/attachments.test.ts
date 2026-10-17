import * as React from 'react'
import { createElement } from 'react'
import { renderToString } from 'react-dom/server'
import { beforeEach, expect, test, vi } from 'vitest'

import { Composer } from './Composer'
import { MessageList } from './MessageList'

beforeEach(() => vi.stubGlobal('React', React))

const composer = {
  value: 'question', onChange: vi.fn(), onSubmit: vi.fn(), streaming: false,
  mode: 'stream' as const, onModeChange: vi.fn(),
}

test('composer offers multiple file selection with the precise attachment whitelist', () => {
  const html = renderToString(createElement(Composer, composer))
  expect(html).toContain('type="file"')
  expect(html).toContain('multiple=""')
  expect(html).toContain('.png,.jpg,.jpeg,.md,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.pdf,.go,.py,.css,.html,.htm,.java,.js,.json,.xml,.yaml,.yml,.c,.h,.cpp,.cc,.cxx,.hpp,.vue,.ts')
  expect(html).not.toContain('.tsx')
})

test('rate limit disables send and shows the remaining seconds on the button', () => {
  const html = renderToString(createElement(Composer, { ...composer, sendLocked: true, retryLeft: 8 }))
  expect(html).toContain('8 秒后可重试')
  expect(html).toContain('disabled=""')
  expect(html).not.toContain('aria-label="发送消息"')
})

test('invalid drafts remain visible and removable instead of being silently discarded', () => {
  const props = { ...composer, attachments: [{ id: 'draft', name: 'blocked.tsx', size: 10,
    mime_type: 'text/plain', status: 'error' as const, error: '不支持此附件类型' }],
  onRemoveAttachment: vi.fn(), onFiles: vi.fn() }
  const html = renderToString(createElement(Composer, props))
  expect(html).toContain('blocked.tsx')
  expect(html).toContain('不支持此附件类型')
  expect(html).toContain('aria-label="移除 blocked.tsx"')
})

test('persisted message renders attachment summaries without embedding binary payloads', () => {
  const html = renderToString(createElement(MessageList, {
    messages: [{ id: 'user-message', role: 'user', content: 'question', model: null,
      created_at: '2026-10-16T00:00:00Z', sources: [], attachments: [
        { name: 'notes.md', size: 1024, mime_type: 'text/markdown' },
      ] }], streamingText: '', stageStages: [], currentStage: null, tools: [], streaming: false, streamError: null,
  }))
  expect(html).toContain('notes.md')
  expect(html).toContain('1.0 KB')
  expect(html).not.toContain('content_base64')
})
