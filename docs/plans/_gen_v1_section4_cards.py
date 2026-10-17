"""One-shot: append plan §4 cards into tasks.yaml. Not part of runtime."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
TASKS = ROOT / "tasks.yaml"

cards: list[dict] = []


def add(**kwargs) -> None:
    cards.append(kwargs)


add(
    id="COV-001",
    title="覆盖率数字：可复现报告",
    status="backlog",
    priority="P0",
    depends_on=[],
    phase=1,
    problem="现在没有可复现的覆盖率报告，不能把 §4.5 的「不低于 70%」写成已经达到。",
    facts=[
        (
            "覆盖率数字已从 QA-005 划出，由本卡单独验收。",
            "docs/plans/plan_v1_task_split_20261002.md C9、§4.1",
        )
    ],
    allowed=["scripts/", "pyproject.toml", "frontend/package.json", "docs/", "tasks.yaml", ".github/"],
    forbidden=[".env"],
    deliverables=["一次可复现的覆盖率数字，并写明命令"],
    non_goals=["不把没有数字的结果写成达标", "不为凑数字新增无意义测试", "不在本条修测试失败"],
    acceptance=["命令退出后能读到覆盖率数字", "没有数字则本卡不完成", "是否达到 70% 另记，不把未达到写成达到"],
    risk="L0",
    side="coverage-report",
    rollback="删除报告文件即可。",
    cases=["跑一次覆盖率命令并读出数字"],
    commands=["pytest --cov=app --cov-report=term-missing"],
)

add(
    id="ENV-001",
    title=".env.example 覆盖全部配置字段",
    status="backlog",
    priority="P0",
    depends_on=[],
    phase=2,
    problem="app/core/config.py 有 125 个配置字段，.env.example 有 83 个键。",
    facts=[("字段数与示例键数不一致。", "app/core/config.py；.env.example")],
    allowed=[".env.example", "app/core/config.py", "README.md", "tasks.yaml"],
    forbidden=[".env", "生产密钥"],
    deliverables=["125 个字段都有键，并标明必填或选填"],
    non_goals=["不新增配置项来凑数", "不把密钥写进示例"],
    acceptance=["对照结果是 125/125", "没有示例里多出来的键"],
    risk="L1",
    side="env-example-extended",
    rollback="回退 .env.example 提交。",
    cases=["脚本核对 config 字段与示例键"],
    commands=["python -c \"from app.core import config; print('ok')\""],
)

add(
    id="ENV-002",
    title="示例口令退出仓库",
    status="backlog",
    priority="P0",
    depends_on=["ENV-001"],
    phase=2,
    problem=".env.example 里有一段可直接登录的管理员口令。",
    facts=[("§5.3.9 要求初始口令不进仓库。", "docs/product/项目产品需求方案.md §5.3.9")],
    allowed=[".env.example", "README.md", "tasks.yaml"],
    forbidden=[".env", "生产密钥"],
    deliverables=["示例只留占位", "配置默认口令仍为空串"],
    non_goals=["不把真实口令写进仓库、日志、接口样例和测试夹具"],
    acceptance=["示例中不再有可直接登录的口令", "用户名和口令都非空才创建管理员，这条保持"],
    risk="L1",
    side="example-password-removed",
    rollback="回退示例提交。",
    cases=["检索示例文件无可用口令"],
    commands=["rg -n PASSWORD .env.example"],
)

add(
    id="INST-004",
    title="内置账号 admin/system/culture 与首次改密",
    status="backlog",
    priority="P0",
    depends_on=[],
    phase=2,
    problem=(
        '现在只能从环境变量引导名为 admin 的系统管理员。没有 system、cul'
        'ture，也没有首次改密标记。'
    ),
    facts=[("§5.3.9 要求三人与首次改密。", "docs/product/项目产品需求方案.md §5.3.9")],
    allowed=[
        "app/core/",
        "app/models/",
        "app/services/",
        "app/api/",
        "alembic/",
        "frontend/src/",
        "tests/",
        "tasks.yaml",
        ".env.example",
    ],
    forbidden=[".env", "生产密钥"],
    deliverables=[
        "admin 为超级管理员，system 为默认租户管理员，culture 为内置测试用户",
        "初始口令只来自环境变量",
        "三人首次登录必须先改密",
    ],
    non_goals=["不把初始口令写进仓库", "不在前端包和后端放同一把可逆密钥"],
    acceptance=["未改密不能进入对话和其他业务页", "库里只存 bcrypt", "这三人的邮箱可以空"],
    risk="L2",
    side="builtin-accounts-force-change",
    rollback="回退账号与改密标记提交。",
    cases=["三账号首次登录强制改密"],
    commands=['pytest -k "admin or password or setup"'],
)

add(
    id="CLI-002",
    title="init 写入 JWT，跳过 init 时拒绝默认密钥",
    status="backlog",
    priority="P0",
    depends_on=["CLI-001"],
    phase=2,
    problem="跳过 init 时，非开发环境仍可能用默认密钥启动。",
    facts=[("§5.11 要求 init 写入 JWT。", "docs/product/项目产品需求方案.md §5.11")],
    allowed=["app/", "scripts/", "tests/", "tasks.yaml", "README.md"],
    forbidden=[".env", "生产密钥"],
    deliverables=["按 §5.11，init 写入 JWT"],
    non_goals=["不改已登录会话的业务语义"],
    acceptance=["跳过 init 时，非开发环境拒绝用默认密钥启动"],
    risk="L2",
    side="init-jwt-enforced",
    rollback="回退 init 与启动校验提交。",
    cases=["非开发环境跳过 init 应失败"],
    commands=['pytest -k "init or jwt"'],
)

add(
    id="NFR-012",
    title="Compose 口令与跨域默认收紧",
    status="backlog",
    priority="P0",
    depends_on=["NFR-003"],
    phase=2,
    problem="生产编排有可预测的数据库口令，CORS_ORIGINS 默认为 *。",
    facts=[
        (
            "差距表要求生产不能用可预测口令，也不能默认放行全部来源。",
            "docs/product/企业级上线差距-2026-10-02.md",
        )
    ],
    allowed=["docker-compose.yml", "docker-compose.prod.yml", "deploy/", ".env.example", "docs/", "tasks.yaml"],
    forbidden=[".env", "生产密钥"],
    deliverables=["生产编排不使用可预测数据库口令", "跨域不默认 *"],
    non_goals=["不放宽 JWT_SECRET_KEY 必填"],
    acceptance=["JWT_SECRET_KEY 仍强制必填", "上述两项默认值不再出现在生产编排里"],
    risk="L2",
    side="compose-defaults-hardened",
    rollback="回退编排默认值提交。",
    cases=["检查生产编排默认值"],
    commands=['rg -n "CORS_ORIGINS|POSTGRES_PASSWORD" docker-compose*.yml'],
)

add(
    id="API-001",
    title="对外路径改为 /api/v1",
    status="backlog",
    priority="P0",
    depends_on=[],
    phase=2,
    problem="需求写 /api/v1，app/main.py 把路由挂在 /api。",
    facts=[("2026-10-02 定为对外路径 /api/v1。", "docs/plans/plan_v1_lock_20261002.md")],
    allowed=["app/main.py", "app/api/", "frontend/src/", "tests/", "README.md", "docs/", "tasks.yaml"],
    forbidden=[".env"],
    deliverables=["挂载和现有调用方改为 /api/v1", "OpenAPI 与 README 的路径一致"],
    non_goals=["不保留 /api 作为另一套对外路径", "不在本条改业务规则"],
    acceptance=["已注册的对外路径都在 /api/v1 下", "README 里的路径能在 OpenAPI 里找到"],
    risk="L2",
    side="api-prefix-v1",
    rollback="回退前缀提交。",
    cases=["OpenAPI 路径均以 /api/v1 开头"],
    commands=['pytest -k "openapi or api"'],
)

add(
    id="SSE-001",
    title="SSE 事件协议对齐 §5.1.4",
    status="backlog",
    priority="P0",
    depends_on=[],
    phase=3,
    problem="现有事件缺少 run_id、heartbeat、Last-Event-ID 回放等约定。",
    facts=[
        (
            "当前事件是 stage/token/tool 等，与 §5.1.4 不完全一致。",
            "app/services/chat_service.py；docs/product/项目产品需求方案.md §5.1.4",
        )
    ],
    allowed=["app/services/", "app/api/", "app/agents/", "frontend/src/", "tests/", "tasks.yaml"],
    forbidden=[".env"],
    deliverables=["对齐 §5.1.4 的事件"],
    non_goals=["不把引用侧栏算进本条，引用由 PRAG-003 锁定"],
    acceptance=[
        "事件带 run_id",
        (
            '含 run 开始与结束、节点进出、intent、plan、分开的 tool.ca'
            'll 与 tool.result、citations、heartbeat、断线按'
            ' Last-Event-ID 回放'
        ),
        "缺一项不锁定",
    ],
    risk="L2",
    side="sse-protocol-aligned",
    rollback="回退 SSE 事件提交。",
    cases=["流式对话检查事件字段"],
    commands=['pytest -k "stream or sse"'],
)

add(
    id="RAG-014",
    title="清洗链与删除向量补偿",
    status="backlog",
    priority="P0",
    depends_on=[],
    phase=3,
    problem="导入只会压缩空白。删除路径失败后没有补偿队列。",
    facts=[("§5.2.2 与 §5.2.4 要求清洗与补偿。", "docs/product/项目产品需求方案.md §5.2")],
    allowed=["app/rag/", "tests/", "tasks.yaml"],
    forbidden=[".env", "evals/reports/rag-v0.1-baseline-20260919.json"],
    deliverables=["上传路径做去重、质量评分、格式规范化、元数据提取", "向量删除失败进入补偿队列"],
    non_goals=["不改切分策略来代替清洗", "不把 2026-09-29 的五条脚本当作本条证据"],
    acceptance=["四步都在上传路径上", "有一次删除失败后再成功的记录"],
    risk="L2",
    side="rag-clean-compensate",
    rollback="回退清洗与补偿提交。",
    cases=["上传清洗与删除失败补偿"],
    commands=['pytest -k "import or delete or clean"'],
)

add(
    id="RAG-015",
    title="默认向量库切到 Milvus",
    status="backlog",
    priority="P0",
    depends_on=["RAG-014"],
    phase=3,
    problem="默认仍是 local。需求写的是 Milvus 存储。",
    facts=[("默认 RAG_VECTOR_STORE 仍是 local。", "app/core/config.py")],
    allowed=[
        "app/rag/",
        "app/core/config.py",
        ".env.example",
        "docker-compose.yml",
        "tests/",
        "docs/",
        "tasks.yaml",
    ],
    forbidden=[".env", "生产数据"],
    deliverables=["默认存储切到 Milvus，上传会写入"],
    non_goals=["不把 2026-09-29 的五条脚本当作这次切换的证据", "不在本条改检索公式"],
    acceptance=["默认配置下一次上传能在 Milvus 中查到", "切回 local 的开发路径要写明，不能静默丢数据"],
    risk="L2",
    side="milvus-default",
    rollback="默认切回 local 并回退提交。",
    cases=["默认配置上传后检索命中"],
    commands=['pytest -k "milvus or vector"'],
)

add(
    id="CHAT-006",
    title="助手消息重新生成",
    status="backlog",
    priority="P1",
    depends_on=[],
    phase=3,
    problem="最后一条助手消息没有重新生成。",
    facts=[("frontend 无 regenerate。", "frontend/src")],
    allowed=["app/services/", "app/api/", "frontend/src/", "tests/", "tasks.yaml"],
    forbidden=[".env"],
    deliverables=["最后一条助手消息可以重新生成"],
    non_goals=["不做对话分支"],
    acceptance=["重新生成后保留可区分的一次新回复", "失败时原回复仍在"],
    risk="L1",
    side="chat-regenerate",
    rollback="回退重新生成提交。",
    cases=["重新生成成功与失败"],
    commands=['pytest -k "regenerat or chat"'],
)

add(
    id="LLM-001",
    title="模型按 P0-8 任务类型配置",
    status="backlog",
    priority="P0",
    depends_on=[],
    phase=3,
    problem="只登记 chat、intent、fallback。嵌入另有环境变量，没有重排任务档。",
    facts=[("2026-10-02 定为按 chat/intent/embedding/rerank 配。", "docs/plans/plan_v1_lock_20261002.md")],
    allowed=["app/llm/", "app/core/config.py", ".env.example", "tests/", "tasks.yaml"],
    forbidden=[".env", "生产密钥"],
    deliverables=["按 P0-8 配齐 chat、intent、embedding、rerank 和兜底链"],
    non_goals=["不为五个阶段各自配模型"],
    acceptance=["未知任务拒绝", "熔断后走兜底链", "规划、执行、质量门用 chat"],
    risk="L2",
    side="task-model-routing",
    rollback="回退路由配置提交。",
    cases=["未知任务拒绝与兜底链"],
    commands=['pytest -k "llm or factory or profile"'],
)

add(
    id="CHAT-007",
    title="会话附件：粘贴图片与上传文件",
    status="backlog",
    priority="P0",
    depends_on=[],
    phase=3,
    problem="输入框只有文字，不能粘贴图片，也不能选文件。",
    facts=[
        (
            "§5.1.7 已写类型、1MB、图片交模型、文件不进知识库。",
            "docs/product/项目产品需求方案.md §5.1.7",
        )
    ],
    allowed=["app/api/", "app/services/", "app/models/", "frontend/src/", "tests/", "tasks.yaml"],
    forbidden=[".env", "生产数据"],
    deliverables=[
        "按 §5.1.7：png/jpg 可粘贴或选择并直接交给模型",
        "列出的文档和源码随该条消息交给模型",
    ],
    non_goals=["不把这些文件写入知识库，不建索引", "不增加 §5.1.7 列表以外的类型", "不含 .tsx、.jsx"],
    acceptance=[
        "单文件超过 1048576 字节或扩展名不在列表内则拒绝，且不进知识库",
        "允许的图片到达模型",
        "允许的文档和源码到达该条消息，知识库文档数不因此增加",
    ],
    risk="L2",
    side="chat-attachments",
    rollback="回退附件上传提交。",
    cases=["超限拒绝、图片交模型、文件不进知识库"],
    commands=['pytest -k "attach or upload or chat"'],
)

add(
    id="FLOW-003",
    title="工作流到点触发写入执行记录",
    status="backlog",
    priority="P1",
    depends_on=["FLOW-001"],
    phase=4,
    problem="手动执行会写 WorkflowExecution。到点比较进不了执行。",
    facts=[
        (
            "next_run_time 与 scheduler 条件导致 cron 走不到执行。",
            "app/workflow/engine.py；app/workflow/scheduler.py",
        )
    ],
    allowed=["app/workflow/", "tests/", "tasks.yaml"],
    forbidden=[".env"],
    deliverables=["到点时进入执行并写入执行记录"],
    non_goals=["不用手动执行代替本条", "不在本条做重试，重试是 PWFL-001"],
    acceptance=["设定的下一次时间到达后有执行记录", "只调用手动执行路径不能算通过"],
    risk="L2",
    side="workflow-cron-fires",
    rollback="回退调度比较逻辑提交。",
    cases=["到点触发产生执行记录"],
    commands=['pytest -k "workflow or schedul"'],
)

add(
    id="EVO-001",
    title="Reflect 与夜间蒸馏可锁定运行",
    status="backlog",
    priority="P1",
    depends_on=[],
    phase=4,
    problem="开关打开时，对话结束后的反思和夜间蒸馏没有可锁定的运行记录。默认关闭被当成了完成。",
    facts=[("蒸馏调度默认关闭。", "app/core/config.py；app/evolution/")],
    allowed=["app/evolution/", "app/services/", "app/core/config.py", ".env.example", "tests/", "tasks.yaml"],
    forbidden=[".env"],
    deliverables=["开关打开时，对话结束后能反思", "夜间蒸馏按配置跑完并留下记录"],
    non_goals=["不把默认关闭算完成", "不在本条把反思结果直接改成技能"],
    acceptance=["打开开关的一次对话有反思记录", "一次按配置跑完的蒸馏有记录"],
    risk="L2",
    side="reflect-distill-on",
    rollback="关闭开关并回退提交。",
    cases=["打开开关后反思与蒸馏有记录"],
    commands=['pytest -k "reflect or distill or evolution"'],
)

add(
    id="AUTH-005",
    title="对话读取权限与矩阵一致",
    status="backlog",
    priority="P0",
    depends_on=[],
    phase=5,
    problem=(
        'conversations.read 允许 system_viewer 和 vi'
        'ewer。§5.3.2 里这两类角色的对话权限是不允许。'
    ),
    facts=[
        (
            "权限矩阵与实现冲突。",
            "app/core/security.py；docs/product/项目产品需求方案.md §5.3.2",
        )
    ],
    allowed=["app/core/security.py", "app/api/", "tests/", "tasks.yaml"],
    forbidden=[".env"],
    deliverables=["这两类角色的对话读权限与 §5.3.2 一致"],
    non_goals=["不在本条改矩阵里尚未核对的格子"],
    acceptance=["与 §5.3.2 不一致的读请求被拒绝", "若矩阵改写，代码跟改后的矩阵"],
    risk="L2",
    side="conversation-read-matrix",
    rollback="回退权限表提交。",
    cases=["viewer 与 system_viewer 读对话被拒"],
    commands=['pytest -k "permission or conversation"'],
)

add(
    id="FE-001",
    title="非系统角色管理页进入 403 页",
    status="backlog",
    priority="P0",
    depends_on=[],
    phase=5,
    problem="非系统角色打开管理页时，只有组件能识别 403，没有独立页面。",
    facts=[("§4.5.2 要求进入 403 页。", "docs/product/项目产品需求方案.md §4.5.2")],
    allowed=["frontend/src/", "tests/", "tasks.yaml"],
    forbidden=[".env"],
    deliverables=["进入 403 页"],
    non_goals=["不在本条改权限矩阵"],
    acceptance=["非系统角色打开管理页看到 403 页，而不是只有一段组件内提示"],
    risk="L1",
    side="frontend-403-page",
    rollback="回退前端路由提交。",
    cases=["非系统角色访问管理页"],
    commands=["cd frontend && npm run typecheck"],
)

add(
    id="SEC-005",
    title="上传按文件头校验魔术数字",
    status="backlog",
    priority="P0",
    depends_on=[],
    phase=5,
    problem="类型可能只按扩展名放行。",
    facts=[("知识库存储按扩展名白名单。", "app/rag/document_storage.py")],
    allowed=["app/rag/", "app/api/", "app/services/", "tests/", "tasks.yaml"],
    forbidden=[".env"],
    deliverables=["知识库上传和会话附件都按文件头判断"],
    non_goals=["不扩大 §5.1.7 的允许类型"],
    acceptance=["扩展名在列表内但文件头不符的被拒绝", "扩展名不在列表内的仍拒绝"],
    risk="L2",
    side="magic-byte-check",
    rollback="回退校验提交。",
    cases=["伪造扩展名被拒"],
    commands=['pytest -k "upload or magic or storage"'],
)

add(
    id="INV-004",
    title="邀请码角色有效期次数可配置",
    status="backlog",
    priority="P0",
    depends_on=[],
    phase=5,
    problem="角色、有效期和次数写死为 7 天、1 次、member。",
    facts=[("§5.3.3 要求可配置。", "docs/product/项目产品需求方案.md §5.3.3")],
    allowed=["app/", "frontend/src/", "tests/", "tasks.yaml", ".env.example"],
    forbidden=[".env"],
    deliverables=["角色、有效期、次数可配置", "写死值只作为默认"],
    non_goals=["不在本条把邀请绑到指定的人"],
    acceptance=["改配置后，新邀请码使用新的角色、有效期和次数"],
    risk="L1",
    side="invite-configurable",
    rollback="回退邀请配置提交。",
    cases=["改配置后新邀请码生效"],
    commands=["pytest -k invite"],
)

add(
    id="QUOTA-002",
    title="Token 配额与自然月重置",
    status="backlog",
    priority="P1",
    depends_on=["QUOTA-001"],
    phase=5,
    problem="有消息条数和源文件字节上限。没有 Token 配额，也没有自然月重置。",
    facts=[("§5.3.6 要求 Token 与自然月重置。", "docs/product/项目产品需求方案.md §5.3.6")],
    allowed=["app/", "frontend/src/", "tests/", "tasks.yaml"],
    forbidden=[".env"],
    deliverables=["消息数、Token、存储都有上限", "自然月重置"],
    non_goals=["不把空上限改成强制限制"],
    acceptance=["超限返回用量和重置时间，且不产生部分写入"],
    risk="L2",
    side="quota-token-monthly",
    rollback="回退配额提交。",
    cases=["超限返回用量与重置时间"],
    commands=["pytest -k quota"],
)

add(
    id="CHAT-008",
    title="会话软删除与归档",
    status="backlog",
    priority="P1",
    depends_on=[],
    phase=5,
    problem="删除是硬删除。会话没有归档字段。",
    facts=[("delete_conversation 使用 session.delete。", "app/services/chat_service.py")],
    allowed=["app/models/", "app/services/", "app/api/", "alembic/", "frontend/src/", "tests/", "tasks.yaml"],
    forbidden=[".env", "生产数据"],
    deliverables=["删除改为软删除", "可以归档"],
    non_goals=["不在本条做训练导出"],
    acceptance=["软删除后默认列表不再出现该会话，数据仍可按删除状态找到", "归档后进入归档列表"],
    risk="L2",
    side="conversation-soft-delete",
    rollback="回退软删除与归档提交。",
    cases=["软删除与归档列表"],
    commands=['pytest -k "conversation or archive or delete"'],
)

add(
    id="EXPORT-002",
    title="训练格式导出 Alpaca 或 ShareGPT",
    status="backlog",
    priority="P1",
    depends_on=["EXPORT-001"],
    phase=5,
    problem="租户管理员还不能导出 Alpaca 或 ShareGPT。",
    facts=[("现有导出是对话原文 JSON。", "tasks.yaml EXPORT-001")],
    allowed=["app/", "frontend/src/", "tests/", "tasks.yaml"],
    forbidden=[".env", "生产数据"],
    deliverables=["导出 Alpaca 或 ShareGPT 其中一种", "导出前脱敏，并写入审计"],
    non_goals=["不导出未脱敏原文"],
    acceptance=["文件可被对应格式解析", "审计在文件开始返回之前写好"],
    risk="L2",
    side="export-training-format",
    rollback="回退导出提交。",
    cases=["导出文件可解析且有审计"],
    commands=["pytest -k export"],
)

add(
    id="AUTH-006",
    title="公开注册邮箱必填与唯一校验",
    status="backlog",
    priority="P0",
    depends_on=[],
    phase=5,
    problem="公开注册可以不填邮箱。空白会收成空。",
    facts=[
        (
            "2026-10-02 定为邮箱必填、格式与库内唯一，前期不做真实性校验。",
            "docs/product/项目产品需求方案.md §5.3.8",
        )
    ],
    allowed=[
        "app/models/",
        "app/schemas/",
        "app/services/",
        "app/api/",
        "alembic/",
        "frontend/src/",
        "tests/",
        "tasks.yaml",
    ],
    forbidden=[".env"],
    deliverables=["自行注册可以打开", "邮箱必填，做格式校验，并在数据库里唯一"],
    non_goals=["不做真实性校验，也不做真人核验", "不写腾讯云发送实现", "admin/system/culture 可以没有邮箱"],
    acceptance=[
        "缺邮箱、格式不对、邮箱已被占用时拒绝注册，且不新增用户",
        "合法新邮箱可以注册",
    ],
    risk="L2",
    side="register-email-required",
    rollback="回退注册校验提交。",
    cases=["缺邮箱与重复邮箱被拒"],
    commands=['pytest -k "register or auth"'],
)

add(
    id="ADM-005",
    title="系统状态页：健康、在线用户、存储、调用量",
    status="backlog",
    priority="P0",
    depends_on=[],
    phase=5,
    problem="状态接口没有在线用户数、存储用量、调用量。",
    facts=[("2026-10-02 定为四项同时展示。", "docs/plans/plan_v1_lock_20261002.md")],
    allowed=["app/api/", "app/services/", "frontend/src/", "tests/", "tasks.yaml"],
    forbidden=[".env"],
    deliverables=["同一页展示健康、在线用户数、存储用量和 API 调用量"],
    non_goals=["不把多副本汇总算进本条", "1.0 只统计本进程"],
    acceptance=["四项都能在页面上读到", "只放行 system_admin 的规则保持，除非矩阵另改"],
    risk="L1",
    side="admin-status-metrics",
    rollback="回退状态页提交。",
    cases=["system_admin 可见四项"],
    commands=['pytest -k "status or admin"'],
)

add(
    id="MIG-001",
    title="启动补列并入 Alembic 单轨",
    status="backlog",
    priority="P0",
    depends_on=["NFR-005"],
    phase=6,
    problem="启动路径上还有手写补列，和生产迁移不是同一条路径。",
    facts=[("migration.py 存在 Alembic 与手写 ALTER。", "app/core/migration.py")],
    allowed=["app/core/migration.py", "alembic/", "tests/", "tasks.yaml"],
    forbidden=[".env", "生产数据"],
    deliverables=["启动补列并入 Alembic"],
    non_goals=["不在本条改表的业务含义"],
    acceptance=["生产库不再有第二条改表路径", "空的 Alembic head 不能当成成功"],
    risk="L2",
    side="alembic-single-track",
    rollback="回退迁移路径提交。",
    cases=["空库迁移到当前 head"],
    commands=["pytest -k migrat"],
)

add(
    id="SAND-003",
    title="沙箱 Linux 实机限制与两家模型提供商",
    status="backlog",
    priority="P0",
    depends_on=[],
    phase=6,
    problem="沙箱限额没有 Linux 实机记录。模型提供商还不能按 1.0 的标准切换两家。",
    facts=[
        (
            "八项 P0 要求沙箱实机隔离与两家模型可切换。",
            "docs/product/企业级上线差距-2026-10-02.md",
        )
    ],
    allowed=["app/", "tests/", "docs/", "tasks.yaml", ".env.example"],
    forbidden=[".env", "生产密钥"],
    deliverables=["Linux 上的资源限制记录", "至少两个模型提供商可以切换"],
    non_goals=["不把 Mock 提供商算成第二家"],
    acceptance=["实机记录含限制生效的证据", "切换后对话走被选中的那一家"],
    risk="L2",
    side="sandbox-two-providers",
    rollback="回退沙箱与提供商提交。",
    cases=["Linux 限制生效", "切换提供商"],
    commands=['pytest -k "sandbox or provider"'],
)

add(
    id="SEC-006",
    title="日志侧身份证手机号银行卡脱敏",
    status="backlog",
    priority="P0",
    depends_on=["SEC-002"],
    phase=6,
    problem="输入过滤会替换三类号码。日志脱敏路径还没盖住这三类。",
    facts=[("日志脱敏有凭据字段，未覆盖身份证/手机号/银行卡。", "app/security/log_sanitizer.py")],
    allowed=["app/security/", "tests/", "tasks.yaml"],
    forbidden=[".env"],
    deliverables=["这三类在日志路径上替换"],
    non_goals=["不把输入侧已经会替换，写成日志侧已完成"],
    acceptance=["三类样例进入日志后都不再是原文"],
    risk="L1",
    side="log-pii-redaction",
    rollback="回退日志脱敏提交。",
    cases=["三类号码进入日志被替换"],
    commands=['pytest -k "sanitiz or log or pii"'],
)

add(
    id="REL-005",
    title="锁定清单通过后打附注标签 v1.0.0",
    status="backlog",
    priority="P0",
    depends_on=[],
    phase=7,
    problem=(
        '锁定清单还没有一次同时成立的证据。2026-10-02 的门禁记录不能当作这次的'
        '结果。'
    ),
    facts=[("打标规则见锁定计划阶段 7。", "docs/plans/plan_v1_lock_20261002.md")],
    allowed=["docs/plans/", "tasks.yaml", "CHANGELOG*", "README.md"],
    forbidden=[".env", "生产数据", "app/", "frontend/src/"],
    deliverables=["附注标签 v1.0.0", "说明只写这一版锁定了什么"],
    non_goals=["不把仍是 Partial 或未定的行写进标签说明", "不在打标时改验收"],
    acceptance=[
        "阶段 1 到阶段 6 的锁定标准都有命令输出或演练记录",
        (
            'pytest、ruff、mypy、前端类型检查、Vitest、ESLint、前端'
            '构建在同一环境的退出码都为 0'
        ),
    ],
    risk="L2",
    side="v1-tag",
    rollback="删除附注标签（需人工确认）。",
    cases=["核对锁定清单与门禁退出码"],
    commands=["pytest", "ruff check .", "mypy app/"],
)


def render(c: dict) -> str:
    lines: list[str] = []
    lines.append(f'  - id: "{c["id"]}"')
    lines.append(f'    title: "{c["title"]}"')
    lines.append(f'    status: "{c["status"]}"')
    lines.append(f'    priority: "{c["priority"]}"')
    lines.append('    target_date: ""')
    deps = c["depends_on"]
    if deps:
        lines.append("    depends_on:")
        for d in deps:
            lines.append(f'      - "{d}"')
    else:
        lines.append("    depends_on: []")
    lines.append("    context:")
    lines.append(f'      problem: "{c["problem"]}"')
    lines.append("      facts:")
    for claim, ev in c["facts"]:
        lines.append(f'        - claim: "{claim}"')
        lines.append(f'          evidence: "{ev}"')
    lines.append(f'        - claim: "本卡来自 1.0 锁定拆分说明第 4 节，阶段 {c["phase"]}。"')
    lines.append(
        '          evidence: "docs/plans/plan_v1_task_split_20261002.md §4；docs/plans/plan_v1_lock_20261002.md"'
    )
    lines.append("    scope:")
    lines.append("      allowed_paths:")
    for p in c["allowed"]:
        lines.append(f'        - "{p}"')
    lines.append("      forbidden_paths:")
    for p in c["forbidden"]:
        lines.append(f'        - "{p}"')
    lines.append("      deliverables:")
    for p in c["deliverables"]:
        lines.append(f'        - "{p}"')
    lines.append("      non_goals:")
    for p in c["non_goals"]:
        lines.append(f'        - "{p}"')
    lines.append("    acceptance:")
    for p in c["acceptance"]:
        lines.append(f'        - "{p}"')
    lines.append("    risk:")
    lines.append(f'      level: "{c["risk"]}"')
    lines.append(f'      side_effect: "{c["side"]}"')
    lines.append(f'      rollback: "{c["rollback"]}"')
    lines.append("    evaluation:")
    lines.append("      cases:")
    for p in c["cases"]:
        lines.append(f'        - "{p}"')
    lines.append("      commands:")
    for p in c["commands"]:
        # quote commands that contain spaces or special chars safely via YAML double quotes
        safe = p.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'        - "{safe}"')
    lines.append("    execution:")
    lines.append('      owner: "个人开发者"')
    lines.append("      nightly:")
    lines.append("        eligible: false")
    lines.append("        production_access: false")
    lines.append("        auto_merge: false")
    lines.append("        auto_deploy: false")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    existing = {t["id"] for t in yaml.safe_load(TASKS.read_text(encoding="utf-8"))["tasks"]}
    for c in cards:
        if c["id"] in existing:
            raise SystemExit(f"id already exists: {c['id']}")

    raw = TASKS.read_text(encoding="utf-8")
    if not raw.endswith("\n"):
        raw += "\n"
    TASKS.write_text(raw + "".join(render(c) for c in cards), encoding="utf-8")

    data = yaml.safe_load(TASKS.read_text(encoding="utf-8"))
    ids = [t["id"] for t in data["tasks"]]
    if len(ids) != len(set(ids)):
        raise SystemExit("duplicate ids after append")
    for c in cards:
        if c["id"] not in ids:
            raise SystemExit(f"missing after append: {c['id']}")

    map_path = ROOT / "docs/plans/plan_v1_section4_card_ids_20261003.md"
    lines = [
        "# 1.0 锁定第 4 节新卡编号",
        "",
        "> 日期：2026-10-03",
        "> 来源：`docs/plans/plan_v1_task_split_20261002.md` 第 4 节",
        "> 状态：已追加进 `tasks.yaml`，均为 `backlog`。未改业务代码。",
        "",
        "| 编号 | 标题 | 阶段 |",
        "|---|---|---|",
    ]
    for c in cards:
        lines.append(f"| `{c['id']}` | {c['title']} | {c['phase']} |")
    lines.append("")
    lines.append("Vitest 与 ESLint 已并入 `NFR-001`，不单开卡。")
    lines.append("")
    map_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"appended {len(cards)} cards; total {len(ids)}")
    print("ids:", ",".join(c["id"] for c in cards))


if __name__ == "__main__":
    main()
