/** 下载当前租户对话原文或 ShareGPT 训练格式。 */

import { exportFailureMessage } from '@/lib/export-message'
import { API_BASE, notifySessionExpired, refreshTokens } from '@/lib/http'
import { getAccessToken } from '@/store/auth'

async function postExport(path: string, token: string | null): Promise<Response> {
  return fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
}

async function downloadExport(path: string, filename: string): Promise<void> {
  let res = await postExport(path, getAccessToken())
  if (res.status === 401) {
    const next = await refreshTokens()
    if (!next) {
      notifySessionExpired()
      throw new Error('登录状态已失效，请重新登录')
    }
    res = await postExport(path, next.access_token)
  }
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    let payload: unknown = text
    if (text) {
      try {
        payload = JSON.parse(text) as unknown
      } catch {
        payload = text
      }
    }
    throw new Error(exportFailureMessage(res.status, payload))
  }
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

export async function downloadTenantConversations(): Promise<void> {
  await downloadExport('/export/conversations', 'conversations.json')
}

export async function downloadTenantConversationsSharegpt(): Promise<void> {
  await downloadExport('/export/conversations/sharegpt', 'conversations_sharegpt.json')
}
