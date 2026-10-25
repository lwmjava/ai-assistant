# TRACE-001 实施说明

基线为本批 EVAL 候选工作树。采用 ADR-0021：主库保存脱敏投影，30 天查询窗、无自动物理删除。2026-10-24批准的真实资源设计已将公开页面/API收口为system_admin；Service保留同租户资源过滤，公开入口其他角色403、已准入管理员对不可见/不存在同为404。

实现复用 `AgentTrace` 和 ChatService 的真实执行入口。非调试模式仍记录起止、终态与计数，详细节点事件继续受 `DEBUG_ENABLED` 控制；持久层再次白名单投影，丢弃 Prompt、回答、参数、结果和原始错误。API 仅分页、会话筛选和详情，不提供重放动作。

回滚时移除写入接线、路由和页面，保留已有记录；迁移 downgrade 发现数据即拒绝。专项覆盖持久化、脱敏、所有者/同租户管理员、跨租户不披露、保留窗和未知 usage。

## 2026-10-25 默认配置计数修复与组合验收材料同步

用户授权修复TRACE-001默认非调试模式虚假0计数及EVAL-005验收材料失真。起始分支 `feat/EVAL-002`、HEAD `c4968996b4e8b4ef40f773e7857f6be2e0942c7f`，保留全部已有混合改动（含EVAL-003有界队列修复）。开发前conda解释器运行 `scripts/validate_delivery.py targeted --test tests/test_run_history.py --test tests/test_debug_smoke.py --test tests/test_platform_admin_access.py --test tests/test_evaluation_jobs.py --test tests/test_evaluation_quality.py`，51 passed/1既有弃用warning、7.25秒、exit0；证据 `data/validation/20261025T012937-c3a8b9fe6d1a/result.json`。新增非debug持久计数与debug切换反例在原实现2 failed/6 passed、exit1（`20261025T013129-233610497440`），保留失败记录。

沿用已批准“非debug仍保留摘要计数”的方案：AgentTrace以四个标量独立累计次数/耗时，不保存额外业务文本；summary重复读取和debug切换不清空或重复计数。AgentPipeline阶段、意图分流、质量评分及流式最终回答在实际调用finally登记；保留原权限、超时、预算、Prompt和返回协议。Supervisor自身调用和受控工具委派共享同一trace，ChatService的Supervisor与缺依赖fallback均传trace。模型失败/取消/流式关闭计一次，无实际请求的本层拒绝不计。

工具口径是通过本层守卫后的一次Executor逻辑dispatch，不是底层重试或外部计费次数；在_execute_tool的finally记录成功/失败/取消，移除行动循环成功后的重复登记。skipped/invalid/exhausted继续作为debug决策事件但counted=False。既有工具实际执行、身份撤销、跨租户、取消、预算和checkpoint专项必须保留，不能只调整预计调用总数。持久写入依旧调用RunHistoryService，Token未知仍为null；不迁移、不覆盖旧0计数，不增加模型/工具执行。

EVAL-005只修订现有 `docs/checklists/evaluation-platform-acceptance.md` 和平台Evaluation Case 1.1.0：system_admin唯一平台准入、真实RAG/Agent/Workflow目标、目标租户/执行主体、人工版本或Silver、生成/Judge显式价格/预算/外发确认、Embedding计费、内部Smoke与真实业务质量区分。明确历史3500全量为旧候选证据、已有有限真实合成Smoke不代表业务接受；保留浏览器与最终SHA CI缺口。没有新增付费授权或以材料更新关闭依赖卡。

扩范围前只在两卡既有允许路径内实施。当时普通短路径 `app/agents/fast_path.py` 未接线计数，组合路径 `app/agents/combined_tools.py` 工具取消/异常缺少登记；两者未列入allowed_paths，故先请求范围决定。用户随后明确批准两个文件，本段是当时的范围记录，授权后结果见末节。未委托真人业务接受、未调用真实模型、未改生产数据、未提交/推送。

首轮修复专项 `20261025T013339-0d20b4dc1d3a` 为142 passed/1既有warning、47.86秒、exit0；独立审查随后发现pipeline流式最终回答漏计和工具决策虚增，已补实现及正式反例，故该轮不作为最终输入证据。首次Ruff报新增长行，已折行修复；不放宽规则。最终专项/静态与独立复验结果见下节；所有候选输入由执行器绑定SHA256。

独立公共入口探针又发现首token后close时，内层finally会延迟到垃圾回收，且ChatService先落历史再关闭events。已修复：run_stream和两个最终回答分支显式aclosing内层生成器，_stream_final先关闭provider流再计数；ChatService先close events后_collect_trace，嵌套finally保留收集。新增公共pipeline立即close断言及真实ChatService关闭后持久计数==模型实际调用反例，41 passed/exit0（`20261025T014247-58e4536bbb40`）。未修改权限或调用预算；新反例不靠GC等待制造通过。

### 扩范围前结果与当时缺口（历史）

- 最终组合专项：`data/validation/20261025T014341-49443a3af270/result.json`，350 passed/1既有ast弃用warning、111.52秒、collection/pytest exit0；包括Trace/history/Chat/工具组合/Supervisor/Agent/SSE/预算/撤销/租户/平台权限/评测任务与质量/报告/OpenAPI共22个测试文件，完整命令在result.commands，未筛选-k。
- 调用方补充：同一conda解释器运行 `scripts/validate_delivery.py targeted --test tests/test_chat_resume.py --test tests/test_chat_regeneration.py --test tests/test_chat_regeneration_review.py --test tests/test_chat_multimodal_llm.py --test tests/test_chat_attachments_review.py`，70 passed/1既有warning、14.94秒、exit0，证据 `data/validation/20261025T014511-f410097cca73/result.json`。
- 静态：`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe -m ruff check --no-cache .` exit0；同解释器 `-m mypy app/ --no-incremental --cache-dir data/validation/eval005-trace001-final-mypy`，256源文件、exit0。日志在最终组合目录ruff.log/mypy.log，补充绑定记录supplemental-checks.json。
- 独立审查/受托验收者 `/root/auth005_acceptance`：同一conda解释器运行 `scripts/validate_delivery.py targeted --test tests/test_trace_call_counts.py --test tests/test_run_history.py --test tests/test_chat_controls.py --test tests/test_chat_combined_tools.py`，75 passed/1既有warning、29.10秒；collection/pytest/summary均exit0且指纹匹配，证据 `data/validation/20261025T014450-e650485fa994/result.json`。此前75例虽pytest通过但因Evaluation并行变化被判invalidated的 `20261025T014329-b17828ea8947` exit1保留，不当最终通过证据。
- EVAL-005清单及平台Evaluation 1.1.0（11个唯一Case）可解析，引用文件存在；独立确认原材料失真P2闭合。生命周期Evaluation 1.1.2明确旧内部Smoke才拒绝real/正预算，与真实资源预算契约区分。YAML解析及CRLF感知diff whitespace检查exit0。

扩范围前的上述结果仅接受当时允许路径，非debug计数、stream漏计、决策虚增及公共close落库问题在该范围闭合，没有新范围内未修P1/P2；当时不能据此称所有入口完成。`app/agents/fast_path.py`和`app/agents/combined_tools.py`的两个剩余缺口在用户批准后已继续修复，最新结论以末节为准。工具dispatch口径不冒称底层重试/费用精确次数。

本轮未改共享conftest、测试选择器、验证执行器或CI行为，相关公共调用方已专项覆盖，未发现未覆盖跨模块失败，因此完整后端回归留待最终SHA合格Ubuntu CI，不重复本地全量。尚缺最终提交同SHA必要CI、浏览器走查和代表性业务接受；旧3500全量不替代本次候选。技术专项与允许范围独立复验已完成，整卡业务接受未完成，两个任务保持in_review。后续仅补说明及任务记录，未改变检查输入，复用仍匹配证据。

## 2026-10-25 用户授权后补齐剩余入口

用户明确答复“允许两个文件，继续完整修复”，将 `app/agents/fast_path.py` 与 `app/agents/combined_tools.py` 限定用于Trace传递、调用计数、流关闭及回归，已登记到TRACE-001 allowed_paths。分支/HEAD及其他混合改动不变。开工复核上一候选 `20261025T014341-49443a3af270` 的summary仍passed、638个输入匹配，作为扩展前基线；不无变化重跑350例。

实现仅补接线：ChatService一次性/流式入口向_iter_fast传同一trace；普通/RAG短路径在实际stream finally关闭provider并计一次，逐层aclosing使外层关闭同步传播，不依赖GC。披露语先输出且请求未开始时关闭保持0；工具短路径复用既有AgentPipeline trace及执行器，无新增模型/工具执行。_iter_action_loop准确注解为AsyncGenerator，支持关闭契约。组合工具在守卫/预算/指纹校验后才进入dispatch，以try/finally记录成功/错误/取消一次并恢复身份日志上下文，移除原仅成功后登记；不改变权限、预算、重试、Prompt或业务返回契约。

失败证据：`20261025T015133-da8fca465cd5` 中组合Executor错误/取消、debug开关四例失败（4 failed/39 passed/exit1）；普通短路径需显式启用langgraph路由，修正测试前置后 `20261025T015228-7eb7220e6968` 首token关闭后历史计数仍0（1 failed/9 passed/exit1）。修复后首轮88 passed/exit0（`20261025T015333-03802673efd5`）。随后增加Simple/RAG×成功/错误/取消/close×debug开关16例、披露后提前close零请求、工具短路径实际计数以及真实ChatService关闭落库。新增测试import排序首次Ruff失败，已修正，未放宽规则。

最终候选仍为 `c4968996b4e8b4ef40f773e7857f6be2e0942c7f` 加工作树输入清单；均为Windows/Python3.12.0、conda ai-assistant解释器、隔离SQLite和离线模型，依赖/非敏感配置由result.environment登记，无真实费用或外部请求：

- 主专项：`data/validation/20261025T015500-e8b45c3877b5/result.json`，408 passed/1既有ast弃用warning、83.62秒，collection/pytest/最终summary exit0。覆盖Trace/history/fast/combined、工具契约、RAG短路径调用方、日志最小化、Chat控制、Agent/SSE/预算、Supervisor、撤销与租户生命周期共16文件，完整 `scripts/validate_delivery.py targeted --test ...` 命令在result.commands。
- 评测平台/其他调用方补充：`data/validation/20261025T015646-672948fd1bae/result.json`，172 passed/1既有warning、30.42秒，collection/pytest/最终summary exit0；覆盖评测任务/质量/编排/适配器/API/报告/数据集/对照/OpenAPI/平台权限，以及续跑、重新生成和多模态/附件共15文件，完整命令在result.commands。
- `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe -m ruff check --no-cache .` exit0；同解释器 `-m mypy app/ --no-incremental --cache-dir data/validation/trace001-paths-final-mypy`，256源文件、exit0。日志和补充输入绑定记录归主专项目录ruff.log/mypy.log/supplemental-checks.json。
- 独立审查/受托验收者 `/root/auth005_acceptance`：相关147 passed/1既有warning、30.28秒（`20261025T015600-99ea1d3023e2`），Fast Route调用方13 passed、4.05秒（`20261025T015643-c378647f2a6e`），两轮collection/pytest/summary均exit0且输入匹配。独立核对真实入口、提前关闭、零请求不计、错误/取消与隐私、权限预算顺序及无重复计数，未发现本次范围未修P1/P2。

最终fast_path SHA256 `d30c910972d49b1005251e5b16832e2151b83a625ee9a411674a7ad426375a71`；combined_tools `027ba23b924cffb351b7bc6a70d07a85422f6cbe0011cbb133fe782a858d6fb1`；全体源码/测试/配置/依赖/Evaluation哈希在上述结果中。之后只更新任务/说明，不改验证输入，复核summary后沿用有效结果。YAML解析、引用及CRLF感知Git whitespace检查exit0。

本次已知缺陷（包括两个原卡外缺口）均修复并独立复验，EVAL-005材料P2也已闭合。没有再等待范围决定；整卡仍保留最终SHA完整Ubuntu/必要CI、页面走查及代表性业务接受缺口，保持in_review，不将专项替代门禁、不冒充真人业务接受。无数据库迁移或历史回填，不推断旧0计数；回滚受控回退本次接线并保留历史，未提交/推送/部署。

回滚为受控回退本次计数与Trace传递改动，保留既有主库历史；回退会恢复原计数缺陷，不是上线解决方案。前端/schema/数据库结构未变化，前端契约检查本修复不重复；必要最终完整回归和同SHA CI仍待完成。TRACE-001与EVAL-005保持in_review。
