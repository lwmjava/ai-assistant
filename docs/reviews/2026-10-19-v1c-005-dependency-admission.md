# V1C-005 前置卡当前版本准入与历史 CI 缺口补记

最终业务输入版本：9cbe94fffe269cde9d93c00fb6d1f6d25fd01264。当前请求要求关闭V1C-004并启动V1C-005。核对发现V1C-001/002仍标in_review，原因均为历史记录中仅缺最终CI。本次补齐当前版本证据与状态，不新增业务实现或发布操作。

独立复核身份 /root/v1c004_close_recheck，未参与前两卡实现及既有审查。读取原始任务、既有明确业务接受/技术审核、当前关键代码、当前241项回归与最终输入/CI证据。未为本次文档补记重复运行pytest；此前在相同输入亲自执行120项入口测试，通过37.64秒/退出0。

## 授权与历史证据

- V1C-001用户已明确说“V1C-001 用户业务验收通过”，覆盖普通问答、计算工具、知识库问答和隔离故障。原独立最终审核通过，原唯一剩余项为Ubuntu CI。
- V1C-002用户明确委托Agent业务接受，既有40项真实入口接受通过，涵盖执行、失败、取消、身份/参数拒绝、重复/预算及新Session审计。既有独立审核通过，原唯一剩余项为Ubuntu CI。无需让用户重复接受相同契约。

旧514/516输入不能称为一直未变：001有14项、002有15项已被后续获批公共路径工作修改。共通包括fast_path、pipeline、plan_runtime、prompts、skills/base与manager、state、supervisor、tools/executor、chat_service，以及eval_runner、chat_retrieval_lifecycle、rag_028_model_capability、supervisor测试；002另含tool_path_contract测试。当前526项输入已重新绑定，旧清单保留为历史，不拿旧SHA的报告伪装当前证据。

## 当前版本重新复核

- ChatRetriever本体和原独立检索反例未变。Service仍同步固定reader/tenant/read_scope，仅实际retrieve才创建依赖，不存在新的eager构造或权限旁路。
- 检索生命周期夹具仅获批稳定先后时间，业务断言保留。
- 工具短路径显式装配同一ToolExecutor，默认行动循环未注入时回退同一执行器；后续技能和Supervisor可选钩子未恢复注册表直执行。
- 工具取消测试等待实际函数启动再取消，并要求cancelled拒绝审计，强化实际取消证据，未降低门禁。
- 主Agent在当前版本实跑六个既有文件241 passed/1既有warning，77.76秒，退出0；日志SHA256 `f6325f22e643daf45310a429cccfc5bf1d0c86d8e0563e57468510579e8f39e2`。具体命令、环境和完整526输入见baseline.json。
- 同输入无筛选全量2885 passed/3 skipped/3 warnings，860.30秒，退出0；Ruff无缓存和mypy通过。独立复核只核对输入和归档，不冒称再次全跑。
- 主Agent直接查询 [run39268207253](https://github.com/lwmjava/ai-assistant/actions/runs/39268207253)，Backend、Frontend、Agent official baseline均success，SHA就是当前最终业务版本。三项Windows CLI平台跳过的必要Linux门禁由同SHA Ubuntu Backend成功补齐。

## 对账结论

历史独立审核和明确业务接受保留；后续公共路径修改已在最终HEAD重新复核，当前专项、无筛选全量和最终SHA CI通过，补齐原剩余CI门禁。V1C-001、V1C-002状态收口为done；V1C-003已有明确关闭与当前回归，V1C-004有当前新审核及用户关闭指令。未发现未决范围内P1/P2或新业务接受授权缺口，四项前置可供V1C-005准入。

这不是只读done字段推导功能，也不宣称历史旧提交当时已经有CI。仅修改对账文档与状态；未提交、推送、合并、部署或改变执行输入。V1C-005的架构、工具矩阵和允许路径仍待批准，不因前置收口而自动批准。
