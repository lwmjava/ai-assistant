# 前端换行处理误清空与精确恢复记录

- 日期：2026-10-16。
- 实施者：子 Agent /root/err_frontend；协助恢复：主 Agent /root。
- 事故：为处理 Python 文本写入引入的 CRLF，错误地先 open('w') 截断，再写入空字符串，导致本卡负责的 13 个前端源文件被清空。该命令是可避免的实现失误，不能归为测试或环境故障。
- 影响：仅本地工作区前端文件；无提交、推送、部署、生产数据写入或密钥读取。发现后立即报告主 Agent，暂停业务源码修复，禁止用 git checkout/reset 覆盖此前平台批次改动。
- 恢复方法：只读本任务相关 Codex session JSONL 的原始 UTF-8 工具输出和完整 apply_patch / Python 改动记录，在 data/err001-recovery 中重建。原生 CommandExecution 输出存在截断，未将不完整输出用作替代源码。HTTP/SSE/认证/错误工具基于本子 Agent 开工读取的全文，按原始编辑顺序重放；App、Login 和新增文件同样复原。
- Hook 特别核验：主 Agent 从此前 inst_validation 会话源码全文重放主任务历史 SSE 修复和 CHAT 附件补丁，首先对齐本卡基线 LF SHA a031306b…，再重放本卡错误码修改，最终与误清空前 C71F… 完全一致；没有恢复成 HEAD 旧版本。
- 恢复门禁：全部文件在隔离目录逐一与事故前已记录 SHA256 匹配后才写回。主 Agent 提供 hook 最终重建物，子 Agent 写回后对真实 13 个文件再次核验，13/13 字节精确一致；随后才继续独立 Review 要求的 P1/P2 修复。
- 防复发：后续只使用 data = p.read_bytes(); p.write_bytes(data.replace(...))，先完整读取再写入，绝不在截断后读取原文件。复原后的后续 LF 规范化和 Review 修复是显式新改动，重新执行最终检查，不冒用事故前证据。

## 恢复瞬间的逐文件证据

下列 SHA 是恢复瞬间、继续修复之前的版本；不是后续最终候选版本。原始机器记录位于 data/err001-recovery/verified-restoration.json（临时恢复目录，不包含业务凭据）。

| 文件 | 事故前 SHA256 / 恢复后 SHA256（相同） | 结果 |
|---|---|---|
| frontend/src/App.tsx | 498bed7c023b0317c5a89e59640250a917dde57fe671d248f8a298acd259eb6b | 匹配 |
| frontend/src/lib/api-errors.ts | 6c8744c09d0e47e7189641e6afb09d5d7f482225108f6ee01aa245ae329d7dd1 | 匹配 |
| frontend/src/lib/session-notice.ts | 700040990514238c7cd870da4e0de801d92811080f771bf1a9666b33557c13c8 | 匹配 |
| frontend/src/lib/error-contract.test.ts | c8ced25265aab50d495cc64bfdb124f635089d5b1203c8cc81673abb14fa3d77 | 匹配 |
| frontend/src/lib/http.ts | 1d5d0c03649d6c1bca46d0c8e127aa5fcc3b93bd0d8702af7e3b5b75ce7d0616 | 匹配 |
| frontend/src/lib/sse.ts | 11a6b73b02f379bdf00d28b344cfa5c1f5de0889a988c737707edc2779fc97f6 | 匹配 |
| frontend/src/lib/rate-limit.ts | f076a980bdbac15aff74326d45bac12ec5878a0e85f7c091e0788c933cc05014 | 匹配 |
| frontend/src/lib/quota-message.ts | 16c245b80e3ebc4229268a51821c7c8490d19e7c9c90ffd4020713fd1ac90b76 | 匹配 |
| frontend/src/pages/Login.tsx | 83282e72b0e83f1ae531403b088ddae1c73b24c18a6a449bf287c2a4e6ec35d0 | 匹配 |
| frontend/src/store/auth.ts | 17e5760a4b33ef777991144d6118806714dff2b18c66a30bd4d4e986e1842999 | 匹配 |
| frontend/src/components/auth/SessionNotice.tsx | f643e2c98c5ec6323c4ed108925c8f3ab94b746921fa113e8ee8bff1795b45f5 | 匹配 |
| frontend/src/components/ui/RouteErrorBoundary.tsx | a32021a27989fdd4535793f27e4c8ccaa7422d85e43aaf92d404a7810259b5c9 | 匹配 |
| frontend/src/hooks/useChatStream.ts | c71feeadab59b93fe500a1fb0b234b596c6dcfb066f417af8eaacf7b0696606b | 匹配 |

## 证据有效性

事故前前端检查曾有 146 项测试及四项退出码 0，但之后已修复跨账户刷新竞态及响应体超时，并规范换行；最终交付只能使用修复后重新运行的检查及候选文件指纹。恢复本身不能证明业务验收通过。主 Agent 最终报告补充独立复核和页面验收。
