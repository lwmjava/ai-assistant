/**
 * 权限镜像的回归守护。
 *
 * 后端 `app/rag/access.py::can_control_document` 仍是真正的判定者，这里的镜像
 * 只决定「下载源文件入口要不要显示」。但它是「现有管理权限不变」这条验收在
 * 前端侧的唯一表达，改坏了必须立刻变红——为此逐条覆盖后端那五条判定。
 */

import test from 'node:test'
import assert from 'node:assert/strict'

import { can, canControlDocument } from './permissions.ts'
import type { DocumentOut, UserInfo } from '../types/api.ts'

function makeUser(over: Partial<UserInfo> = {}): UserInfo {
  return {
    id: 'u-1',
    tenant_id: 't-1',
    username: 'tester',
    email: null,
    role: 'member',
    is_active: true,
    ...over,
  }
}

function makeDoc(over: Partial<DocumentOut> = {}): DocumentOut {
  return {
    id: 'd-1',
    tenant_id: 't-1',
    user_id: 'u-1',
    title: '文档',
    source: 'note.txt',
    is_current: true,
    version_state: 'draft',
    deleted_at: null,
    chunk_count: 1,
    created_at: '2026-10-07T00:00:00Z',
    updated_at: '2026-10-07T00:00:00Z',
    ...over,
  }
}

test('未登录一律不可控', () => {
  assert.equal(canControlDocument(makeDoc(), null), false)
  assert.equal(canControlDocument(makeDoc({ user_id: 'u-1' }), null), false)
})

test('系统管理员：跨租户也可控', () => {
  const admin = makeUser({ id: 'admin', role: 'system_admin' })
  assert.equal(canControlDocument(makeDoc(), admin), true)
  assert.equal(canControlDocument(makeDoc({ tenant_id: 't-other', user_id: 'u-9' }), admin), true)
})

test('跨租户一律拒绝（系统管理员除外）', () => {
  const tenantAdmin = makeUser({ id: 'ta', role: 'tenant_admin' })
  const member = makeUser({ id: 'u-1' })
  assert.equal(canControlDocument(makeDoc({ tenant_id: 't-other' }), tenantAdmin), false)
  assert.equal(canControlDocument(makeDoc({ tenant_id: 't-other' }), member), false)
})

test('同租户租户管理员：他人的文档也可控', () => {
  const tenantAdmin = makeUser({ id: 'ta', role: 'tenant_admin' })
  assert.equal(canControlDocument(makeDoc({ user_id: 'u-9' }), tenantAdmin), true)
  assert.equal(canControlDocument(makeDoc({ user_id: 'u-9', is_current: false }), tenantAdmin), true)
})

test('成员：只有本人上传且为当前版才可控', () => {
  const member = makeUser({ id: 'u-1' })
  assert.equal(canControlDocument(makeDoc({ user_id: 'u-1', is_current: true }), member), true)
  assert.equal(canControlDocument(makeDoc({ user_id: 'u-2', is_current: true }), member), false)
  // 历史版（is_current=false）不在成员的控制面内
  assert.equal(canControlDocument(makeDoc({ user_id: 'u-1', is_current: false }), member), false)
})

test('控制面只看身份关系，不看角色权限矩阵（read 权限另由 can() 把关）', () => {
  // 与后端一致：viewer 本人上传且为当前版，can_control_document 同样放行；
  // 入口的最终条件是 Knowledge.tsx 里的 can(read) && canControlDocument(...)，
  // 对应后端 require_permission("knowledge_bases","read") + can_control_document 的合取。
  const viewer = makeUser({ id: 'v', role: 'viewer' })
  assert.equal(canControlDocument(makeDoc({ user_id: 'v', is_current: true }), viewer), true)
  assert.equal(canControlDocument(makeDoc({ user_id: 'u-2', is_current: true }), viewer), false)
  assert.equal(can('viewer', 'knowledge_bases', 'read'), true)
  assert.equal(can('viewer', 'knowledge_bases', 'delete'), false)
})
