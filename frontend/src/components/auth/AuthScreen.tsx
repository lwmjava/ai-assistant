/** 登录、注册和初始化向导共用的左右分栏。 */

import type { ReactNode } from 'react'

import { PipelineShowcase } from '@/components/auth/PipelineShowcase'

export function AuthScreen({
  title,
  subtitle,
  children,
  footnote,
}: {
  title: string
  subtitle: string
  children: ReactNode
  footnote?: ReactNode
}) {
  return (
    <div className="grid min-h-screen lg:grid-cols-[1.1fr_1fr]">
      <section className="relative hidden flex-col justify-between overflow-hidden border-r border-border p-10 lg:flex xl:p-14">
        <div
          className="pointer-events-none absolute -left-24 top-1/4 size-[420px] rounded-full bg-primary/12 blur-3xl"
          aria-hidden
        />
        <div
          className="pointer-events-none absolute -bottom-32 right-0 size-[380px] rounded-full bg-accent/8 blur-3xl"
          aria-hidden
        />

        <div className="relative flex items-center gap-3">
          <span className="grid size-10 place-items-center rounded-xl bg-primary/15 text-primary ring-1 ring-primary/30">
            <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12 3 4 7v6c0 4.4 3.4 7.4 8 8 4.6-.6 8-3.6 8-8V7l-8-4Z" strokeLinejoin="round" />
              <path d="M9 12.5l2 2 4-4" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </span>
          <div>
            <p className="font-display text-lg font-semibold tracking-tight text-text">ai-assistant</p>
            <p className="text-xs text-text-faint">企业级 AI 助手平台</p>
          </div>
        </div>

        <div className="relative max-w-lg space-y-6">
          <h1 className="font-display text-4xl font-semibold leading-[1.15] tracking-tight text-text text-balance xl:text-5xl">
            每一次回答，都跑完一条
            <span className="text-primary"> 可观测的推理管线</span>
          </h1>
          <p className="text-base leading-relaxed text-text-muted text-pretty">
            理解、规划、检索、行动、反思、响应——六个阶段逐层推进，工具调用与知识检索全程留痕。
          </p>
          <div className="panel-inset w-fit p-5">
            <PipelineShowcase />
          </div>
        </div>

        <p className="relative text-xs text-text-faint">
          没有系统管理员时，首次打开会进入初始化向导。已有账号后可以登录，也可以注册进默认租户。
        </p>
      </section>

      <section className="relative flex items-center justify-center px-5 py-10 sm:px-8">
        <div className="w-full max-w-sm space-y-7">
          <div className="space-y-2 lg:hidden">
            <div className="flex items-center gap-2.5">
              <span className="grid size-9 place-items-center rounded-xl bg-primary/15 text-primary ring-1 ring-primary/30">
                <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M12 3 4 7v6c0 4.4 3.4 7.4 8 8 4.6-.6 8-3.6 8-8V7l-8-4Z" strokeLinejoin="round" />
                </svg>
              </span>
              <p className="font-display text-lg font-semibold text-text">ai-assistant</p>
            </div>
          </div>

          <div className="space-y-1.5">
            <h2 className="font-display text-2xl font-semibold tracking-tight text-text">{title}</h2>
            <p className="text-sm text-text-muted">{subtitle}</p>
          </div>

          {children}
          {footnote}
        </div>
      </section>
    </div>
  )
}
