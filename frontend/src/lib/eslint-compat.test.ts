import { resolve } from 'node:path'
import { ESLint } from 'eslint'
import { expect, test } from 'vitest'

const lint = new ESLint({ cwd: process.cwd() })

test('nested TypeScript files retain unused-variable checks', async () => {
  const [result] = await lint.lintText(
    'export function Example() { const unused = 1; return null }',
    { filePath: 'src/nested/Example.tsx' },
  )
  expect(result.messages).toContainEqual(expect.objectContaining({
    ruleId: '@typescript-eslint/no-unused-vars', severity: 2,
  }))
})

test('conditional React Hooks remain errors', async () => {
  const [result] = await lint.lintText(
    `import { useEffect } from 'react'
     export function Example({ enabled }: { enabled: boolean }) {
       if (enabled) useEffect(() => {}, [])
       return null
     }`,
    { filePath: 'src/nested/Example.tsx' },
  )
  expect(result.messages).toContainEqual(expect.objectContaining({
    ruleId: 'react-hooks/rules-of-hooks', severity: 2,
  }))
})

test('missing Hook dependencies retain their warning level', async () => {
  const [result] = await lint.lintText(
    `import { useEffect } from 'react'
     export function Example({ value }: { value: string }) {
       useEffect(() => { console.log(value) }, [])
       return null
     }`,
    { filePath: 'src/nested/Example.tsx' },
  )
  expect(result.messages).toContainEqual(expect.objectContaining({
    ruleId: 'react-hooks/exhaustive-deps', severity: 1,
  }))
})

test.each(['dist/assets/generated.ts', 'node_modules/example/index.ts'])(
  'generated path %s remains ignored', async (path) => {
    expect(await lint.isPathIgnored(resolve(path))).toBe(true)
  },
)
