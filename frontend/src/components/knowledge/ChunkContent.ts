/**
 * 分块正文展示组件：搜索结果正文与文档分块正文共用同一处渲染。
 *
 * 结构化正文（盒图 / 代码 / 缩进结构）用等宽 + `whitespace-pre` + 横向滚动，
 * 普通段落保持自动换行；判定见 `chunkContentClassName`。
 *
 * 为什么是 `createElement` 而不是 JSX：Node 内置的类型擦除只认 `.ts`，
 * 加载 `.tsx` 会直接报 `ERR_UNKNOWN_FILE_EXTENSION`。写成无 JSX 的 `.ts`
 * 之后，这个**真实组件**（而不是它的复制品）就能被 `node --test` +
 * `react-dom/server` 直接渲染，从而在无浏览器环境里证明两件事：
 * 盒图的缩进与列位置真的保留了、上传文本里的 HTML 真的被转义了。
 * 渲染语义与 JSX 版完全一致：正文始终是 React 文本子节点。
 */

import { createElement } from 'react'

import { chunkContentClassName } from '../../lib/chunk-content.ts'

export function ChunkContent({ content }: { content: string }) {
  // 正以文本子节点渲染，React 默认转义：不解析 HTML，不执行脚本
  return createElement('p', { className: chunkContentClassName(content) }, content)
}
