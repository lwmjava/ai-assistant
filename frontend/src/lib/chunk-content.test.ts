import test from 'node:test'
import assert from 'node:assert/strict'

import {
  chunkContentClassName,
  isStructuredBlock,
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

test('盒图正文拿到结构化 class，普通段落拿到换行 class', () => {
  const box = '┌──┐\n│ab│\n└──┘'
  const structured = chunkContentClassName(box).split(' ')
  assert.ok(structured.includes('whitespace-pre'))
  assert.ok(!structured.includes('whitespace-pre-wrap'))

  const plain = chunkContentClassName('一段普通的说明文字。').split(' ')
  assert.ok(plain.includes('whitespace-pre-wrap'))
  assert.ok(plain.includes('break-words'))
})
