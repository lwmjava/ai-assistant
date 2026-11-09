/** 审批待办页。内容来自服务端事件，刷新后仍是同一批记录。 */

import { ApprovalInbox } from '@/components/approvals/ApprovalInbox'
import { PageHeader } from '@/components/layout/PageHeader'

export default function ApprovalsPage() {
  return (
    <div className="space-y-4">
      <PageHeader
        title="待审批"
        description="只显示你发起的动作。刷新或重新连接后仍是同一条记录。在对话里回复不会批准。"
      />
      <ApprovalInbox />
    </div>
  )
}
