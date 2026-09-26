/** 首次初始化：库中没有系统管理员时创建首个管理员。 */

import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { useNavigate } from 'react-router-dom'
import { z } from 'zod'
import { ArrowRight, Lock } from 'lucide-react'

import { AuthScreen } from '@/components/auth/AuthScreen'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Field'
import { api, ApiError } from '@/lib/http'
import { useAuthStore } from '@/store/auth'
import type { Token } from '@/types/api'

const schema = z.object({
  username: z.string().trim().min(1, '请输入用户名').max(64, '用户名过长'),
  password: z.string().min(8, '密码至少 8 位').max(128, '密码过长'),
  email: z.string().trim().max(254, '邮箱过长').optional(),
})

type FormValues = z.infer<typeof schema>

export default function SetupPage() {
  const navigate = useNavigate()
  const establish = useAuthStore((s) => s.establish)
  const [formError, setFormError] = useState<string | null>(null)

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { username: '', password: '', email: '' },
  })

  async function onSubmit(values: FormValues) {
    setFormError(null)
    const email = values.email?.trim()
    try {
      const token = await api.post<Token>(
        '/auth/setup',
        { username: values.username, password: values.password, email: email || null },
        { anonymous: true },
      )
      await establish(token)
      void navigate('/chat', { replace: true })
    } catch (err) {
      setFormError(
        err instanceof ApiError ? err.detail : '创建管理员失败，请确认后端已启动',
      )
    }
  }

  return (
    <AuthScreen
      title="初始化管理员"
      subtitle="库里还没有系统管理员。创建后这个向导就会关闭。"
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
          label="邮箱"
          type="email"
          autoComplete="email"
          placeholder="选填"
          error={errors.email?.message}
          {...register('email')}
        />
        <Input
          label="密码"
          type="password"
          autoComplete="new-password"
          required
          placeholder="至少 8 位"
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
          {isSubmitting ? '创建中…' : '创建并进入对话'}
        </Button>
      </form>
    </AuthScreen>
  )
}
