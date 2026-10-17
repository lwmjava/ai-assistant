import { defineConfig } from 'vitest/config'
import { fileURLToPath } from 'node:url'

/**
 * Vitest 独立配置。
 *
 * 不加载 @vitejs/plugin-react：现有 4 个 .test.ts 均为纯逻辑/服务端渲染测试
 * （node 环境 + react-dom/server），不需要浏览器环境，也不需要 JSX 转换插件。
 * 保留 '@' 别名与 vite.config.ts 一致，避免测试内引用别名时解析分叉。
 */
const srcDir = fileURLToPath(new URL('./src', import.meta.url))

export default defineConfig({
  resolve: {
    alias: {
      '@': srcDir,
    },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
})
