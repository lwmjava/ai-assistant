/** 已登录但当前角色无权打开该页时的完整 403 页。 */

import { Link } from 'react-router-dom'

export function ForbiddenPage({
  title = '没有访问权限',
  description = '当前角色无权打开此页面。权限由后端角色矩阵控制，如需访问请联系管理员。',
}: {
  title?: string
  description?: string
}) {
  return (
    <div className="panel mx-auto max-w-md" role="alert" aria-labelledby="forbidden-heading">
      <div className="px-6 py-12 text-center">
        <p className="font-display text-4xl font-semibold text-text">403</p>
        <h1 id="forbidden-heading" className="mt-2 text-lg font-medium text-text">
          {title}
        </h1>
        <p className="mt-2 text-sm text-text-muted">{description}</p>
        <Link
          to="/"
          className="mt-6 inline-flex h-11 items-center justify-center rounded-lg bg-primary px-4 text-sm font-semibold text-primary-fg"
        >
          返回首页
        </Link>
      </div>
    </div>
  )
}
