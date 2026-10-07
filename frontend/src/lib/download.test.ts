import test from 'node:test'
import assert from 'node:assert/strict'

import {
  SOURCE_FILE_NETWORK_FAILURE,
  contentDispositionFilename,
  saveBlobAsFile,
  sourceFileFailureMessage,
} from './download.ts'

/** 最小 DOM 桩：不引入任何依赖，只记录 saveBlobAsFile 对浏览器 API 的调用序列。 */
interface DomProbe {
  created: string[]
  revoked: string[]
  clicked: { href: string; download: string; attachedWhenClicked: boolean }[]
  restore: () => void
}

function installDomStub(): DomProbe {
  const probe: DomProbe = { created: [], revoked: [], clicked: [], restore: () => {} }
  const g = globalThis as unknown as Record<string, unknown>
  const savedUrl = g.URL
  const savedDocument = g.document

  let seq = 0
  g.URL = {
    ...(savedUrl as object),
    createObjectURL: (_blob: Blob) => {
      const url = `blob:stub/${(seq += 1)}`
      probe.created.push(url)
      return url
    },
    revokeObjectURL: (url: string) => {
      probe.revoked.push(url)
    },
  }

  const makeAnchor = () => {
    const node: Record<string, unknown> = {
      href: '',
      download: '',
      attached: false,
      click() {
        probe.clicked.push({
          href: String(node.href),
          download: String(node.download),
          attachedWhenClicked: Boolean(node.attached),
        })
      },
      remove() {
        node.attached = false
      },
    }
    return node
  }

  g.document = {
    createElement: () => makeAnchor(),
    body: {
      appendChild(node: Record<string, unknown>) {
        node.attached = true
      },
    },
  }

  probe.restore = () => {
    g.URL = savedUrl
    g.document = savedDocument
  }
  return probe
}

test('落盘：文件名取自传入值，点击前已挂到 DOM，点击后节点移除', () => {
  const probe = installDomStub()
  try {
    saveBlobAsFile(new Blob(['x']), 'refund-policy.txt')
  } finally {
    probe.restore()
  }
  assert.equal(probe.clicked.length, 1, '必须真的触发一次点击')
  assert.equal(probe.clicked[0].download, 'refund-policy.txt')
  assert.equal(probe.clicked[0].href, probe.created[0], '点击的 href 应为刚创建的 object URL')
  assert.equal(probe.clicked[0].attachedWhenClicked, true, '必须在挂到 DOM 之后再 click')
})

test('objectURL 用完即释放，与创建次数配平', () => {
  const probe = installDomStub()
  try {
    saveBlobAsFile(new Blob(['a']), 'a.txt')
    saveBlobAsFile(new Blob(['b']), 'b.txt')
  } finally {
    probe.restore()
  }
  assert.equal(probe.created.length, 2)
  assert.deepEqual(probe.revoked, probe.created, 'revoke 的 URL 必须与 create 的一一对应')
})

test('Content-Disposition 文件名解析：优先后端给的权威取值', () => {
  assert.equal(contentDispositionFilename('attachment; filename="note.txt"'), 'note.txt')
  assert.equal(contentDispositionFilename("attachment; filename=note.txt"), 'note.txt')
  assert.equal(contentDispositionFilename("attachment; filename*=UTF-8''%E6%8A%A5%E5%91%8A.txt"), '报告.txt')
  assert.equal(contentDispositionFilename(null), null)
  assert.equal(contentDispositionFilename(''), null)
  assert.equal(contentDispositionFilename('attachment'), null)
})

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
