/** 下载当前租户对话原文。成功时浏览器保存 conversations.json。 */

import { exportFailureMessage } from '@/lib/export-message'
import { notifySessionExpired, refreshTokens } from '@/lib/http'
import { getAccessToken } from '@/store/auth'

async function postExport(token: string | null): Promise<Response> {
  return fetch('/api/export/conversations', {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
}

export async function downloadTenantConversations(): Promise<void> {
  let res = await postExport(getAccessToken())
  if (res.status === 401) {
    const next = await refreshTokens()
    if (!next) {
      notifySessionExpired()
      throw new Error('登录状态已失效，请重新登录')
    }
    res = await postExport(next.access_token)
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
  link.download = 'conversations.json'
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}
