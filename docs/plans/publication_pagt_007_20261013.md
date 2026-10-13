# Agent 基线门禁发布与 CI 验证记录

2026-10-13。用户明确批准仅 PAGT-007 的变更提交并推送独立分支 codex/agent-baseline-gate，触发一次真实 GitHub CI（最多12次新增模型请求，无重试）；通过后执行独立最终验收。不合并或部署。

远程已有基线 codex/pagt-tool-polish = a0d02eb411a53c3191e81f0fc99528ea60d0d3c4；独立分支以此为父提交，非旧 main。只发布本卡文件及 tasks.yaml 的本卡块，不发布 PAGT-004/005/006 未提交改动。

隔离工作树 data/pagt007-publish 的三个本卡测试文件：92 passed、exit0，88.64秒；范围内 ruff exit0；git diff --check exit0。原工作区补验92 passed、exit0，91.04秒。没有新增本地模型调用。

已保存的本地 official 报告针对原工作区的源码指纹；不将它当作隔离分支或 GitHub CI 的质量证据。GitHub 将生成绑定新提交与自身源码的新报告。CI尚待推送后实际观察；不得凭隔离测试标记任务done。

已提交并推送7bf432820c47963bd657b39da8b3987087cc8d1c；一次push触发run https://github.com/lwmjava/ai-assistant/actions/runs/38397622273。Agent official评测成功：deepseek-flash、12请求、三指标1.0；gate步骤exit2，探针在CI未运行。原始CI产物SHA2dcf41385f92ea03c48b403c63119268992db4e059ecb356ef6ec2408d1e7c47；同原提交/干净独立摘要下本地gate0及三探针通过，属于CI报告本地复验，不等于CI成功。F07已按正式反例修端点捕获，红1failed/exit1、绿2passed/exit0。首次提交时新文档CRLF的完整cached diffcheck失败，推送前仅规范文档LF并amend，完整提交diffcheck0，保留过程事实。

修订尚未再次推送或执行真实CI，一次CI新增12调用授权已耗尽。最终独立人工审核仍未执行；task保持in_review。没有创建PR、合并或部署。后续CI需新增一次发布/模型授权，不能使用原报告冒充修订后CI质量证据。

F07独立修后验收：pagt007_entry_validation_v2，2passed/0skip/exit0（3.27s）；实际YAML端点命令stdout仅64hex摘要、真实警告进入stderr；Git Bash完整heredoc语法exit0。范围ruff及隔离diffcheck0。旧counter审查Agent两次capacity失败，未作为修后验收证据。

同一CI另有既有后端并发测试失败：1852passed/1failed/4skipped（377.90s），tests/test_rag_039_acceptance.py::test_thread_event_loops_share_actual_process_concurrency_cap，SQLite database is locked；该测试及其运行时代码未包含在本卡发布diff，无DB操作/运行时修改，不顺手改RAG并发。Agent job为独立任务，失败已隔离记录；全工作流不满足发布通过，RAG gate被跳过。前端job全部通过，后端ruff/mypy通过。

用户随后明确批准F07修订提交推送与一次新CI（最多新增12请求，无重试），Agent门禁通过后执行独立最终验收；不合并部署。以下执行记录将在真实运行后追加；当前旧run与新修订证据严格区分。
