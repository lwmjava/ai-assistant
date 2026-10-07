/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ['class', '[data-theme="dark"]'],
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  // 分块正文的渲染类是 Tailwind 提取器的盲区：它们只在 `chunk-content.ts`
  // 里以字符串常量出现，一旦被改成拼接形式（`${WS}pre …`）就扫不到，
  // 而盒图缩进会**静默**失效（CSS 里没规则，但代码照样跑）。
  // 这里无条件保留；`chunk-content.test.ts` 会断言本列表与常量集合完全一致，
  // 改动任一侧都必须同步另一侧，否则测试变红。
  safelist: [
    // 结构化正文：whitespace-pre font-mono overflow-x-auto …
    'whitespace-pre',
    'font-mono',
    'overflow-x-auto',
    // 普通正文：whitespace-pre-wrap break-words …
    'whitespace-pre-wrap',
    'break-words',
    // 两类共用的排版 token
    'text-sm',
    'leading-relaxed',
    'text-text-muted',
  ],
  theme: {
    extend: {
      colors: {
        bg: 'hsl(var(--bg) / <alpha-value>)',
        surface: 'hsl(var(--surface) / <alpha-value>)',
        'surface-2': 'hsl(var(--surface-2) / <alpha-value>)',
        'surface-3': 'hsl(var(--surface-3) / <alpha-value>)',
        border: 'hsl(var(--border) / <alpha-value>)',
        'border-strong': 'hsl(var(--border-strong) / <alpha-value>)',
        text: 'hsl(var(--text) / <alpha-value>)',
        'text-muted': 'hsl(var(--text-muted) / <alpha-value>)',
        'text-faint': 'hsl(var(--text-faint) / <alpha-value>)',
        primary: 'hsl(var(--primary) / <alpha-value>)',
        'primary-fg': 'hsl(var(--primary-fg) / <alpha-value>)',
        'primary-soft': 'hsl(var(--primary-soft) / <alpha-value>)',
        accent: 'hsl(var(--accent) / <alpha-value>)',
        danger: 'hsl(var(--danger) / <alpha-value>)',
        success: 'hsl(var(--success) / <alpha-value>)',
        warning: 'hsl(var(--warning) / <alpha-value>)',
      },
      fontFamily: {
        display: ['"Space Grotesk"', '"PingFang SC"', '"Microsoft YaHei"', 'sans-serif'],
        sans: ['"Plus Jakarta Sans"', '"PingFang SC"', '"Microsoft YaHei"', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      fontSize: {
        // 正文起步 16px，行高 1.6；标题用 2 倍以上跳跃建立层级
        xs: ['0.75rem', { lineHeight: '1.5' }],
        sm: ['0.875rem', { lineHeight: '1.55' }],
        base: ['1rem', { lineHeight: '1.65' }],
        lg: ['1.125rem', { lineHeight: '1.6' }],
        xl: ['1.375rem', { lineHeight: '1.45' }],
        '2xl': ['1.75rem', { lineHeight: '1.3' }],
        '3xl': ['2.25rem', { lineHeight: '1.2' }],
        '4xl': ['3rem', { lineHeight: '1.1' }],
      },
      spacing: {
        touch: '2.75rem', // 44px 最小触摸目标
      },
      borderRadius: {
        card: '0.875rem',
      },
      boxShadow: {
        card: '0 1px 2px hsl(var(--shadow) / 0.28), 0 8px 24px -12px hsl(var(--shadow) / 0.35)',
        pop: '0 12px 40px -12px hsl(var(--shadow) / 0.55)',
        glow: '0 0 0 1px hsl(var(--primary) / 0.4), 0 0 24px -6px hsl(var(--primary) / 0.45)',
      },
      keyframes: {
        'fade-up': {
          from: { opacity: '0', transform: 'translateY(6px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        'pulse-ring': {
          '0%': { boxShadow: '0 0 0 0 hsl(var(--primary) / 0.5)' },
          '70%': { boxShadow: '0 0 0 10px hsl(var(--primary) / 0)' },
          '100%': { boxShadow: '0 0 0 0 hsl(var(--primary) / 0)' },
        },
        shimmer: {
          '100%': { transform: 'translateX(100%)' },
        },
      },
      animation: {
        'fade-up': 'fade-up 260ms cubic-bezier(0.22, 1, 0.36, 1) both',
        'pulse-ring': 'pulse-ring 1.6s ease-out infinite',
        shimmer: 'shimmer 1.6s infinite',
      },
    },
  },
  plugins: [],
}
