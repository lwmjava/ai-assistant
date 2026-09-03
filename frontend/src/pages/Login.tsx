/** 登录页：左侧品牌区以「五阶段管线」的动态示意作为记忆点，右侧为登录表单。 */

import { useEffect, useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { useLocation, useNavigate } from 'react-router-dom'
import { z } from 'zod'
import { ArrowRight, Lock, UserRound } from 'lucide-react'

import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Field'
import { ApiError } from '@/lib/http'
import { useAuthStore } from '@/store/auth'
import { PIPELINE_STAGES_BRIEF } from '@/types/api'
import { cn } from '@/lib/cn'

const schema = z.object({
  username: z.string().min(1, '请输入用户名'),
  password: z.string().min(1, '请输入密码'),
})

type FormValues = z.infer<typeof schema>

/** 管线示意：按顺序点亮阶段节点，直观传达平台的 Agent 编排能力。 */
function PipelineShowcase() {
  const [active, setActive] = useState(0)
  const [reduced, setReduced] = useState(false)

  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    setReduced(mq.matches)
    const onChange = (e: MediaQueryListEvent) => setReduced(e.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  useEffect(() => {
    if (reduced) {
      setActive(PIPELINE_STAGES_BRIEF.length - 1)
      return
    }
    const timer = window.setInterval(() => {
      setActive((i) => (i + 1) % (PIPELINE_STAGES_BRIEF.length + 2))
    }, 900)
    return () => window.clearInterval(timer)
  }, [reduced])

  return (
    <div className="relative overflow-hidden" aria-hidden>
      <div className="space-y-3">
        {PIPELINE_STAGES_BRIEF.map((stage, idx) => {
          const done = idx < active
          const current = idx === active
          return (
            <div key={stage} className="flex items-center gap-3">
              <div className="relative flex flex-col items-center">
                <span
                  className={cn(
                    'grid size-8 place-items-center rounded-full border text-xs font-semibold transition-all duration-500',
                    done && 'border-primary/50 bg-primary/20 text-primary',
                    current && 'border-primary bg-primary text-primary-fg scale-110 shadow-glow',
                    !done && !current && 'border-border bg-surface-2 text-text-faint',
                  )}
                >
                  {idx + 1}
                </span>
                {idx < PIPELINE_STAGES_BRIEF.length - 1 && (
                  <span
                    className={cn(
                      'absolute top-9 h-3 w-px transition-colors duration-500',
                      done ? 'bg-primary/60' : 'bg-border',
                    )}
                  />
                )}
              </div>
              <span
                className={cn(
                  'font-display text-sm transition-colors duration-500',
                  current ? 'text-text' : done ? 'text-text-muted' : 'text-text-faint',
                )}
              >
                {stage}
              </span>
              {current && !reduced && (
                <span className="ml-1 h-1 w-8 overflow-hidden rounded-full bg-surface-3">
                  <span className="block h-full w-1/3 animate-shimmer rounded-full bg-primary/70" />
                </span>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

export default function LoginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const login = useAuthStore((s) => s.login)
  const isAuthed = useAuthStore((s) => Boolean(s.access_token))

  const [formError, setFormError] = useState<string | null>(null)

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { username: '', password: '' },
  })

  // 已登录用户直接回到目标页
  useEffect(() => {
    if (isAuthed) {
      const from = (location.state as { from?: string } | null)?.from ?? '/chat'
      void navigate(from, { replace: true })
    }
  }, [isAuthed, location.state, navigate])

  async function onSubmit(values: FormValues) {
    setFormError(null)
    try {
      await login(values)
      const from = (location.state as { from?: string } | null)?.from ?? '/chat'
      void navigate(from, { replace: true })
    } catch (err) {
      // 失败时不清空输入，仅内联提示
      setFormError(
        err instanceof ApiError ? err.detail : '登录失败，请检查网络或后端服务是否启动',
      )
    }
  }

  return (
    <div className="grid min-h-screen lg:grid-cols-[1.1fr_1fr]">
      {/* 品牌区 */}
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
          默认账号由后端首次启动时引导创建，详见服务端日志。
        </p>
      </section>

      {/* 表单区 */}
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
            <h2 className="font-display text-2xl font-semibold tracking-tight text-text">
              登录控制台
            </h2>
            <p className="text-sm text-text-muted">使用平台账号继续</p>
          </div>

          <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <Input
              label="用户名"
              autoComplete="username"
              autoFocus
              required
              placeholder="admin"
              error={errors.username?.message}
              {...register('username')}
            />
            <Input
              label="密码"
              type="password"
              autoComplete="current-password"
              required
              placeholder="••••••••"
              error={errors.password?.message}
              {...register('password')}
            />

            {formError && (
              <div
                role="alert"
                className="flex items-start gap-2 rounded-lg border border-danger/35 bg-danger/10 px-3 py-2.5 text-sm text-danger"
              >
                <Lock className="mt-0.5 size-3.5 shrink-0" aria-hidden />
                <span className="break-words">{formError}</span>
              </div>
            )}

            <Button
              type="submit"
              variant="primary"
              size="lg"
              block
              loading={isSubmitting}
              icon={isSubmitting ? undefined : <ArrowRight className="size-4" aria-hidden />}
            >
              {isSubmitting ? '登录中…' : '登录'}
            </Button>
          </form>

          <p className="flex items-center gap-2 text-xs text-text-faint">
            <UserRound className="size-3.5" aria-hidden />
            登录与刷新令牌均会写入审计日志
          </p>
        </div>
      </section>
    </div>
  )
}
