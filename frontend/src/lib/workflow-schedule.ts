/** 把列表响应头译成页面上的手动执行说明。 */

export const MANUAL_ONLY_NOTICE = '未安装 croniter，定时不会自动运行，目前只剩手动执行。'

export function workflowScheduleNotice(mode: string | null): string | null {
  return mode === 'manual-only' ? MANUAL_ONLY_NOTICE : null
}
