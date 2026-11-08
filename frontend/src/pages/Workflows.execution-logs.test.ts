import * as React from 'react'
import { createElement } from 'react'
import { renderToString } from 'react-dom/server'
import { beforeEach, expect, test, vi } from 'vitest'

import { AlertsStrip, ExecutionNodeLine, FailureAlertsStrip, PromptStepNotice } from './Workflows'

beforeEach(() => {
  vi.stubGlobal('React', React)
})

test('renders owner suspension and execution failure as separate status regions', () => {
  const owner = renderToString(createElement(AlertsStrip, {
    alerts: [{
      id: 'owner-1',
      tenant_id: 'tenant',
      workflow_id: 'wf',
      workflow_name: '日报',
      owner_id: 'user',
      owner_username: 'alice',
      reason: 'owner_disabled',
      detail: null,
      created_at: '2026-01-01T00:00:00Z',
    }],
  }))
  const failure = renderToString(createElement(FailureAlertsStrip, {
    alerts: [{
      id: 'fail-1',
      tenant_id: 'tenant',
      workflow_id: 'wf',
      workflow_name: '日报',
      execution_id: 'exec',
      trace_id: 'trace',
      reason: 'timeout',
      error_message: 'WORKFLOW_TIMEOUT',
      detail: null,
      created_at: '2026-01-02T00:00:00Z',
    }],
  }))

  expect(owner).toContain('创建者失效已自动停用')
  expect(owner).toContain('创建者已被禁用')
  expect(owner).not.toContain('工作流执行失败')
  expect(failure).toContain('工作流执行失败')
  expect(failure).toContain('执行超时')
  expect(failure).not.toContain('创建者失效已自动停用')
})

test('shows the approved prompt step and does not treat a bad placeholder as a variable', () => {
  const valid = renderToString(createElement(PromptStepNotice, { template: '你好 {customer}' }))
  const invalid = renderToString(createElement(PromptStepNotice, { template: '你好 {1bad}' }))
  expect(valid).toContain('已批准步骤：prompt')
  expect(valid).toContain('customer')
  expect(valid).toContain('不会替换或求值')
  expect(invalid).toContain('不合法')
})

test('locates a failed prompt node separately from a successful one', () => {
  const failed = renderToString(createElement(ExecutionNodeLine, {
    node: { id: 'prompt', type: 'prompt', status: 'failed', failed: true, reason: 'timeout' },
  }))
  const success = renderToString(createElement(ExecutionNodeLine, {
    node: { id: 'prompt', type: 'prompt', status: 'success', failed: false, reason: null },
  }))
  expect(failed).toContain('运行节点')
  expect(failed).toContain('prompt')
  expect(failed).toContain('失败位置')
  expect(failed).toContain('执行超时')
  expect(success).toContain('prompt')
  expect(success).toContain('成功')
  expect(success).not.toContain('失败位置')
})
