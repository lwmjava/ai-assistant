/** 与服务端相同的占位名规则。只声明，不替换。 */

const IDENT = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/

export function parsePromptVariables(
  template: string,
): { ok: true; variables: string[] } | { ok: false; variables: string[] } {
  const variables: string[] = []
  for (const match of template.matchAll(/\{([^{}]*)\}/g)) {
    const body = match[1] ?? ''
    if (!IDENT.test(body)) return { ok: false, variables: [] }
    if (!variables.includes(body)) variables.push(body)
  }
  return { ok: true, variables }
}
