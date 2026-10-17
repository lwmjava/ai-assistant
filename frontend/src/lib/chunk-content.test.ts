import { test } from 'vitest'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import {
  chunkContentClassName,
  isStructuredBlock,
  plainClassTokens,
  structuredClassTokens,
} from './chunk-content.ts'

test('盒图正文判定为结构化', () => {
  const box = ['┌─────────┐', '│ 订单    │', '├─────────┤', '│ 支付    │', '└─────────┘'].join('\n')
  assert.equal(isStructuredBlock(box), true)
})

test('只含一个制表符也算结构化（避免漏判简化的盒图）', () => {
  assert.equal(isStructuredBlock('步骤 A ──▶ 步骤 B'), true)
})

test('三反引号代码围栏判定为结构化', () => {
  const fenced = '```python\nfor i in range(3):\n    print(i)\n```'
  assert.equal(isStructuredBlock(fenced), true)
})

test('缩进结构（≥2 行以 ≥2 空格开头）判定为结构化', () => {
  const yaml = ['root:', '  child: 1', '  other: 2'].join('\n')
  assert.equal(isStructuredBlock(yaml), true)
})

test('制表符缩进同样算结构化', () => {
  const text = ['def f():', '\treturn 1', '\t# done'].join('\n')
  assert.equal(isStructuredBlock(text), true)
})

test('普通自然语言段落不算结构化', () => {
  const prose = '退款政策如下：用户在签收后七日内可以申请无理由退款，' + '需保证商品配件与包装完整，特殊定制类商品不适用本条款。'
  assert.equal(isStructuredBlock(prose), false)
})

test('只有一行缩进的段落不算结构化（避免误伤自然语言）', () => {
  const text = ['这是一段说明文字。', '  这里恰好缩进了一次。', '这里是最后一句。'].join('\n')
  assert.equal(isStructuredBlock(text), false)
})

test('边界：空串与全空白为 false', () => {
  assert.equal(isStructuredBlock(''), false)
  assert.equal(isStructuredBlock('   \n\t  \n'), false)
})

test('边界：单行（含缩进、含超长行）为 false', () => {
  assert.equal(isStructuredBlock('   这一行有缩进但只有一行'), false)
  assert.equal(isStructuredBlock('x'.repeat(5000)), false)
})

test('边界：超长行带上盒图字符仍然是结构化', () => {
  assert.equal(isStructuredBlock(`│${'y'.repeat(5000)}`), true)
})

test('结构化正文的渲染 class 保留空白、等宽、可横向滚动', () => {
  const tokens = structuredClassTokens()
  assert.ok(tokens.includes('whitespace-pre'), '必须保留 whitespace-pre')
  assert.ok(tokens.includes('font-mono'), '必须是等宽字体')
  assert.ok(tokens.includes('overflow-x-auto'), '窄屏必须能横向滚动')
})

/**
 * 与 Tailwind `safelist` 的一致性守护。
 *
 * 为什么读源码而不是只比对运行时值：Tailwind 的提取器是对**文件原文**做正则的，
 * 类名一旦被写成拼接形式（`${WS}pre …`）就扫不到，而运行时 `structuredClassTokens()`
 * 仍会返回同样的 token——只比对运行时值的话，这个改动会完全逃逸。
 * 所以这里要求常量保持单引号字面量（比 Tailwind 更严格，换来可静态校验），
 * 并断言它的 token 集合与 safelist **双向完全一致**。
 */

const CHUNK_SOURCE = readFileSync(new URL('./chunk-content.ts', import.meta.url), 'utf8')

function literalOf(constName: string): string[] {
  const matched = new RegExp(`const ${constName}\\s*=\\s*'([^']*)'`).exec(CHUNK_SOURCE)
  assert.ok(matched, `${constName} 必须是单引号字符串字面量，拼接形式会让 Tailwind 扫不到`)
  return matched[1].split(' ').filter(Boolean)
}

// 直接读配置源码取 safelist：既避免给无类型声明的 .js 配置写 any 断言，
// 也不需要在测试里执行 Tailwind 配置。
const CONFIG_SOURCE = readFileSync(new URL('../../tailwind.config.js', import.meta.url), 'utf8')

const safelist = (() => {
  const block = /safelist:\s*\[([\s\S]*?)\]/.exec(CONFIG_SOURCE)
  assert.ok(block, 'tailwind.config.js 里找不到 safelist')
  const withoutComments = block[1].replace(/\/\/.*$/gm, '')
  return [...withoutComments.matchAll(/'([^']+)'/g)].map((m) => m[1])
})()

test('渲染类常量必须逐 token 出现在 Tailwind safelist 里', () => {
  for (const token of [...structuredClassTokens(), ...plainClassTokens()]) {
    assert.ok(safelist.includes(token), `safelist 缺少 ${token}`)
  }
})

test('safelist 与常量集合双向完全一致：任一侧多一个/少一个都要红', () => {
  const inCode = new Set([...literalOf('STRUCTURED_CLASS'), ...literalOf('PLAIN_CLASS')])
  const inConfig = new Set(safelist)
  const onlyInCode = [...inCode].filter((t) => !inConfig.has(t))
  const onlyInConfig = [...inConfig].filter((t) => !inCode.has(t))
  assert.deepEqual(
    { onlyInCode, onlyInConfig },
    { onlyInCode: [], onlyInConfig: [] },
    'chunk-content.ts 的类常量与 tailwind.config.js 的 safelist 已经漂移',
  )
})

test('常量必须是静态字面量：拼接写法会让提取器失效', () => {
  // 与运行时值比对，确保「源码里的字面量」和「实际用到的 class」没有分叉
  assert.deepEqual(literalOf('STRUCTURED_CLASS'), structuredClassTokens())
  assert.deepEqual(literalOf('PLAIN_CLASS'), plainClassTokens())
})

test('盒图正文拿到结构化 class，普通段落拿到换行 class', () => {
  const box = '┌──┐\n│ab│\n└──┘'
  const structured = chunkContentClassName(box).split(' ')
  assert.ok(structured.includes('whitespace-pre'))
  assert.ok(!structured.includes('whitespace-pre-wrap'))

  const plain = chunkContentClassName('一段普通的说明文字。').split(' ')
  assert.ok(plain.includes('whitespace-pre-wrap'))
  assert.ok(plain.includes('break-words'))
})
