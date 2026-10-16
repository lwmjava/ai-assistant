import { expect, test } from 'vitest'

import { emptyStreamSnapshot, reduceStreamSnapshot } from '@/hooks/useChatStream'

const runId = '11111111222243338444555555555555'
const event = (name: string, data: Record<string, unknown>) => ({
  event: name, data: JSON.stringify({ run_id: runId, ...data }),
})

test('规范事件展示意图、步骤、独立工具成败、引用与增量文本', () => {
  let state = emptyStreamSnapshot()
  state = reduceStreamSnapshot(state, event('run.started', {}))
  state = reduceStreamSnapshot(state, event('intent.classified', { intent: 'analysis', shortcut: false }))
  state = reduceStreamSnapshot(state, event('plan.generated', { steps: [{ description: '核对输入' }, '计算结果'] }))
  state = reduceStreamSnapshot(state, event('tool.call', { call_id: 'call-one', tool_name: 'calculator', args_summary: '已脱敏' }), 0)
  state = reduceStreamSnapshot(state, event('tool.call', { call_id: 'call-two', tool_name: 'calculator' }), 1000)
  state = reduceStreamSnapshot(state, event('tool.result', { call_id: 'call-two', tool_name: 'calculator', ok: false, result_summary: '计算失败' }), 2000)
  state = reduceStreamSnapshot(state, event('tool.result', { call_id: 'call-one', tool_name: 'calculator', ok: true, result_summary: '计算完成' }), 3000)
  state = reduceStreamSnapshot(state, event('citations', { sources: [{ filename: '说明.md', excerpt: '摘录', page: 1, chunk_id: 'chunk-one' }] }))
  state = reduceStreamSnapshot(state, event('token', { delta: '答案' }))
  state = reduceStreamSnapshot(state, event('run.finished', { status: 'done' }))
  expect(state).toMatchObject({ streaming: false, intent: 'analysis', planSteps: ['核对输入', '计算结果'], text: '答案' })
  expect(state.toolCalls.map((call) => call.status)).toEqual(['done', 'failed'])
  expect(state.sources[0]).toMatchObject({ excerpt: '摘录', chunk_id: 'chunk-one' })
})

test('短路省略阶段，异运行与损坏事件不能拼入回复，终态失败保留错误', () => {
  let state = reduceStreamSnapshot(emptyStreamSnapshot(), event('node.started', { node: 'understanding' }))
  state = reduceStreamSnapshot(state, event('intent.classified', { intent: 'direct', shortcut: true }))
  state = reduceStreamSnapshot(state, event('node.started', { node: 'respond' }))
  expect(state.stages).toEqual([])
  expect(reduceStreamSnapshot(state, { event: 'token', data: '{invalid' })).toBe(state)
  expect(reduceStreamSnapshot(state, event('token', { run_id: 'another-run', delta: '不应出现' }))).toBe(state)
  state = reduceStreamSnapshot(state, event('error', { message: '服务暂时不可用', retryable: true }))
  state = reduceStreamSnapshot(state, event('run.finished', { status: 'failed' }))
  expect(state).toMatchObject({ error: '服务暂时不可用', retryable: true, streaming: false, text: '' })
})

test('取消后的未确认工具不再显示执行中，也不伪称已成功', () => {
  let state = reduceStreamSnapshot(emptyStreamSnapshot(), event('tool.call', { call_id: 'in-flight', tool_name: 'probe' }))
  state = reduceStreamSnapshot(state, event('run.finished', { status: 'cancelled' }))
  expect(state.stoppedByUser).toBe(true)
  expect(state.toolCalls[0]?.status).toBe('interrupted')
  expect(state.tools[0]).toContain('结果未确认')
})
