# CHAT-006 前端独立审查

日期：2026-10-18。审查身份：`/root/chat006_engineering`，参与前置只读工程准入，未参与业务实现。本轮仅写审查记录，不改业务代码、测试或任务状态。原始契约：tasks.yaml CHAT-006；实施计划：docs/plans/plan_chat_006_20261018.md。基线提交：7d3570827b165198daa049f3802933ab6e2ce572，候选为该 HEAD 加当前工作区。

## 范围与指纹

读取真实 hook、页面入口、MessageList、API、两个前端测试文件；未把已有测试摘要当作本轮执行结果。

| 文件 | SHA256 |
|---|---|
| frontend/src/hooks/useRegenerate.ts | E1C3A0BF4B00B62D657790982B09B10D0F1D701BB3820FD1059368BA4F3289C4 |
| frontend/src/pages/Chat.tsx | 934EF64D560B02ECC4834813143326387A1E6811BC6A20839D0503994001C88A |
| frontend/src/components/chat/MessageList.tsx | AE9DB414AE068467CBC5C8B04AE20E24E7EE160B5BE1294B54268AC8646CBB75 |
| frontend/src/api/chat.ts | 312EBB5F567FA606E97C9203E3605EA598BE52535FA16F8DC63C7CE91592561F |
| frontend/src/lib/chat-regenerate.test.ts | 26BACE5213740A57C19C9BC72912E298196A4A958F61F1E079BC94EA17059F2A |
| frontend/src/components/chat/regenerate.test.ts | 9D5F08D7B9D5CB374FCE7C599394E40BFEF2EBD50CE215EE98D2BE1AB4752534 |

## 发现：P2 删除生成中的当前会话后等待状态不能释放

真实入口为 Chat.tsx 侧栏 onDelete → confirmDelete；删除操作未禁用生成中会话，成功后 setActiveId(null)，但没有调用 regeneration.reset。useRegenerate.ts 26–29 行 effect cleanup 中止请求并将 inFlight 设为空；旧请求 finally 因引用不再相等，不会 setRegenerating(false)。hook 的 regenerating 因而永久为 true；页面 busy 保持 true，发送和切换会话不可用，需刷新或另点新会话 reset 才恢复。

实际反例：用 Node 加 frontend/node_modules/typescript 的 transpileModule 编译真实 hook，注入最小 React hooks 调度器和延迟 API Promise，启动 run 后执行真实 effect cleanup，重新 render conversationId=null，再以 AbortError 拒绝旧请求。命令退出码 0，输出：

```text
started {"busy":true,"locked":true,"calls":1}
after conversation deleted {"busy":true,"locked":false,"calls":1}
REPRODUCED: busy remains true although request lock is gone
```

此反例属于 E3 hook 生命周期证据；页面入口到状态改变通过代码核对，尚未实际删除页面会话，不以它充当 E2 页面验收。已有 hook 测试 mock useEffect 为无操作，不能覆盖真实 cleanup 引发的这条路径。应补运行 effect cleanup 的测试，并在会话切换时释放 busy，或在删除入口显式 reset 后检查其他切换路径。修复后需独立复核。

## 其余核对

- 同步重复点击：run 在任何 await 前设置 inFlight；页面发送及重新生成均读 isRunning，因此 React 尚未提交 busy 时仍能挡住第二次请求。静态核对，与已有测试一致，本轮未重跑 Vitest。
- 停止：cancel 仅 abort 请求等待；onAbort 展示历史加载，不调用服务端取消接口，没有把停止等待宣称为停止服务端生成。
- 旧请求串入新会话：ownsView 同时比较 controller 和当前 conversationId，成功提示前再次复核；显式 handleNew 会 reset。删除路径的 busy 缺陷另列上文。
- 失败：onError 后尝试读取真实历史；失败刷新不删除原气泡。refresh 失败不会覆盖原错误提示。
- 原输入与附件：重新生成 API 只传路径 ID，没有客户端 prompt 或附件覆盖；handleRegenerate 没有 setInput/clearAttachments，因此当前草稿不会被重新生成覆盖；原历史附件仍显示。服务端原附件复用由后端审查负责。
- MessageList 只在实际末条助手上展示操作，完整/失败助手均可触发；只读没有 onRegenerate 因而不显示。ID 和时间用于区分保留的新旧消息。

## 结论

当前存在未修复 P2，前端独立审查暂不通过。未运行 npm、pytest 或实际页面验收，避免与主代理验证并行；本记录不替代前端四门和页面业务验收。修复后更新候选指纹并追加复核记录。

## 修复后独立复核（同日）

上述结论为首轮发现时状态。实现者已将清 busy 放到 conversationId effect setup，cleanup 仅 abort 并释放引用；补充真正执行 effect cleanup 的回归测试。最终 hook SHA256：E32F6F512764368A6E5C491B45D83DAE7022CEBAAD2DC2F8829BF1FB3FC1290B；hook 测试 SHA256：49381670A866FF17986D653F60DFD5C40BD9E7727EAFDA5DBE6533A2B6F0C6DB。其余首轮文件指纹沿用。

本审查者重新用 Node、TypeScript transpileModule 编译真实 hook，独立构造延迟 Promise、真实 effect setup/cleanup 及状态写入计数，执行三项反例；命令退出码 0：

```text
PASS delete/switch clears busy before old promise settles
PASS old completion retains newer busy and lock
PASS unmount cleanup and late rejection cause no state writes or notices
```

具体验证：删除会话后旧请求尚未完成即 busy=false；新会话发起第二次请求后旧请求成功返回，仍保留新请求 busy 和锁、不弹旧成功提示；unmount 后旧请求拒绝，不增加任何 setState 次数或提示。均为 E3 生命周期反例证据，不冒充页面 E2。本轮仍未运行 npm/pytest，与根代理全量后端验证保持隔离。

最终前端独立审查结论：P2 已修复并经独立反例复核，未发现剩余 P1/P2。前端代码审查通过；页面验收及全量四门仍以主验证者对应最终指纹证据为准，本结论本身不关闭任务。
