/** 内置账号首次登录必须先修改初始口令。 */
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { useNavigate } from 'react-router-dom'

import { AuthScreen } from '@/components/auth/AuthScreen'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Field'
import { ApiError, hideStackTrace } from '@/lib/http'
import { useAuthStore } from '@/store/auth'

const schema = z.object({
  current_password: z.string().min(1, '请输入当前密码'),
  new_password: z.string().min(8, '新密码至少 8 位').max(128, '密码过长'),
  confirm_password: z.string().min(1, '请再次输入新密码'),
}).refine((values) => values.new_password !== values.current_password, {
  message: '新密码不能与当前密码相同', path: ['new_password'],
}).refine((values) => values.new_password === values.confirm_password, {
  message: '两次输入的新密码不一致', path: ['confirm_password'],
})

type FormValues = z.infer<typeof schema>

export default function ChangePasswordPage() {
  const navigate = useNavigate()
  const changePassword = useAuthStore((s) => s.changePassword)
  const logout = useAuthStore((s) => s.logout)
  const [formError, setFormError] = useState<string | null>(null)
  const { register, handleSubmit, formState: { errors, isSubmitting } } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { current_password: '', new_password: '', confirm_password: '' },
  })

  async function onSubmit(values: FormValues) {
    setFormError(null)
    try {
      await changePassword({ current_password: values.current_password, new_password: values.new_password })
      void navigate('/chat', { replace: true })
    } catch (err) {
      setFormError(hideStackTrace(err instanceof ApiError ? err.detail : '修改密码失败，请检查网络后重试'))
    }
  }

  return <AuthScreen title="首次登录请修改密码" subtitle="设置自己的密码后才能进入对话和其他功能。">
    <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
      <Input label="当前密码" type="password" autoComplete="current-password" autoFocus required error={errors.current_password?.message} {...register('current_password')} />
      <Input label="新密码" type="password" autoComplete="new-password" required placeholder="至少 8 位" error={errors.new_password?.message} {...register('new_password')} />
      <Input label="确认新密码" type="password" autoComplete="new-password" required error={errors.confirm_password?.message} {...register('confirm_password')} />
      {formError && <div role="alert" className="rounded-lg border border-danger/35 bg-danger/10 px-3 py-2.5 text-sm text-danger">{formError}</div>}
      <Button type="submit" variant="primary" size="lg" block loading={isSubmitting}>{isSubmitting ? '修改中…' : '修改密码并继续'}</Button>
      <Button block onClick={logout} disabled={isSubmitting}>退出登录</Button>
    </form>
  </AuthScreen>
}
