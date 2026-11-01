# ADR-0024：资源级知识库权限

- 状态：Accepted（2026-10-31 用户批准提案并授权开始实现）；资源 ACL 尚未实现。
- 日期：2026-10-31
- 决策任务：RAG-041；用户已授权按拆分开展业务实现；实际重要数据迁移与生产启用仍须另获授权。

## 事实与进入条件

1.0 已纳入资源 ACL 决策，用户已要求启动本卡。现有 app/rag/access.py 的 tenant 模式允许同租户当前文档检索，不能证明资源 ACL。RAG-035 的 rag-036 真实失败切片作为受限资料进入模型的风险输入；生成输出屏蔽不能替代检索授权。

沿用 [ADR-0001](0001-knowledge-base-permission.md) 的控制面与检索面分离、[ADR-0007](0007-rag-source-verification-permission.md) 的证据核验边界。现有实现与历史 ADR 的管理员 uploader 语义存在差异，实施卡须以已批准的无管理员检索豁免规则验收，不能将当前代码视作新权限决定。

## 备选与建议

建议采用文档级显式可见性（tenant/private/restricted）与同租户用户授权，保留既有读取准入并取交集。仅上传者隔离无法表达同租户指定成员共享；通用角色/组/策略引擎引入额外权限体系，本轮不采纳。跨租户共享、组继承、显式拒绝规则及块级独立授权不纳入本次提案。

## 已批准权限决定

主体来自服务端当前有效 User/Tenant，不能信任请求中的用户、角色或租户声明。资源为租户内文档及其版本，Chunk、父块和摘要继承来源授权，向量 metadata 不作为权威授权来源。

| 动作 | 建议规则 |
|---|---|
| 检索/进入模型/引用核验 | 有既有知识库读取准入且同租户；tenant 可见性允许本租户成员，private 仅上传者，restricted 仅上传者及显式授权用户；再与 tenant/uploader 既有检索范围取交集 |
| 列表/详情/整文件下载 | 保留既有控制面，不因检索授权扩大管理或下载权限；实施前单独核对这些入口与敏感正文暴露风险 |
| 删除/重解析/发布 | 保留既有控制面规则，ACL 读授权不赋予写权限 |
| ACL 查看/变更 | 仅本租户管理员及当前租户内系统管理员；普通上传者不能自行扩大授权；跨租户变更不提供 |

管理员身份不豁免对话检索、核验和摘要授权。ACL 变更属于权限操作，必须沿用人工审批与审计边界，不能由 Agent 自主批准。授权只能指向当前同租户有效主体；未知可见性、主体失效、缺失 ACL、授权查询失败均拒绝，不能回落 tenant 共享。软删、非当前版、生效时间等既有约束继续生效。

过滤在候选正文进入重排/模型前完成；服务端读取权威文档和授权再复核，覆盖 API、Agent、父块、引用及所有后端。后过滤不足 top-k 时有界补取，不能补入未授权内容。禁止在错误、计数、Trace 或拒绝信息中泄露受限正文与资源存在性。

缓存绑定主体、租户、用途及授权版本，每次使用重验；撤销后下一次读取失效。摘要需保存完整来源标识与权限版本，对所有来源取授权交集；任一来源撤销、丢失或不可验证，弃用并从当前有权原文重算。历史引用不可自动指向新版。不得因已有会话归属就复用失权摘要。

## 迁移、启用与回滚

新增文档可见性、显式授权和权限版本的具体 schema 由实施卡评审；版本发布不自动扩大授权。历史资料是否共享不能从内容推断：先盘点，负责人确认共享的文档显式标为 tenant，其余隔离待分类。不得批量默认共享；备份、重要数据回填或重建必须另获授权。默认 RAG_KB_SCOPE=tenant 不变，但启用 ACL 后与 ACL 取交集。

先隔离测试与影子对账，再经迁移核对、独立安全审查、CI 与人工接受后启用。旧程序不理解 ACL 时不得回退为共享读取；回滚应关闭受影响知识库读入口或保留 ACL 执行层并回退其他变更。保留授权、版本和原文，不删除数据。恢复共享必须重新获权限决策授权。

## 已批准实施拆分与验收

1. 数据与授权服务：模型/迁移、人工审批与审计、乐观版本冲突、同租户授权对象验证；记录失败边界及恢复演练。先批准具体 schema 与 allowed_paths。
2. 读链路接入：API/Agent、两种 scope、Milvus/显式 local、父块与核验全部复用受控授权；验证跨租户、管理员、停用、删除、旧版、缺失 ACL、伪造来源和后端错误。
3. 派生上下文治理：摘要、会话快照和缓存来源依赖/撤销版本，覆盖失权、流中撤销、重试与取消；对接 RAG-040 既有摘要约束，不能另建旁路。
4. 迁移与发布验收：隔离 fixture 和版本化 Adversarial/Observed Regression 案例覆盖 rag-036：未授权正文不得进入 selected、模型请求、引用或摘要；输出围栏只作为纵深保护。真实模型费用另获授权，确定性授权验收不依赖真实模型。最终提交须满足后端全量 CI、适用静态检查与平台门禁。

以上顺序为依赖顺序，可共享评审但不可在未批准权限决策前实施。RAG-041 只有在负责人批准本 ADR 与拆分后才能关闭；不代表 ACL 已实现或生成质量发布门禁通过。

## 第一阶段具体实施规格（2026-10-31 用户批准）

采用独立 rag_document_acl 表，document_id 为文档外键主键，tenant_id 为索引，visibility 为 tenant/private/restricted，revision 为正整数（初始 1），updated_by 为操作者，updated_at 为 UTC 更新时间。缺行表示未分类并拒绝读取，避免为历史文档静默授予共享。采用 rag_document_acl_grants 表，以 document_id/user_id 为复合主键并保存 tenant_id；授权对象必须由 Service 复核当前有效用户及租户。两表外键不能替代同租户校验。数据库迁移只新增空表，不回填、不触碰历史正文。

ACL 更新接口必须提交 expected_revision，使用条件更新原子比较版本；冲突回滚整个事务，不先提交 grants。审批复用项目已有的可核验人工审批设施，不接受普通请求的 approved=true。授权状态与审计记录在同一事务落地；审计不保存正文或凭据。实施前先定位已有审批设施；若不能表达本租户文档 ACL 的审批，则停止该写入口并提交最小缺口，不新造审批系统。

第一阶段 allowed_paths：app/models/rag.py、app/rag/access.py、app/rag/resource_acl.py、alembic/versions/（仅新迁移）、tests/test_rag_resource_acl.py、evals/resource_acl/、docs/adr/0024-resource-knowledge-permission.md、tasks.yaml。API、主检索链和缓存接线在后续卡内限定路径；第一阶段交付不宣称运行时已有 ACL 保护。

基线：2026-10-31，codex/pagt-008，HEAD 3b0e322b0775bcc4e18f03eeb005570e251ebeef；已有 Agent 安全批次工作区修改保留。权限决定已批准，具体 schema 审批尚待完成。当前未执行迁移或业务代码变更。实施需从更新后的 develop 建立隔离分支，不能把现有其他任务修改带入交付；远程核对及分支操作在获具体实施规格批准后进行。

实施承接：RAG-046。当前代码检索未发现人工审批模型或执行设施；按已批准失败边界，暂不提供 ACL 写入口、授权变更及乐观更新，不以布尔值替代审批。独立读取服务仅为后续接线基座；迁移仅创建空表，降级明确拒绝删除授权记录。主链路保护仍待后续实施与验收。

## 第一阶段验证记录

Windows；解释器 D:/DepTooL/anaconda3/envs/ai-assistant/python.exe；未提交候选为 HEAD 3b0e322 加下列文件指纹。执行 `scripts/validate_delivery.py preflight` 退出 0；原权限基线 targeted 9 passed。最终执行 `scripts/validate_delivery.py targeted --test tests/test_rag_resource_acl.py --test tests/test_rag_access.py` 退出 0，29 passed（新增20、既有9），结果 data/validation/20261031T095641-0383ac814515，绑定配置/依赖与完整命令见 result.json。`ruff check --no-cache` 四个变更 Python 文件退出0；`mypy app/rag/resource_acl.py --cache-dir data/validation/acl-mypy-cache` 退出0；`alembic heads` 单head fc28fa01b3c4；git diff --check 退出0。默认 ruff/mypy 缓存目录曾权限失败，改用无缓存/可写隔离缓存，未放宽类型规则。一次验证运行期间输入变更被标 invalidated，已重跑，未作为通过证据。

独立审查：子Agent acl_review，输入为本阶段模型/迁移/服务/测试。发现请求主体刷新污染 P2，改列查询并加入连续两次撤销/迁租拒绝反例；复核实际执行快照与时间专项4 passed，无剩余范围内 P1/P2。覆盖可见性、授权撤销、跨租户、管理员、主体停用、迁移及时间；未覆盖主链路、Linux/CI和并发生产迁移。新增表仅在隔离内存库验证；没有执行生产或历史数据迁移。check-only 服务不提交事务。

未完成：人工审批设施接入与授权写/并发版本更新；主检索及派生上下文接线；全量后端与静态检查最终CI、Linux迁移证据、业务接受。现有 confirmations.py 的 OperationConfirmation 仅支持跨租户 delete/reparse/publish，不能作为本租户 ACL 人工审批。ACL仍Partial基座，运行时ACL保护未启用，RAG-046保持in_review。此次纯后端独立模块无前端契约变更，前端专项不适用。

候选文件 SHA256：

- `app/models/rag.py`：`6129a3f7b78fccb73ebc9ed1f7405843517fcdb2c635dcff91e403fe64fc9e4b`

- `app/rag/resource_acl.py`：`d333263f8a40b1ed04fbb340639b5b67cc92f3f02c6c1832338468cfa88b3b15`

- `alembic/versions/fc28fa01b3c4_add_document_resource_acl.py`：`33180d7142af0440e71b8c689389d12c1ce8fc6d178593bdadef7b08f21d2d6d`

- `tests/test_rag_resource_acl.py`：`d18283d39a3b5feb8567533123b7f2b72aca705a2a699a4a24cf579fff1fe01a`

- `evals/resource_acl/adversarial-v1.json`：`2e22dc74e5c0b6796f485af799239082ef406b481198be865381606e597d6baa`

## 人工确认补充决定（2026-10-31 用户批准）

允许同一管理员提出并确认 ACL 变更。实现文档专用确认提案，绑定 document_id、tenant_id、预期ACL版本、规范化visibility/grants及SHA256；确认有效期10分钟，过期、内容或版本变化必须重新提出。首次创建使用expected_revision=0，成功后为1。提案和确认均重新校验当前有效管理员与同租户主体；提出者失效、角色撤销或撤销版本改变也不执行旧提案。确认后直接在同一事务消费提案、条件更新ACL、替换授权并写入AuditLog，失败回滚，禁止无确认直接更新。允许不同有效同租户管理员确认，审计分别保存提出人和确认人。读取与管理能力仍分离。

本阶段仅提供受控Service状态机，不新增公开API/Agent Tool/前端；后续HTTP入口必须从当前鉴权主体调用人工确认动作，不能接受Agent自批或客户端approved布尔值。审批记录不是通用HITL平台；在现有allowed_paths内新增专用模型与新迁移，旧OperationConfirmation语义保持。服务不自行commit，调用方必须在同一事务提交；使用savepoint确保失败不残留部分授权/消费记录。

## 人工确认实现与验证记录（后续证据，取代先前写入口阻断）

用户已批准文档专用人工确认，允许同人提出并确认。已新增 DocumentACLProposal 与迁移 fd39ab12c4d5（父 fc28fa01b3c4，单 head），以及 propose_acl_change / confirm_acl_change 服务。提案不改变权限；确认使用当前管理员身份、10分钟时限、规范化payload摘要与expected_revision，首次创建版本0→1，其后CAS递增。不能更改原提案内容后继续确认；失效需新提案。提出者/确认者、租户、文档及授权对象每次复核；锁定提案，等待授权锁后重新检查服务端时间。确认消费、ACL、授权替换与AuditLog同事务；不通过异步降级审计替代事务记录。调用方负责commit，失败使用保存点回滚，SQLite显式保持外层事务，避免RELEASE保存点提前提交。旧确认工具不变，未提供Agent工具、公开API或前端入口。

基线复用上一阶段29 passed及其绑定源码指纹（当前开工读取源码未变）；分支/HEAD仍 codex/rag-resource-acl / 3b0e322b0775bcc4e18f03eeb005570e251ebeef，其他任务工作区修改保留。开发专项曾36 passed/1 failed，暴露SQLite外层rollback未保留，已修复并纳入正式回归。开发中全量mypy写缓存及一次验证记录因E盘空间耗尽失败（退出1），仅清理本任务两处mypy缓存恢复空间，未删除源码/业务数据/报告；失败不算业务通过，后续改用cache-dir nul完整重跑。

最终证据：Windows，Python3.12.0，pytest9.0.3；D:/DepTooL/anaconda3/envs/ai-assistant/python.exe。执行 scripts/validate_delivery.py targeted --test tests/test_rag_resource_acl.py --test tests/test_rag_access.py --test tests/test_migration_single_track.py --test tests/test_rag_permission_semantics.py --test tests/test_rag_040_section_summaries.py，退出0，88 passed（资源ACL49、旧权限9、单轨迁移14、权限语义2、摘要14）；报告 data/validation/20261031T104921-6c17296bf3d1/result.json（完整命令、配置/依赖、环境与输入指纹），collection/pytest均退出0。原文与测试数据库不混用，验证器使用独立pytest.db与空模型密钥。完整后端pytest未运行，拟由最终SHA Ubuntu CI承担，不能以此专项替代。

其他实际检查：python -m ruff check --no-cache . 退出0；python -m mypy app/ --cache-dir nul 退出0（260 source files）；python -m alembic heads 退出0（fd39ab12c4d5）；git diff --check退出0。mypy无缓存仅解决空间，不改变follow_imports或类型规则。测试后仅修改本ADR与任务说明，未改变代码/测试/配置/评测输入，复用这些证据。

独立审查者 acl_confirmation_review，输入为本次提案模型、新迁移、服务和正式测试。覆盖身份/撤销、租户、内容与版本绑定、一次消费、授权替换、审计/保存点。发现等待授权锁后仍使用旧时间执行的P2，已在锁后重采样时间并加提案行锁，新增正式到期回归。限定复核实际执行 expiry_is_rechecked 专项退出0，1 passed，无剩余范围内P1/P2；主链路/API未接线、PostgreSQL真并发、Linux未覆盖。SQLite双线程同提案竞争已纳入正式回归，最多一次权限改变与一次审计，锁冲突失败关闭，不自动重试。

未完成：最终提交的完整后端/Ubuntu CI、Linux迁移证据与业务接受；本机wsl枚举报E_ACCESSDENIED，未取得Linux执行证据。未提交、未推送、未合并，未执行历史或生产数据迁移。RAG-046保持in_review；主检索/派生上下文接线及人工HTTP/管理页面入口属于后续阶段。前端契约没有变化，本阶段前端检查不适用。RAG-041仅按文档决策契约关闭，不能表示资源ACL已在运行时启用。

最终候选业务/测试/评测文件SHA256：

- `app/models/rag.py`：`ead946462d79608c32c679eef16501ce5c666f7af10841d364839f09ef7cf499`

- `app/rag/resource_acl.py`：`d872dcf6c355154168f378f18a5f1a3a62c155400aaf6daa4e04cc10bd51b539`

- `alembic/versions/fc28fa01b3c4_add_document_resource_acl.py`：`33180d7142af0440e71b8c689389d12c1ce8fc6d178593bdadef7b08f21d2d6d`

- `alembic/versions/fd39ab12c4d5_add_document_acl_proposals.py`：`144852c86f602f545bebbb45b20d1b1a82f5585f0f4f57542e532450d26594d8`

- `tests/test_rag_resource_acl.py`：`a04a0dd39bd09881b1981cd527fd8fe0979da898076236b1a9a732ef03e16813`

- `evals/resource_acl/adversarial-v1.json`：`4c4267ea8eed594e71e28bba752e021347f700ed13ae5b4a313a86b2f7600c1a`

## 后端接线实施范围（2026-11-01 用户授权继续）

RAG-047承接后端入口与读链路，RAG-046先固定原候选全量证据。已核对API routes/rag.py、Service.search、三种backend、local/Milvus权威SQL回查、evidence、cache及services/cache_scope。接线必须在外部重排/模型看到正文前授权，不以最终响应过滤替代；来源核验和派生上下文需再次校验，不能把有缓存或管理身份当作授权。

实施顺序：先接受控管理员提案/人工确认API及契约测试；随后将有效主体贯穿ReadScope与向量候选回查、父块与引用；最后纳入授权版本的缓存/摘要复核。无ACL历史资料不自动授权，不执行回填与生产启用；上线前须完成资料分类与失败关闭演练。API使用当前鉴权主体，不接受客户端主体/租户或approved标记，Agent不得调用人工确认入口。若部署使用旧共享行为，启用ACL必须显式配置并先完成分类，不能自动把已有共享声明为ACL保护。

本轮全量验证运行 data/validation/20261031T173540-9bd956c9109c，3895项已收集，运行中保持源码不变；报告与源码指纹待完成后登记。E盘空间紧张，仅清理本任务以往运行的tmp与pytest缓存，保留日志/报告，不清理业务数据。

## 2026-11-01 推进证据（未关闭）

RAG-046新增本地全量尝试：E盘run 20261031T173540-9bd956c9109c收集3895项，运行中E盘耗尽，停止隔离pytest，验证器保存报告也出现ENOSPC；该轮不是通过，也没有完整失败汇总。仅清理本任务旧run/tmp与pytest缓存，保留日志/迁移失败现场。日志中的磁盘错误/中断不当作业务断言结果。

专项复现旧迁移调用方：反馈迁移报table already exists，已给两条ACL迁移增加已存在表幂等兼容检查，关键PK/FK/非空不兼容拒绝，同名索引校验列；不降低授权数据降级保护。feedback测试新增直接目标d7e0f3a21b44验证自己的data exists拒绝；chunk_plan测试只往返该迁移自己的a1b2c3d4e5f6版本，保留legacy数据断言。独立审查acl_confirmation_review发现缺主键结构可被接受的P2，已修复并新增正式回归，限定复核7 passed，无剩余范围内P1/P2。

RAG-047当前新增POST /rag/documents/{document_id}/acl-proposals与POST /rag/acl-proposals/{proposal_id}/confirm。当前鉴权主体进入既有Service校验管理员、租户与撤销；提案201，确认200及revision；禁止approved/伪造主体字段，普通成员403，失效/重放409。无Agent Tool或管理页面接线。独立API验证3 passed，后续检索/引用/缓存/摘要部分尚未实施。

最终原工作区专项：指定conda解释器执行scripts/validate_delivery.py targeted --test tests/test_rag_resource_acl_api.py --test tests/test_chat_feedback_migration.py --test tests/test_chunk_plan_migration.py --test tests/test_rag_resource_acl.py，退出0，58 passed，run data/validation/20261031T175212-b4466e133aeb。python -m ruff check --no-cache . 退出0；python -m mypy app/ --cache-dir nul退出0，260 files；git diff --check退出0。此次后端API改变需要后续前端对接，当前无前端代码改动/调用方，不宣称已完成页面验收。

C盘验证副本：C:/Users/123/.codex/visualizations/2026/10/31/01a19143-9a9e-7490-80dc-6d19dd2d8538/acl-validation-workspace。复制2070个版本化及未忽略源码/测试文件，不复制.env或业务数据库，以source-snapshot.json记录SHA256。从原仓库只读获取HEAD到副本Git元数据，使Agent门禁保留真实git_identity校验；初始化前那轮缺Git身份的测试失败为环境缺口，已停止，未放宽门禁。副本full run 20261031T174825-be98e9a112ec仍运行；它绑定副本快照，后续原工作区迁移约束补强及新回归并未纳入，结果只能作为该快照诊断，不能当最新候选全绿。

Linux探测：提升读取权限后wsl仅列出docker-desktop，Docker Linux引擎管道不存在；未取得Linux执行证据，没有下载镜像或启动容器。最终SHA CI、Linux迁移、完整最新候选回归及业务接受仍缺；RAG-046保持in_review，RAG-047保持in_progress。未提交/推送/合并或执行生产迁移。

当前变更指纹：

- `app/api/routes/rag.py`：`23f9ae2d5fb37297bfb63d8de676475709a67ac6f64160a2ed3020d4d2fc398a`

- `alembic/versions/fc28fa01b3c4_add_document_resource_acl.py`：`4b42f4d3e17bce7f4be10676ba3f8fe5da0f38caf79ce70f2b528c8fe01b0f77`

- `alembic/versions/fd39ab12c4d5_add_document_acl_proposals.py`：`ea8a85dd85021d2bdf24212bdfa3bb088fcd4d3c8acc1bb7ad2c8294cf898740`

- `tests/test_chat_feedback_migration.py`：`a46fda479de5405ac5d68c0abca544f6d26e899267384e5dd0c6c064baf1a3d0`

- `tests/test_chunk_plan_migration.py`：`2d2899297ed9db692f84ee48d776a85fe812e14b440a68ee547f3cdfacf646d8`

- `tests/test_rag_resource_acl.py`：`c5ad5cd310b0d914d68ade60e5830d3eb6f1f18944255765f1bf4ca20d95b538`

- `tests/test_rag_resource_acl_api.py`：`ad7fa1bd05fc8258c65eb22cb0ea022690cf9f44727e5f0a47e8f5497492a637`

## 最新原工作区全量结果（2026-11-01）

直接对原工作区最终源码执行不筛选pytest，复用validate_delivery的check_environment、WorkspaceLock、run_command和input_snapshot，未修改验证器；将独立测试库、basetemp、缓存和日志放到C盘短临时目录，以避开E盘容量与长路径。完整命令与输入SHA256见data/validation/20261101-latest-source-full/result.json，pytest日志和XML已从临时目录复制保留。结果退出1：3851 passed、19 failed、31 skipped，1103.94秒；不是通过。全量已结束，没有留待运行的验证任务。19项失败尚未全部完成原HEAD对照，不得一概称为存量或环境问题；不修改无关安全批次，也不关闭RAG-046。

本次扩大专项包含workflow_timeout迁移自身往返与quota探针补强，data/validation/20261031T180902-9889c9ca1d4e，74 passed、1 warning、退出0。quota探针已显式验证升级列及(9,2,2026-10)写入值，独立审查限定复核闭合。检索/父块/引用/缓存与摘要接线仍未实施；当前仅人工提案/确认API，不宣称资源ACL已在读链路生效。
