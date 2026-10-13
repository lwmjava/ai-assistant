# Agent 基线门禁发布与 CI 验证记录

2026-10-13。用户明确批准仅 PAGT-007 的变更提交并推送独立分支 codex/agent-baseline-gate，触发一次真实 GitHub CI（最多12次新增模型请求，无重试）；通过后执行独立最终验收。不合并或部署。

远程已有基线 codex/pagt-tool-polish = a0d02eb411a53c3191e81f0fc99528ea60d0d3c4；独立分支以此为父提交，非旧 main。只发布本卡文件及 tasks.yaml 的本卡块，不发布 PAGT-004/005/006 未提交改动。

隔离工作树 data/pagt007-publish 的三个本卡测试文件：92 passed、exit0，88.64秒；范围内 ruff exit0；git diff --check exit0。原工作区补验92 passed、exit0，91.04秒。没有新增本地模型调用。

已保存的本地 official 报告针对原工作区的源码指纹；不将它当作隔离分支或 GitHub CI 的质量证据。GitHub 将生成绑定新提交与自身源码的新报告。CI尚待推送后实际观察；不得凭隔离测试标记任务done。
