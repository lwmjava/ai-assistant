import { afterEach, expect, test, vi } from 'vitest'

import { ATTACHMENT_EXTENSIONS, MAX_ATTACHMENT_BYTES, attachmentError, readAttachment } from './chat-attachments'

afterEach(() => vi.unstubAllGlobals())

test.each(ATTACHMENT_EXTENSIONS)('the declared %s extension accepts the exact byte limit', (extension) => {
  expect(attachmentError({ name: `document.${extension}`, size: MAX_ATTACHMENT_BYTES })).toBeNull()
  expect(attachmentError({ name: `document.${extension}`, size: MAX_ATTACHMENT_BYTES + 1 })).toContain('1048576')
})

test.each(['file.tsx', 'file.jsx', 'file.txt', 'file.gif', 'image.png.exe', '.png', 'no-extension'])(
  'undeclared type %s is rejected before reading', (name) => {
    expect(attachmentError({ name, size: 1 })).toContain('不支持')
  },
)

test('data URL reading sends only base64 bytes and the original display name', async () => {
  class Reader {
    result = 'data:text/markdown;base64,c3ludGhldGlj'
    onload?: () => void
    readAsDataURL() { this.onload?.() }
  }
  vi.stubGlobal('FileReader', Reader)
  const file = { name: 'notes.md', size: 9 } as File
  await expect(readAttachment(file)).resolves.toEqual({ name: 'notes.md', content_base64: 'c3ludGhldGlj' })
})

test('a read failure exposes a fixed message without forwarding browser error details', async () => {
  class Reader {
    onerror?: () => void
    readAsDataURL() { this.onerror?.() }
  }
  vi.stubGlobal('FileReader', Reader)
  await expect(readAttachment({ name: 'notes.md', size: 1 } as File)).rejects.toThrow('读取附件失败')
})

test('unsupported files never instantiate a reader', async () => {
  const reader = vi.fn()
  vi.stubGlobal('FileReader', reader)
  await expect(readAttachment({ name: 'notes.tsx', size: 1 } as File)).rejects.toThrow('不支持')
  expect(reader).not.toHaveBeenCalled()
})
