# 验证效能调整与两张业务试用卡交接

日期：2026-10-22，Asia/Shanghai。用户因额度不足要求交接，当前实施暂停，未完成最终验收。

## 1. 给接手 AI 的任务

在现有工作区继续落实 [修复方案](proposal_validation_delivery_efficiency_20261022.md)，完成验证工具、审核顺序和 CI 兼容调整，然后通过 NFR-011、FE-001 两张业务卡试用。用户明确回复“采用这两个任务”。不要重新讨论已批准路线，不要另写同内容计划，不要把当前专项通过当完整交付。

先读当前 AGENTS.md、本文、方案第13节实施记录、tasks.yaml全局策略及VAL-001/NFR-011/FE-001，按影响补相关规则与代码。需要新增事实或决策时再展开，不全文扫描旧台账。

## 2. 授权、范围与工作区

- 仓库：`E:/culture/SmartCustomerServiceSystem/ai-assistant`。
- 分支：`codex/eval-001-report-query`。
- 当前HEAD及本轮基线：`b4efcc5d3e4493c12a73338c1fdca7fb848931be`。
- 用户批准按方案实施一轮，并选择NFR-011与FE-001试用。允许文件详见方案第13节和任务卡。
- 没有新增Git提交/推送/合并/部署授权；没有新增外发源码、真实付费模型或生产操作授权。完成本地可审核候选后再集中处理确实需要的授权。
- GitHub连接器读取默认分支main成功，读取main分支保护403：Resource not accessible by integration。不能声称远程required checks已核验或生效。
- CI先做兼容阶段：保留Backend原RAG阻断步骤。独立RAG job须有明确探针触发/迁移安排，避免未经批准长期重复付费；远程新check被确认为必需后，才移除旧步骤。
- 本轮无提交；交接时没有已知仍在运行的测试session。恢复时核对进程及`data/validation/full.lock`，不盲删锁、不误杀其他任务。

交接时实际Git状态：

```text
 M tasks.yaml
 M tests/conftest.py
?? docs/plans/proposal_validation_delivery_efficiency_20261022.md
?? scripts/validate_delivery.py
?? tests/test_validation_isolation.py
?? tests/test_validation_runner.py
```

本文是用户新授权的交接产物。保留这些未提交改动；恢复时若发现其他改动，先识别来源，不能覆盖。

## 3. 已完成的实际修改

### tasks.yaml与实施计划

- 新增VAL-001，状态in_progress，依赖GOV-003、NFR-001；统一承担方案A/B/C兼容阶段，不是几十张治理卡。
- NFR-011、FE-001仍为backlog，尚未改业务代码。
- 两张试用卡补充了方案记录等必要文档路径；NFR-011同时补README、Swagger、产品需求文档同步路径。不要删除已有验收或改变权限矩阵。
- 方案状态已改为用户批准实施，并追加第13节：每张卡目标、现状、方案、范围、非目标、验收及基线。继续往同一记录补实际结果。

### scripts/validate_delivery.py

当前约300行，标准库实现，无新依赖：

- preflight / targeted / full / summary模式。
- 创建`data/validation/<UTC时间+随机ID>/`唯一目录；本轮SQLite库、pytest临时目录与pytest/Ruff/mypy缓存独立。
- 清空常见LLM/Embedding密钥环境，固定离线检查身份配置；继续依赖tests/conftest屏蔽工作区.env和外网访问。
- 预检解释器依赖、文件创建/重命名/读写及独立SQLite操作；不读取产品.env。
- targeted要求显式测试文件或--frontend，拒绝路径穿越、外部文件、缺失文件与空选择；pytest先collection再执行，collection失败停止。
- full通过原子full.lock互斥，已有或陈旧锁不自动绕过；正常退出核对token后释放锁。
- 命令采用参数数组、shell=False，保留stdout日志/退出码/耗时；超时和取消处理自身进程树，不当通过。
- backend/frontend按域生成输入文件SHA256；相关输入变化将结果标invalidated；前端图标不使后端证据失效，API schema会使前端证据失效。
- result.json原子保存；summary只读本地证据，不把invalidated提升为通过。

### tests/conftest.py

- 新增`_PYTEST_RUN = data/pytest-tmp/run-<uuid>`。
- 默认TMP/TEMP/TMPDIR与默认SQLite库在该唯一目录下。
- pytest未显式指定--basetemp时，使用本轮`_PYTEST_RUN/tmp`，不再清理旧固定run目录。
- 保留原来密钥隔离、网络阻断、身份及安全fixture；没有删失败用例或放宽业务门禁。

## 4. 实际验证与失败记录

解释器必须使用`D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`，Python3.12.0，UTF-8模式。不要使用PATH上的base解释器。

### 已通过

1. 改动前本地基线：全库Ruff退出0；mypy app/退出0，231个源文件。缓存显式放在`data/validation/val001-baseline/`。
2. 1973299到当前HEAD只变更文档与台账。1973299的Ubuntu CI先前全绿，可作当时代码的Linux基线；不是本轮源码修改的最终CI或Windows全量证据。
3. 执行器专项最新命令：

```powershell
$env:PYTHONUTF8='1'
& D:/DepTooL/anaconda3/envs/ai-assistant/python.exe scripts/validate_delivery.py targeted --test tests/test_validation_runner.py --test tests/test_validation_isolation.py
```

退出0，20项测试通过。原始记录：`data/validation/20261022T065816-f7cfdce0e6d9/result.json`，同目录collection.log、pytest.log及pytest.xml。这是专项，不是完整pytest。result中的inputs可用于恢复时核对是否仍有效。

4. 当前执行器、两份新测试和conftest的专项Ruff退出0：

```powershell
$env:RUFF_CACHE_DIR='data/validation/val001-lint-cache'
& D:/DepTooL/anaconda3/envs/ai-assistant/python.exe -m ruff check scripts/validate_delivery.py tests/test_validation_runner.py tests/test_validation_isolation.py tests/conftest.py
```

### 已遇到且已定位的失败

- 初始空实现得到20项失败；其中默认conftest导入写法不对，之后改为`from tests import conftest`。不能把导入失败称为有效业务红测；路径拒绝、互斥、退出码等契约失败才是本轮目标。
- 使用Python write_text覆盖apply_patch新建的脚本时出现PermissionError；后改用apply_patch成功。不要为此改系统ACL或删除用户文件。
- 在conftest尚未修正时，一次专项未传--basetemp，复现旧共享run目录权限问题，出现18个setup ERROR；不是业务失败。唯一目录修复后不再复现。
- Windows路径与SQLite URL斜杠断言导致2项测试失败，已改用Path.as_posix；最新20项通过。
- 一次Ruff未指定缓存目录，旧.ruff_cache写入失败；显式新缓存后通过。不要把环境错误误报代码问题。

## 5. 尚未完成，不能宣称通过

1. 执行器还需审查/补齐：
   - 目前summary仅支持本地结果，没有GitHub最终SHA自动查询整合。
   - 基础设施错误目前主要由预检防止；pytest运行中同源PermissionError的快速止损尚未实现。
   - 输入变化目前在各检查结束时检测，不是运行中实时监控；不要宣称实时中止。
   - 陈旧锁有意保留，不做自动清理；应有清晰占用信息和恢复说明。
   - 检查环境目前只记Python/平台/库路径，依赖版本、基线SHA等最终证据字段还需补齐。
   - 需补实际CLI并发、进程中断、输入变化、非法run路径等必要反例；20项通过不代表这些全部覆盖。
   - snapshot枚举范围需自审：不能读取.env或凭据；不能漏掉实际消费的配置。frontend顶层.env已排除。
2. 规则同步未实施：AGENTS第9/10/11/13节、Cursor全局/测试规则、台账全局策略、交付技能/流程案例、工作流与检查清单仍需按批准方案修改。
3. CI文件尚未修改。不得移除现有RAG门禁或假称远程保护已配置。
4. 首次设施完整pytest尚未启动；修改后的全库Ruff、mypy及Linux/最终SHA CI均未完成。
5. NFR-011与FE-001业务代码、测试、页面验证均未开始。
6. 最后一次给test_health.py补测试的apply_patch因“同一补丁含两次同文件操作”被拒绝，未写入任何健康检查代码。应把同文件的修改合并为一个Update File操作，不要认为已经有新增健康测试。

## 6. 两张试用卡的具体接续设计

### NFR-011

- 现有health：`app/api/routes/health.py`；只有database/vector_store/llm/tools，缺独立embedding字段。
- factory：`app/rag/embeddings/factory.py`；get_embedding_provider支持_override，开发缺密钥回Mock、生产缺密钥抛RuntimeError。Mock提供商model=mock-embedding，并有dim。
- 新增只读availability/metadata函数，不构造真实provider、不调用embed、不探测网络。报告provider/model/dim/is_mock、configured/mock/unavailable/override及connectivity_verified=false/quality_evidence=false；自定义注入provider身份未知时如实标未知，不能把配置模型冒充实际模型。
- health沿用整体数据库/向量库连通性返回语义，Embedding配置展示不改变生产启动fail-fast。区别配置可用与外部链路已验证，不输出API key或base URL。
- 先给`tests/test_health.py`增加行为失败用例：开发缺密钥Mock；有合成密钥的配置展示且禁止构造真实provider；Mock override实际dim；生产缺密钥仍拒绝get_embedding_provider。
- 核对既有检索/评测报告已有模型/维度字段，能复用则说明，不额外付费跑质量报告。同步README、Swagger与产品第六章；导出Swagger使用隔离环境，不能触发真实调用。

### FE-001

- 路由在`frontend/src/App.tsx`，RequirePermission当前无权直接Navigate到/chat。
- Users/Tenants/Status/UserCreate/TenantCreate/Audit页面自身也有无权分支，当前仅EmptyState。单改App守卫不能覆盖所有管理入口。
- 新建统一ForbiddenPage（403文案、说明、返回聊天/可用入口），可由RequirePermission及上述页面拒绝分支直接渲染。是否另设/403路由按现有结构最小实现，必须让用户实际看到完整403页面。
- 保持未登录跳/login、有权角色正常；不把所有非system_admin角色一刀切拦截，Audit允许system_viewer等现有矩阵不变。
- 既有权限函数位于`frontend/src/lib/permissions.ts`：canManageTenants当前仅system_admin；canViewAudit为system_admin/system_viewer。
- 前端Vitest配置只收集`src/**/*.test.ts`、node+SSR；可参考`frontend/src/pages/Evaluations.test.ts`用createElement/renderToString。不要随手新增未安装的浏览器测试依赖。
- 浏览器技术走查和前端typecheck/test/lint/build仍需实际执行，不用SSR测试冒充浏览器验收。

## 7. 推荐继续顺序

1. 核对本文列出的工作区与结果指纹，修完善执行器必要边界；用唯一运行目录跑专项，完成静态检查。
2. 同步规则与审核顺序：本地选测+最终合格CI承担完整回归；首次测试设施修改仍按更严格规则验收。审核前置、限定材料，不把整份tasks/manifest丢给外部审核。
3. 修改兼容CI：pip缓存、结果artifact、独立RAG迁移探针；保留原Backend RAG步骤。远程保护403作为未验证缺口。
4. 用户已选定的两卡逐一开发/专项验证，记录真实耗时；相关业务更改收敛后，整个批次做一次最终本地全量，避免为基础设施和两卡重复开三轮全量。此排序需在方案第13节明确补记；不在尚未验收时把VAL-001写done。
5. 同一工作区只保留一轮完整pytest。修改源码/测试/CI/评测输入后对应旧证据失效；不要全量运行时继续改受影响输入。
6. 当前无Git发布与费用授权，先交付可审核候选。Linux/同最终SHA CI缺失时保留in_review；需要用户授权时集中说明具体提交范围、CI真实调用和远程保护事项。

用户当前已要求交接，不要继续自动实施或启动后台任务。其他AI收到用户继续指令后再按上述顺序推进。

## 8. 可直接复制给其他 AI

> 请先读取AGENTS.md和docs/plans/handoff_validation_efficiency_20261022.md，在现有未提交改动上继续VAL-001，再实现已批准的NFR-011和FE-001试用。方案第13节已经明确目标、范围与验收，沿用有效决定，不重新写同内容方案。先修完善验证执行器、同步规则和兼容CI，再逐卡试用；最终只保留一轮有效完整回归。现有20项设施专项通过不等于完整验收。保留旧Backend RAG门禁，远程保护读取403不得声称已配置。未获额外授权不要提交推送、合并部署、外发源码或调用真实收费模型。真实记录命令、耗时、失败及未验证项，任务不要假done。
