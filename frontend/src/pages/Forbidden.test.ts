import * as React from 'react'
import { createElement } from 'react'
import { renderToString } from 'react-dom/server'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, test, vi } from 'vitest'

import { ForbiddenPage } from './Forbidden'

beforeEach(() => {
  vi.stubGlobal('React', React)
})

function render(node: React.ReactElement) {
  return renderToString(createElement(MemoryRouter, null, node))
}

test('shows a full 403 page with a return-to-chat action', () => {
  const html = render(createElement(ForbiddenPage))
  expect(html).toContain('403')
  expect(html).toContain('没有访问权限')
  expect(html).toContain('返回对话')
  expect(html).toContain('href="/chat"')
  expect(html).toContain('role="alert"')
})

test('keeps caller-provided title and description', () => {
  const html = render(
    createElement(ForbiddenPage, {
      title: '当前角色无权管理用户',
      description: '用户列表只对平台管理员开放。',
    }),
  )
  expect(html).toContain('403')
  expect(html).toContain('当前角色无权管理用户')
  expect(html).toContain('用户列表只对平台管理员开放。')
})
