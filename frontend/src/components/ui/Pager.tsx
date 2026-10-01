/** 列表底部分页：上一页、下一页，以及当前页和总条数。 */

import { Button } from '@/components/ui/Button'

export function Pager({
  page,
  pageCount,
  total,
  onPage,
}: {
  page: number
  pageCount: number
  total: number
  onPage: (page: number) => void
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-text-muted">
      <span>
        共 {total} 条，第 {page} / {pageCount} 页
      </span>
      <div className="flex gap-2">
        <Button
          type="button"
          variant="secondary"
          size="sm"
          disabled={page <= 1}
          onClick={() => onPage(page - 1)}
        >
          上一页
        </Button>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          disabled={page >= pageCount}
          onClick={() => onPage(page + 1)}
        >
          下一页
        </Button>
      </div>
    </div>
  )
}
