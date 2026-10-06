"""One-time, scoped task-document transformation and contract validation."""

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "tasks.yaml"
original = PATH.read_text(encoding="utf-8")
text = original.replace(
    'claim: "2026-10-05 用户直接需求，已完成实现。\n', 'claim: "2026-10-05 用户直接需求，已完成实现。"\n'
).replace('claim: "后端新增分块接口，前端按块展示。\n', 'claim: "后端新增分块接口，前端按块展示。"\n')
parts = re.split(r"(?m)(?=^  - id:)", text)
text = text.replace('- "pytest -k "agent or tool or chat""', "- 'pytest -k \"agent or tool or chat\"'")
parts = re.split(r"(?m)(?=^  - id:)", text)
data = yaml.safe_load(text)
tasks = {t["id"]: t for t in data["tasks"]}
before = {t["id"]: t["status"] for t in data["tasks"]}
PLAN = "docs/plans/plan_rag_review_remediation_20261005.md"
SPLIT = "docs/plans/plan_rag_remediation_cards_20261005.md"
changed = set()


def get(id):
    changed.add(id)
    t = tasks[id]
    t["context"]["facts"].append(
        {"claim": "本次仅调整任务契约，未实施新增范围；验收与拆分见正式映射。", "evidence": SPLIT}
    )
    for p in ["docs/", "tasks.yaml"]:
        if p not in t["scope"]["allowed_paths"]:
            t["scope"]["allowed_paths"].append(p)
    return t


t = get("RAG-014")
t["context"]["problem"] = "已有文本 hash 去重与部分归一化；缺完整质量清洗及删除失败持久补偿，不能把已有去重写成不存在。"
t["scope"]["allowed_paths"] += ["app/models/rag.py", "alembic/"]
t["acceptance"] += ["代码、盒图缩进与原文结构不得被清洗破坏；补偿重启后可恢复且幂等；软删和保留期物理清理均覆盖。"]
t = get("RAG-015")
t["depends_on"] += ["RAG-023", "RAG-032"]
t["acceptance"] += [
    "真实 Milvus 相似度按批准 metric 转换，禁止 similarity=1.0 占位；记录跨库检索差异，质量对照由 RAG-036 承接。"
]
t = get("RAG-016")
t["scope"]["non_goals"] += ["本卡仅锁定知识库搜索父块展开；对话父块选入与预算由 RAG-030 承接。"]
t = get("RAG-017")
t["depends_on"] += ["NFR-001", "RAG-034"]
t["scope"]["allowed_paths"] += ["scripts/", "README.md"]
t["acceptance"] += [
    (
        "实施计划先批准 Recall@1/5/10、MRR、nDCG 与安全切片阈值及容"
        "忍量；外部模型失败不得记质量通过；门禁走实际对话过滤契约。"
    )
]
t["evaluation"]["commands"] = [
    "python scripts/run_rag_baseline.py --mode official",
    "pytest tests/eval/ -v",
    "CI 运行同一评测入口并注入退化验证拦截",
]
t["execution"]["blocked_by"] = "NFR-001/RAG-034 完成且发布指标阈值获批准后开工。"
t = get("RAG-018")
t["evaluation"]["commands"] = ["python scripts/run_rag_baseline.py --mode official"]
t["scope"]["allowed_paths"] += ["scripts/"]
t["execution"]["blocked_by"] = "Deferred：真实、可复现 Recall@5 跌破 0.90 并确认进入条件前不可开工。"
t = get("RAG-019")
t["depends_on"] += ["RAG-026", "RAG-032"]
t["scope"]["non_goals"] = [s for s in t["scope"]["non_goals"] if s != "缓存键不含权限/租户隔离信息"] + [
    "不以 temperature=0 单独证明答案可安全复用。"
]
t["scope"]["deliverables"] += [
    "缓存键包含租户、有效授权域、数据/索引版本；LLM 键包含实际上下文与模型/Prompt 版本；命中再授权。"
]
t["acceptance"] += ["发布、删除、重解析和改权使缓存失效；同查询跨租户/不同权限不复用。"]
t = get("RAG-021")
t["context"]["problem"] = (
    "已有标题/解析块/版式感知，仍缺代码围栏、盒图和表格的统一完整性保护，parent_child 长段硬切可切坏结构。"
)
t["context"]["facts"][1]["claim"] = "已有策略具备局部结构识别，但缺完整结构原子性与超长结构处理的统一契约。"
t["scope"]["allowed_paths"] = [
    p.replace("app/rag/parsers/", "app/rag/document_parsers/") for p in t["scope"]["allowed_paths"]
] + ["app/rag/ingestion.py", "evals/"]
t["scope"]["deliverables"] = [
    "共享结构单元保护与来源范围，保留代码/盒图换行缩进、表格行与表头关联。",
    "chunk_size 为软目标，硬限来自批准的输入限制；超长结构安全拆分或 oversized，不静默字符切断。",
    "原文覆盖、乱序、结构破坏率与长度分布基线；规则层不依赖 LLM。",
]
t["acceptance"] = [
    "未超硬限的截图同类盒图与 fenced code 整体完整；超长结构有明确结果。",
    "原文无丢失/乱序；来源范围可复算；重复表头/overlap 区分派生上下文。",
    "共享保护对各策略的适用/降级有测试，不声称所有结构均能无损强拆。",
]
t["execution"]["blocked_by"] = "结构软目标/硬限与 oversized 规则获确认；涉及 ADR-0005 边界的改动须 Accepted 后实施。"
t = get("RAG-022")
t["depends_on"] = ["RAG-021"]
t["scope"]["allowed_paths"] = [
    "app/rag/",
    "app/models/rag.py",
    "alembic/",
    "app/core/config.py",
    ".env.example",
    "README.md",
    "tests/",
    "evals/",
    "docs/",
    "tasks.yaml",
]
t["scope"]["deliverables"] = [
    "按文档类型、blocks/bbox/表格与内容特征的可追溯路由决策表，不强行覆盖十种。",
    "上传/重解析复用版本化切分计划并保存策略参数；父块先建，子块在父块内切，按源范围关联。",
    "缺结构信息显式保守降级，切分质量可复算。",
]
t["acceptance"] = [
    "同原文同计划上传/重解析保留相同结构与策略；旧数据缺计划有显式兼容路径。",
    "带 bbox 与 Markdown 样本路由可解释，无 bbox/blocks 明确降级。",
    "父子范围包含关系正确；对照 RAG-021 指标；不无指标切换默认。",
]
t["execution"]["blocked_by"] = "RAG-021 验收通过；策略持久化/边界变更所需 ADR 获批准；不自动重建旧文档。"
t = get("PRAG-002")
t["execution"]["blocked_by"] = "现无已批准采纳候选；新的采纳证据与负责人决定落盘前不可开工，REL-004 done 不构成准入。"
t["execution"]["nightly"]["eligible"] = False
t["scope"]["non_goals"] = [
    s.replace("采纳结论以 REL-004 为准", "既有候选以 REL-004 不采纳为准；新候选需另获明确采纳决定")
    for s in t["scope"]["non_goals"]
]

# id, title, observations, dependencies, problem/current fact, deliverables,
# non-goals, acceptance, paths, risk, blocking condition
rows = [
    (
        "023",
        "检索适配分数契约修复",
        ["C1"],
        [],
        "LangChain 适配返回 RRF score 后误赋 similarity。",
        ["透传真稠密 similarity，保留 score 融合语义。"],
        ["不改 RRF/Embedding/阈值。"],
        ["融合分低但余弦高的命中不被误滤；缺失/非法 similarity 不伪造。"],
        ["app/rag/backend/", "app/rag/vectorstore/"],
        "L1",
        "",
    ),
    (
        "024",
        "导入新版本发布原子性",
        ["D1"],
        ["RAG-013"],
        "新版本摄取前旧版退出 current 的独立提交可能在失败后无法恢复。",
        ["准备完成后原子切换；失败保留旧版，重试幂等。"],
        ["不改版本产品语义、不自动迁移旧库。"],
        ["Embedding/落库失败后旧版仍可检索；成功只一个 current；外部索引补偿有记录。"],
        ["app/rag/", "app/models/rag.py", "alembic/"],
        "L2",
        "实际故障复现与事务基线隔离后开工；生产重建另获授权。",
    ),
    (
        "025",
        "RAG 日志内容最小化",
        ["C6"],
        [],
        "问题/计划与 sources 摘录进入日志调用，最终是否脱敏需复核。",
        ["移除正文日志，保留关联 ID、计数、阈值与原因。"],
        ["不重写日志平台。"],
        ["最终输出无凭据、手机号、敏感问题/文档摘录；故障日志仍可关联。"],
        ["app/rag/", "app/services/chat_service.py", "app/core/", "app/security/"],
        "L1",
        "",
    ),
    (
        "026",
        "上传者范围贯穿检索授权",
        ["D4"],
        [],
        "uploader 判定未贯穿只携 tenant_id 的对话检索。",
        ["鉴权主体/有效读范围在检索前过滤，覆盖适配/父块/摘要。"],
        ["不新增资源 ACL、不改变默认 tenant。"],
        ["uploader A/B 同租户互不可见；tenant 共享保留；跨租户拒绝。"],
        ["app/rag/", "app/services/chat_service.py"],
        "L2",
        "先核对 ADR-0001 uploader 兼容契约；若改变权限语义先修订批准。",
    ),
    (
        "027",
        "授权引用原文只读核验",
        ["D5"],
        ["RAG-026"],
        "同租户可检索他人文档，但引用详情走控制面权限。",
        ["先落只读溯源权限 ADR，批准后提供受控原文核验入口及前端定位。"],
        ["不放宽删除/重解析控制权。"],
        ["A 上传 B 检索核验成功；B 不能删改 A；跨租户和软删拒绝。"],
        ["app/rag/", "app/api/routes/", "frontend/src/"],
        "L2",
        "只读溯源权限 ADR Accepted 后才改业务。",
    ),
    (
        "028",
        "模型能力契约与全部调用预算",
        ["E2"],
        [],
        "无模型窗口契约，当前 max_tokens 是输出上限。",
        ["版本化生成/Embedding 能力、计数器；每次实际 payload Guard，包含工具与 critique。"],
        ["不向 LLM 自报窗口、不猜未知上限、不换供应商。"],
        ["模型切换按对应窗口；未知模型无批准配置拒绝；最终 payload+输出预留+余量不超已验证限制。"],
        [
            "app/llm/",
            "app/rag/",
            "app/agents/",
            "app/services/chat_service.py",
            "app/core/config.py",
            ".env.example",
            "README.md",
        ],
        "L2",
        "ADR-0005 Accepted；能力来源/计数精度/估算余量获批准。",
    ),
    (
        "029",
        "按块上下文组装与真实来源",
        ["D3", "E2"],
        ["RAG-028"],
        "字符截断可能破坏围栏，last_hits 不等于实际模型证据。",
        ["纯 Context Builder 按块预算；Memory/RAG 优先级；selected 来源与实际 payload 对齐。"],
        ["不把 selected 宣称逐答案点 cited、不新增数据库直连。"],
        ["围栏闭合；长历史/工具合并不超预算；未选块不出现在 selected 来源。"],
        ["app/rag/", "app/agents/", "app/services/chat_service.py", "app/api/routes/", "frontend/src/"],
        "L2",
        "ADR-0005 Accepted；预算与来源契约获批准。",
    ),
    (
        "030",
        "对话父块受控组装",
        ["A4"],
        ["RAG-016", "RAG-029", "RAG-026"],
        "搜索父块展开不能证明对话选入父块。",
        ["授权/版本/注入复核后按预算选入父块并去重，保留定位元信息。"],
        ["不只返回父块、不绕过预算与阈值。"],
        ["同步/流式实际上下文有父块；重复去重；他租户/历史/注入父块拒绝。"],
        ["app/rag/", "app/services/chat_service.py", "app/agents/"],
        "L2",
        "ADR-0005 Accepted。",
    ),
    (
        "031",
        "LLM 辅助切分边界",
        ["A5", "A6"],
        ["RAG-022", "RAG-028"],
        "规则结构保护之外的主题边界需要单独评测辅助能力。",
        ["模型仅输出边界 ID；原文由代码切取；校验、有限调用、失败规则降级。"],
        ["不改写原文、不替代结构保护、不默认启用。"],
        ["无丢失/乱序/越界；注入/坏 JSON/超时回退；真实单变量质量与成本报告。"],
        ["app/rag/", "app/llm/", "app/core/config.py", ".env.example", "README.md", "evals/"],
        "L2",
        "ADR-0005 Accepted，数据处理授权与质量/成本门禁获批准。",
    ),
    (
        "032",
        "Embedding 索引身份与切换治理",
        ["B2"],
        [],
        "维度校验和 health 展示不足以阻止同维度异模型混用。",
        ["绑定 provider/model/dim/index_version；新模型新索引构建、校验再切换。"],
        ["不原地改模型、不默认切 Milvus。"],
        ["同维度异模型阻断混用；新 collection 全量重建与回滚证据；旧版持续可读。"],
        ["app/rag/", "app/models/rag.py", "alembic/", "app/core/config.py", ".env.example", "README.md", "scripts/"],
        "L2",
        "索引迁移方案/相关 ADR 批准；实际重建另获授权。",
    ),
    (
        "033",
        "多格式解析与 OCR 质量基线",
        ["B3"],
        [],
        "解析代码存在不等于真实格式/扫描件质量已验收。",
        ["各格式、中文 OCR、双栏/表格样本、顺序/覆盖指标与依赖失败行为。"],
        ["不新增解析供应商、不以 Mock 证明 OCR 质量。"],
        ["真实依赖样本报告含覆盖与阅读顺序、失败清单；质量门禁评审锁定。"],
        ["app/rag/document_parsers/", "app/rag/ocr/", "evals/", "scripts/"],
        "L1",
        "真实依赖与样本数据授权到位。",
    ),
    (
        "034",
        "RAG 评测分级与审核链核对",
        ["E1"],
        ["RAG-004"],
        "需复核现有人工 Gold、split 与来源链，不预设数据均不可信。",
        ["审核链、Gold/Silver/Adversarial/Regression/Smoke、泄漏检查与版本登记。"],
        ["不改冻结报告、不把 AI 生成自动升级 Gold。"],
        ["每条 Gold 有人工记录与原文证据；缺证据降级或补审；split 无泄漏。"],
        ["evals/", "tests/eval/", "scripts/"],
        "L0",
        "",
    ),
    (
        "035",
        "真实 RAG 生成与引用评测",
        ["B1"],
        ["RAG-034", "RAG-023"],
        "检索命中和 quote coverage 不能证明最终答案/拒答/引用正确。",
        ["真实模型答案点、引用准确、无答案、冲突/过期、注入与工具行为评测。"],
        ["不以模型自评替代人工、不用 holdout 调参。"],
        ["版本化报告与失败切片；人工确认 Gold；生成发布阈值批准并锁定；Mock 仅链路。"],
        ["evals/", "tests/eval/", "scripts/", "app/rag/"],
        "L1",
        "真实模型配置与数据授权；生成质量阈值获批准后才用于上线判断。",
    ),
    (
        "036",
        "跨向量库混合语义验收",
        ["C3"],
        ["RAG-015"],
        "Milvus 在稠密候选内算 BM25，与 Local 全候选不等价；全零仍造稀疏排名。",
        ["记录语义/适用边界，修复零分稀疏排名，运行词面与过滤对照。"],
        ["不直接改成独立稀疏召回、不改 RRF。"],
        ["全零不造排名；纯关键词遗漏报告；真实 Milvus 切片可复现；新增召回架构另 ADR。"],
        ["app/rag/vectorstore/", "evals/", "scripts/"],
        "L1",
        "真实 Milvus/Embedding 可用。",
    ),
    (
        "037",
        "检索状态与最终拒答契约",
        ["A3", "C4", "C2"],
        ["RAG-023"],
        "故障、无命中、全低分现有路径未形成最终回复统一契约。",
        ["ok/no_hit/below_threshold/unavailable 状态；入口/后端矩阵；明确少返回策略。"],
        ["不自动补位改变排名、不做无界兜底生成。"],
        ["同步/流式故障明确提示不冒充知识回答；低分最终回复验收；补位仅评测不自动采纳。"],
        ["app/rag/", "app/services/chat_service.py", "app/agents/", "app/api/routes/", "frontend/src/"],
        "L2",
        "错误/拒答契约获批准；涉及 Harness 边界按 ADR-0005 批准。",
    ),
    (
        "038",
        "检索调用 deadline、重试与取消",
        ["E3"],
        ["RAG-037"],
        "已有 HTTP timeout；总 deadline、慢查询、有限重试与取消需专项核对。",
        ["统一总时限、有限退避、取消传播与成本/错误追踪。"],
        ["不自动降级 BM25、不引入新队列依赖。"],
        ["429/5xx/慢查询/取消故障注入；重试次数与耗时有界；外部服务失败不伪造空知识。"],
        ["app/rag/", "app/core/config.py", ".env.example", "README.md"],
        "L2",
        "deadline/重试上限在实现计划锁定；若新增降级架构另批准。",
    ),
    (
        "039",
        "导入任务恢复与多实例幂等",
        ["C7"],
        ["OPS-001", "OPS-002", "RAG-024"],
        "MAX_CONCURRENCY 当前作为批次 limit，逐个 await；需原子抢占与中断恢复。",
        ["区分批次/并发，按批准方案租约抢占、重启恢复与幂等。"],
        ["不直接引入新队列、不重拆企业 OPS。"],
        ["多 worker 不重复发布；running 重启可恢复；并发与批次有实测。"],
        ["app/rag/", "app/models/rag.py", "alembic/", "app/core/config.py", ".env.example", "README.md"],
        "L2",
        "OPS 外部状态/幂等契约批准；新依赖另批准。",
    ),
    (
        "040",
        "版本绑定章节摘要",
        ["B1"],
        ["RAG-022", "RAG-028", "RAG-029", "RAG-035"],
        "知识库无大章节派生摘要，Memory 摘要不承接此需求。",
        ["独立状态摘要任务、分批合并、支撑原文 ID、版本 hash/Prompt/模型绑定、失效与成本限制。"],
        ["不替代原文、不另建摘要向量检索、不默认启用。"],
        ["失败不影响原文检索；过期/软删/跨租户摘要拒绝；原文回查与真实保真评价；所有调用受预算。"],
        [
            "app/rag/",
            "app/models/rag.py",
            "alembic/",
            "app/llm/",
            "app/core/config.py",
            ".env.example",
            "README.md",
            "evals/",
        ],
        "L2",
        "ADR-0005 Accepted；摘要存储/授权/保留与质量门禁批准。",
    ),
    (
        "041",
        "资源级 ACL 进入条件与权限决策",
        ["B4"],
        [],
        "资源 ACL 仍 Planned，现同租户当前版本共享不应算 ACL 完成。",
        ["目标版本纳入时先批准资源权限 ADR与独立拆分方案。"],
        ["进入条件未满足不实施 ACL、不改变 tenant 默认。"],
        ["决定明确主体/资源/动作、过滤与引用/摘要权限及迁移回滚；实施另卡。"],
        [],
        "L0",
        "Deferred：资源 ACL 明确纳入目标版本且负责人授权决策时启动。",
    ),
]

for num, title, obs, deps, problem, deliver, ngoals, accept, paths, risk, blocked in rows:
    id = "RAG-" + num
    if id in tasks:
        raise ValueError("ID already exists: " + id)
    t = {
        "id": id,
        "title": title,
        "status": "backlog",
        "priority": "P0" if num in ["023", "024", "025", "026", "037"] else "P1",
        "target_date": "",
        "depends_on": deps,
        "context": {
            "problem": problem,
            "facts": [
                {
                    "claim": "评审静态观察或能力缺口，实施前须复核并取得复现；未在本轮修复。",
                    "evidence": PLAN + "；" + SPLIT + " " + id,
                }
            ],
        },
        "scope": {
            "allowed_paths": list(dict.fromkeys(paths + ["tests/", "docs/", "tasks.yaml"])),
            "forbidden_paths": [".env", "生产数据", "evals/reports/rag-v0.1-baseline-20260919.json"],
            "deliverables": deliver,
            "non_goals": ngoals,
        },
        "acceptance": accept,
        "risk": {
            "level": risk,
            "side_effect": "documentation-only" if risk == "L0" else title,
            "rollback": "规则/配置/模型候选独立关闭或回退；旧版本/原文保留，重要数据重建另获人工授权。",
        },
        "evaluation": {
            "cases": accept,
            "commands": ["git diff --check"]
            if risk == "L0"
            else ['pytest tests/ -k "rag or chunk or context or embedding" -v', "ruff check app/rag/", "mypy app/rag/"],
        },
        "execution": {
            "owner": "个人开发者",
            "blocked_by": blocked,
            "nightly": {"eligible": False, "production_access": False, "auto_merge": False, "auto_deploy": False},
        },
    }
    if any(p.startswith("frontend") for p in paths):
        t["evaluation"]["commands"] += ["cd frontend; npm run typecheck", "cd frontend; npm run build"]
    tasks[id] = t


def render(t):
    class Dumper(yaml.SafeDumper):
        def ignore_aliases(self, value):
            return True

    return (
        "\n".join(
            "  " + line if line else ""
            for line in yaml.dump([t], Dumper=Dumper, allow_unicode=True, sort_keys=False, width=110).splitlines()
        )
        + "\n\n"
    )


out = parts[0] + "".join(
    render(tasks[yaml.safe_load(p)[0]["id"]]) if yaml.safe_load(p)[0]["id"] in changed else p for p in parts[1:]
)
out += "\n" + "".join(render(tasks["RAG-" + r[0]]) for r in rows)
parsed = yaml.safe_load(out)
ids = [t["id"] for t in parsed["tasks"]]
assert len(ids) == len(set(ids))
graph = {t["id"]: t.get("depends_on", []) for t in parsed["tasks"]}
seen = set()
active = set()


def visit(id):
    assert id in graph, "missing dependency " + id
    assert id not in active, "dependency cycle " + id
    if id in seen:
        return
    active.add(id)
    for dep in graph[id]:
        visit(dep)
    active.remove(id)
    seen.add(id)


for id in graph:
    visit(id)
assert all(tasks[id]["status"] == status for id, status in before.items())

sections = [
    (
        "# RAG 整改正式任务拆分与实施计划\n\n日期：2026-10-05。任务契约调整已落盘；业务未实施，ADR-0005 "
        "仍 Proposed。入口：[tasks.yaml](../../tasks.yaml)。总方案：[整改映射](plan"
        "_rag_review_remediation_20261005.md)。\n\n本节逐卡记录目标、现状、方案、非目标和验收"
        "，可作为开工前实现计划基础；blocked_by、权限/架构批准、失败复现及发布阈值未满足时不得开工。实际验证命令在开工"
        "时按修改文件细化；本轮没有执行业务测试。\n\n## 既有卡调整\n\n"
    )
    + ", ".join(sorted(changed))
    + (
        "。另修复 RAG-020 引号，状态不变。RAG-014 清洗/补偿、015 切换、016 搜索范围、017 CI、01"
        "8 Deferred、019 缓存隔离、021 结构保护、022 路由/重解析、PRAG-002 准入分别承接原边界。\n"
        "\n## 正式映射\n\n| 评审项 | 正式任务 |\n|---|---|\n"
    )
]
mapping = {
    "A1": "RAG-019",
    "A2": "本轮 YAML 修复/契约检查",
    "A3": "RAG-015 / RAG-037",
    "A4": "RAG-016 / RAG-030",
    "A5": "RAG-021",
    "A6": "RAG-022",
    "A7": "PRAG-002",
    "A8": "后续能力文档对账，不自动改 done",
    "B1": "RAG-035 / RAG-040",
    "B2": "RAG-032",
    "B3": "RAG-033",
    "B4": "RAG-041",
    "C1": "RAG-023",
    "C2": "RAG-037（保留少返回，补位不自动实施）",
    "C3": "RAG-036",
    "C4": "RAG-037",
    "C5": "RAG-017 / RAG-018",
    "C6": "RAG-025",
    "C7": "RAG-039 / OPS-001 / OPS-002",
    "D1": "RAG-024",
    "D2": "RAG-022",
    "D3": "RAG-029",
    "D4": "RAG-026",
    "D5": "RAG-027",
    "E1": "RAG-034",
    "E2": "RAG-028 / RAG-029",
    "E3": "RAG-038",
}
for k, v in mapping.items():
    sections.append("| " + k + " | " + v + " |\n")
for id in sorted(changed) + ["RAG-" + r[0] for r in rows]:
    t = tasks[id]
    sections.append(
        "\n## "
        + id
        + " "
        + t["title"]
        + "\n\n目标/交付："
        + "；".join(t["scope"]["deliverables"])
        + "\n\n现状："
        + t["context"]["problem"]
        + "\n\n方案：在 allowed_paths 内按交付最小实施，先失败测试/案例后修改；依赖："
        + (", ".join(t["depends_on"]) or "无任务依赖")
        + "。准入："
        + (t["execution"].get("blocked_by") or "复核事实与测试基线，范围可隔离后开工。")
        + "\n\n非目标："
        + "；".join(t["scope"]["non_goals"])
        + "\n\n验收："
        + "；".join(t["acceptance"])
        + "\n\n回滚："
        + t["risk"]["rollback"]
        + "\n"
    )
sections.append(
    (
        "\n## 验证记录\n\nYAML 完整解析、ID 唯一、依赖目标存在/无环、已有卡状态不变均由本次脚本断言。仅对目标卡重序列"
        "化；其他卡文本保持。业务 pytest/mypy 未运行。新增 "
    )
    + str(len(rows))
    + " 卡，全为 backlog，夜间准入全部关闭。\n"
)
# All assertions precede writes; validate even untouched task IDs before saving.
PATH.write_text(out, encoding="utf-8")
(ROOT / SPLIT).write_text("".join(sections), encoding="utf-8")
print(
    json.dumps(
        {
            "added": len(rows),
            "adjusted": sorted(changed),
            "total": len(ids),
            "yaml": "ok",
            "unique_ids": "ok",
            "dependencies": "ok",
            "existing_statuses": "unchanged",
        },
        ensure_ascii=False,
    )
)
