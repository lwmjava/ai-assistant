import test from 'node:test'
import assert from 'node:assert/strict'

import { SOURCE_FILE_NETWORK_FAILURE, sourceFileFailureMessage } from './download.ts'

test('401：提示会话失效，需要重新登录', () => {
  const msg = sourceFileFailureMessage(401)
  assert.match(msg, /登录状态已失效/)
})

test('403：提示无权下载，并说明谁能下载', () => {
  const msg = sourceFileFailureMessage(403)
  assert.match(msg, /无权下载/)
  assert.match(msg, /上传者本人/)
})

test('404：提示源文件不存在（后端 detail 带「源文件」时原样带上）', () => {
  const msg = sourceFileFailureMessage(404, { detail: '源文件不存在' })
  assert.match(msg, /源文件不存在/)

  const generic = sourceFileFailureMessage(404)
  assert.match(generic, /源文件不存在/)
  assert.match(generic, /不是通过文件上传创建|已被清理/)
})

test('5xx：提示服务暂时不可用', () => {
  assert.match(sourceFileFailureMessage(500), /服务暂时不可用/)
  assert.match(sourceFileFailureMessage(502), /服务暂时不可用/)
  assert.match(sourceFileFailureMessage(503), /服务暂时不可用/)
})

test('网络失败（状态码 0）：提示网络异常', () => {
  assert.match(sourceFileFailureMessage(SOURCE_FILE_NETWORK_FAILURE), /网络异常/)
})

test('其它状态码：给出兜底的下载失败提示', () => {
  assert.match(sourceFileFailureMessage(400), /下载失败/)
  assert.match(sourceFileFailureMessage(418), /下载失败/)
})

test('每种失败都有独立中文提示，不会互相混淆', () => {
  const messages = [401, 403, 404, 500, SOURCE_FILE_NETWORK_FAILURE].map((s) =>
    sourceFileFailureMessage(s),
  )
  assert.equal(new Set(messages).size, messages.length)
  for (const msg of messages) assert.ok(msg.length > 0)
})
