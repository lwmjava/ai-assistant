/** 主题：深色默认，可切换并持久化；未设置时跟随系统偏好。 */

import { create } from 'zustand'
import { persist } from 'zustand/middleware'

type Theme = 'dark' | 'light'

interface ThemeState {
  theme: Theme
  toggle: () => void
  set: (theme: Theme) => void
}

function apply(theme: Theme) {
  const root = document.documentElement
  root.setAttribute('data-theme', theme)
  root.style.colorScheme = theme
}

function initial(): Theme {
  const attr = document.documentElement.getAttribute('data-theme')
  if (attr === 'light' || attr === 'dark') return attr
  return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
}

export const useThemeStore = create<ThemeState>()(
  persist(
    (set, get) => ({
      theme: initial(),
      toggle: () => {
        const next: Theme = get().theme === 'dark' ? 'light' : 'dark'
        apply(next)
        set({ theme: next })
      },
      set: (theme) => {
        apply(theme)
        set({ theme })
      },
    }),
    { name: 'aa-theme' },
  ),
)

// 初始化时把 store 中的值落到 DOM（index.html 已做首屏防闪，这里保证一致性）
apply(useThemeStore.getState().theme)
