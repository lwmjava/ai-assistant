/**
 * 分块正文的「结构化」判定与渲染样式。
 *
 * 目的：知识库分块里常出现 ASCII 盒图、代码块、缩进配置/YAML 这类正文，
 * 它们的语义完全依赖缩进与列位置。一旦被浏览器按普通文本折叠空白，
 * 盒图会散架、代码的层级会消失，用户根本读不懂。
 *
 * 因此判定为结构化的正文要改用：等宽字体 + `whitespace-pre` + 横向滚动。
 *
 * 安全约束：本模块只负责「用哪种 class 渲染纯文本」。全仓不存在
 * `dangerouslySetInnerHTML`，正文一律作为 React 文本节点渲染（React 默认转义），
 * 上传文本里的 `<script>` 只会显示成字符串，不会被执行。
 */

/** 盒图/制表符字符区间 U+2500–U+257F 的下界与上界。 */
const BOX_DRAWING_START = 0x2500
const BOX_DRAWING_END = 0x257f

/** Markdown 三反引号代码围栏。 */
const CODE_FENCE = '```'

/** 行首缩进：≥2 个空格，或 1 个制表符（一个 tab 就是一个缩进层级），且后面还有可见内容。 */
const INDENTED_LINE = /^(?: {2,}|\t)\S/

/** 规则 3 要求的缩进行数：低于这个数就当成自然语言，避免误判。 */
const INDENTED_LINE_THRESHOLD = 2

/**
 * 是否含盒图/制表符字符。
 *
 * 覆盖 U+2500–U+257F：─ │ ┌ ┐ └ ┘ ├ ┤ ┬ ┴ ┼ ╭ ╮ ╰ ╯ ═ ║ ╔ ╗ ╚ ╝ 等。
 * 用码点区间而不是字符表，避免漏掉不常用的制表符变体。
 */
function hasBoxDrawing(text: string): boolean {
  for (let i = 0; i < text.length; i += 1) {
    const code = text.charCodeAt(i)
    if (code >= BOX_DRAWING_START && code <= BOX_DRAWING_END) return true
  }
  return false
}

/**
 * 判断一段正文是否需要「保留空白 + 等宽 + 横向滚动」渲染。
 *
 * 满足任意一条即为结构化：
 *  1. 含盒图/制表符字符（U+2500–U+257F）；
 *  2. 含 Markdown 三反引号代码围栏；
 *  3. 多行缩进结构：至少 2 行以 ≥2 个空格（或制表符）开头。
 *
 * 规则 3 取「2 行」而不是「1 行」，是因为自然语言段落里偶发一行缩进很常见，
 * 一旦按等宽 + 横向滚动展示反而更难读；只有成片的缩进才说明它是结构化文本。
 *
 * 边界行为：
 *  - 空串 / 全空白：false（没有可保留的结构）；
 *  - 单行（哪怕很长、哪怕有缩进）：false（不存在跨行的列对齐关系）；
 *  - 超长但无盒图、无围栏、无缩进的一行：false（按普通段落换行展示）。
 */
export function isStructuredBlock(text: string): boolean {
  if (!text) return false
  if (hasBoxDrawing(text)) return true
  if (text.includes(CODE_FENCE)) return true

  let indented = 0
  for (const line of text.split('\n')) {
    if (INDENTED_LINE.test(line)) {
      indented += 1
      if (indented >= INDENTED_LINE_THRESHOLD) return true
    }
  }
  return false
}

/** 结构化正文的渲染 class：保留空白 + 等宽 + 窄屏横向滚动。 */
const STRUCTURED_CLASS =
  'whitespace-pre font-mono overflow-x-auto text-sm leading-relaxed text-text-muted'

/** 普通正文的渲染 class：保持原有的自动换行与断词。 */
const PLAIN_CLASS = 'whitespace-pre-wrap break-words text-sm leading-relaxed text-text-muted'

/**
 * 按正文形态给出渲染 class。
 *
 * 抽成纯函数是为了让「盒图必须保留空白」这条契约可被单测直接断言：
 * 任何一处改掉 `whitespace-pre` / `font-mono` / `overflow-x-auto`，测试会立刻变红。
 */
export function chunkContentClassName(text: string): string {
  return isStructuredBlock(text) ? STRUCTURED_CLASS : PLAIN_CLASS
}

/** 结构化渲染 class 的 token 列表，供测试按词断言（避免 `whitespace-pre-wrap` 误命中）。 */
export function structuredClassTokens(): string[] {
  return STRUCTURED_CLASS.split(' ').filter(Boolean)
}

/** 普通正文渲染 class 的 token 列表，与 `structuredClassTokens` 成对使用。 */
export function plainClassTokens(): string[] {
  return PLAIN_CLASS.split(' ').filter(Boolean)
}
