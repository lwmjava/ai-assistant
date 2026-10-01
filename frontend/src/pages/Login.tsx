/** 登录页。 */

import { useEffect, useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { z } from 'zod'
import { ArrowRight, Lock, UserRound } from 'lucide-react'

import { AuthScreen } from '@/components/auth/AuthScreen'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Field'
import { ApiError } from '@/lib/http'
import { useAuthStore } from '@/store/auth'

const schema = z.object({
  username: z.string().min(1, '请输入用户名'),
  password: z.string().min(1, '请输入密码'),
})

type FormValues = z.infer<typeof schema>

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
      setFormError(
        err instanceof ApiError ? err.detail : '登录失败，请检查网络或后端服务是否启动',
      )
    }
  }

  return (
    <AuthScreen
      title="登录控制台"
      subtitle="使用平台账号继续"
      footnote={
        <p className="flex items-center gap-2 text-xs text-text-faint">
          <UserRound className="size-3.5" aria-hidden />
          登录与刷新令牌均会写入审计日志
        </p>
      }
    >
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
      <p className="text-sm text-text-muted">
        还没有账号？{' '}
        <Link to="/register" className="font-medium text-primary hover:underline">
          注册并加入默认租户
        </Link>
      </p>
    </AuthScreen>
  )
}
