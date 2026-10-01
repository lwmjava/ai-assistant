# 实现说明：任务过程展示

> 日期：2026-10-01
> 计划：`docs/plans/plan_d2.md` 的 SUP-002 节
> 结果：Supervisor 对话在回复出现时，阶段条下方显示完成后的子任务摘要。默认五阶段对话没有这块。

## 完成的行为

1. 流式事件 `subtask` 会进入对话快照。数据要能解析成 JSON，并且 `v` 为 1、名称是 `research` 或 `draft`、状态是 `done`、摘要是字符串。缺字段、版本不是 1 或解析失败时忽略。
2. 阶段条下方在有合格记录时显示标题「子任务摘要」。每一行是名称、完成和摘要。摘要区域可以滚动。没有合格记录时，这块不出现，也不留空标题。
3. 五阶段占位保持原样。页面不用「正在执行」描述这些摘要。
4. 回复落库后快照会清空，摘要不写进消息表。刷新会话后看不到这一块。

## 代码位置

- `frontend/src/hooks/useChatStream.ts`：识别 `subtask`。
- `frontend/src/components/chat/SubtaskSummary.tsx`：摘要区块。
- `frontend/src/components/chat/MessageList.tsx`：放在阶段条下方。
- `frontend/src/pages/Chat.tsx`：把快照里的子任务传给消息列表。
- `frontend/src/types/api.ts`：事件类型和摘要结构。

## 验证

```text
cd frontend
npm run typecheck
退出码 0

npm run build
退出码 0
```

浏览器核对了两态：

- 默认编排是 `self`。新建会话发送「用一句话回答：1+1等于几」，回复是「1+1等于2。」页面上没有「子任务摘要」。
- 另起一个进程把编排设为 `langgraph`，前端代理到该进程。新建会话发送「只回答两个字：你好」。回复出现时，页面上有「子任务摘要」，行内是 `draft`、完成和摘要「你好」。没有「正在执行」。这次调度没有分到 `research`，所以页面上没有调研行。

## 没做的事

- 没有做图形化编排，也没有改阶段条的标准顺序。
- 没有把完成后的摘要显示成实时进度。
- 没有新增前端测试运行器。
- 没有把摘要存进消息，重新打开会话后不会再显示。
- 镜像和默认安装清单没有装 `langgraph`。这次核对用的是本机已经装好的 extra，并单独起了一个进程。
