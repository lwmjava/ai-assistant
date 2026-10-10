/**
 * 渲染级测试：用 `react-dom/server` 渲染**真实的** ChunkContent 组件。
 *
 * 这是「盒图缩进与列位置真的保留」「上传文本里的 HTML/脚本真的没被执行」
 * 两条验收点的直接证据——不需要浏览器，也不需要新增渲染依赖
 * （react-dom 是本仓库既有依赖）。
 */

import { test } from 'vitest'
import assert from 'node:assert/strict'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

import { ChunkContent } from './ChunkContent.ts'

/** 渲染真实组件，返回最终的 HTML 字符串。 */
function render(content: string): string {
  return renderToStaticMarkup(createElement(ChunkContent, { content }))
}

/** 取 class 属性的 token 列表（按空格切分，避免 whitespace-pre-wrap 误命中）。 */
function classTokens(html: string): string[] {
  const matched = /^<p class="([^"]*)"/.exec(html)
  assert.ok(matched, `输出不是预期的 <p class=...> 结构：${html}`)
  return matched[1].split(' ').filter(Boolean)
}

const BOX = ['┌─────┐', '│ 订单 │', '├─────┤', '│ 支付 │', '└─────┘'].join('\n')

test('盒图正文：盒图字符与换行原样保留，且套上结构化 class', () => {
  const html = render(BOX)
  for (const line of BOX.split('\n')) {
    assert.ok(html.includes(line), `盒图这一行没有原样出现在输出里：${line}`)
  }
  assert.ok(html.includes('\n'), '换行被吃掉了')
  const tokens = classTokens(html)
  assert.ok(tokens.includes('whitespace-pre'), '缺少 whitespace-pre')
  assert.ok(tokens.includes('font-mono'), '缺少 font-mono')
  assert.ok(tokens.includes('overflow-x-auto'), '缺少 overflow-x-auto（窄屏无法横向滚动）')
})

test('缩进与列位置：4 / 8 个前导空格必须还在', () => {
  const html = render('    def f():\n        pass')
  assert.ok(html.includes('    def f():'), '4 个空格的缩进没有保留')
  assert.ok(html.includes('        pass'), '8 个空格的缩进没有保留')
  assert.ok(classTokens(html).includes('whitespace-pre'))
})

test('脚本注入：<script> 被转义，输出里没有可执行的标签', () => {
  const html = render('<script>alert(1)</script>')
  assert.ok(html.includes('&lt;script&gt;'), `script 没有被转义：${html}`)
  assert.ok(!html.includes('<script'), `输出里出现了未转义的 <script：${html}`)
})

test('属性注入：<img onerror=...> 整段作为文本转义，没有真的标签', () => {
  const html = render('<img src=x onerror=alert(1)>')
  assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt;'), `img 没有被整段转义：${html}`)
  assert.ok(!html.includes('<img'), `输出里出现了未转义的 <img：${html}`)
})

test('输出中不出现 dangerouslySetInnerHTML 痕迹', () => {
  assert.ok(!render(BOX).includes('dangerouslySetInnerHTML'))
  assert.ok(!render('<script>alert(1)</script>').includes('dangerouslySetInnerHTML'))
})

test('普通自然语言段落：走非结构化分支，但仍保留换行', () => {
  const html = render('第一句说明。\n第二句说明。')
  const tokens = classTokens(html)
  assert.ok(!tokens.includes('whitespace-pre'), '普通段落不应套 whitespace-pre')
  assert.ok(tokens.includes('whitespace-pre-wrap'))
  assert.ok(html.includes('\n'), '换行应当保留')
})
