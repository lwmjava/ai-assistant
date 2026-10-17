import js from '@eslint/js'
import globals from 'globals'
import tseslint from 'typescript-eslint'
import reactHooks from 'eslint-plugin-react-hooks'

/**
 * ESLint 9 flat config。
 *
 * 规则集：ESLint recommended + typescript-eslint recommended + React Hooks recommended。
 * 前端源文件按浏览器全局；配置文件（vite/vitest/tailwind/postcss）按 Node 全局，
 * 避免 CommonJS 配置被 no-undef 误报。
 */
export default tseslint.config(
  {
    ignores: ['dist', 'node_modules', 'coverage'],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2021,
      sourceType: 'module',
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
    plugins: {
      'react-hooks': reactHooks,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
    },
  },
)
