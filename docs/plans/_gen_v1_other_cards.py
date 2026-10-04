# -*- coding: utf-8 -*-
"""Append other-chapter gap cards into tasks.yaml. One-shot."""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
TASKS = ROOT / "tasks.yaml"

cards: list[dict] = []


def add(**kwargs) -> None:
    cards.append(kwargs)


add(
    id="CLI-003",
    title="CLI：start / stop / status / logs",
    status="backlog",
    priority="P1",
    depends_on=["CLI-001"],
    phase=2,
    problem="CLI-001 只做 migrate 与创建管理员，明确不做 start、stop、logs 全集。",
    facts=[
        ("需求 P1-7 要求 init/start/stop/status/logs/migrate/admin。", "docs/product/项目产品需求方案.md §4.3 P1-7"),
        ("CLI-001 非目标写明不实现 start、stop、logs 全集。", "tasks.yaml CLI-001"),
    ],
    allowed=["app/", "scripts/", "README.md", "pyproject.toml", "docs/", "tasks.yaml"],
    forbidden=[".env", "生产数据"],
    deliverables=["start、stop、status、logs 可调用", "与 README 命令一致"],
    non_goals=["不重做 migrate 与创建管理员", "不实现 Kubernetes"],
    acceptance=[
        "本机用 start/stop/status/logs 能启停并读到状态与日志",
        "失败时有可读原因",
    ],
    risk="L1",
    side="cli-lifecycle",
    rollback="回退 CLI 入口提交。",
    cases=["启停各一次并读 status/logs"],
    commands=["python -m ai_assistant --help"],
)

add(
    id="FLOW-004",
    title="工作流开关与 croniter 缺失提示",
    status="backlog",
    priority="P1",
    depends_on=["FLOW-002", "FLOW-003"],
    phase=4,
    problem="WORKFLOW_ENABLED 默认关闭；未安装 croniter 时调度不跑，页面未说明只剩手动执行。",
    facts=[
        ("差距表要求打开后才有定时；缺可选依赖时页面要说明。", "docs/product/企业级上线差距-2026-10-02.md §2"),
        ("FLOW-002 只交付空态与失败原因，不覆盖开关语义。", "tasks.yaml FLOW-002"),
    ],
    allowed=[
        "app/workflow/",
        "app/api/",
        "frontend/src/",
        "docs/",
        "tasks.yaml",
        "tests/",
    ],
    forbidden=[".env", "生产数据"],
    deliverables=[
        "打开开关且依赖齐全时定时路径可用",
        "未安装 croniter 时页面写明只剩手动执行",
    ],
    non_goals=["不做重试（属 PWFL-001）", "不替代 FLOW-003 到点触发"],
    acceptance=[
        "关闭时不自动跑定时",
        "打开且依赖齐全时到点可触发",
        "缺 croniter 时页面有只剩手动的说明",
    ],
    risk="L1",
    side="workflow-toggle-ux",
    rollback="回退开关与文案提交。",
    cases=["关开各一次；模拟缺 croniter"],
    commands=["pytest -k workflow -v"],
)

add(
    id="EVO-002",
    title="Reflect 改技能与待办提取",
    status="backlog",
    priority="P1",
    depends_on=["EVO-001"],
    phase=4,
    problem="EVO-001 只锁反思与蒸馏记录，明确不把反思结果改成技能；差距表仍缺改技能与待办提取。",
    facts=[
        ("差距表 v0.5/§2：Reflect 改技能、待办提取没有。", "docs/product/企业级上线差距-2026-10-02.md"),
        ("EVO-001 非目标：不在本条把反思结果直接改成技能。", "tasks.yaml EVO-001"),
    ],
    allowed=["app/evolution/", "app/services/", "app/api/", "docs/", "tasks.yaml", "tests/"],
    forbidden=[".env", "生产数据"],
    deliverables=["开关打开时反思可生成技能候选或待办", "留下可查记录"],
    non_goals=["不在默认关闭时算完成", "不自动无审上线技能"],
    acceptance=[
        "一次对话后有技能候选或待办记录",
        "需人工确认后才生效的路径写清",
    ],
    risk="L2",
    side="reflect-to-skill",
    rollback="关闭开关并回退提交。",
    cases=["打开开关跑一次对话，检查候选记录"],
    commands=["pytest -k evolution -v"],
)

add(
    id="ADM-006",
    title="租户设置页（ADM-04）",
    status="backlog",
    priority="P0",
    depends_on=[],
    phase=5,
    problem="前端没有 /app/settings/tenant；ADM-004 非目标写明不做租户设置页。",
    facts=[
        ("需求 ADM-04：tenant_admin 通过租户设置页管理本租户成员和知识库。", "docs/product/项目产品需求方案.md §5.4"),
        ("差距表：前端没有该路径。", "docs/product/企业级上线差距-2026-10-02.md §2"),
    ],
    allowed=["frontend/src/", "app/api/", "docs/", "tasks.yaml", "tests/"],
    forbidden=[".env", "生产数据"],
    deliverables=["租户设置页可管理本租户成员与知识库", "导航按角色显示"],
    non_goals=["不重做系统级四页", "不做多副本汇总"],
    acceptance=[
        "tenant_admin 可进入并完成成员或知识库管理",
        "其他角色按矩阵被拒绝",
    ],
    risk="L1",
    side="tenant-settings-page",
    rollback="回退前端路由与页面提交。",
    cases=["tenant_admin 与非授权角色各走一次"],
    commands=["cd frontend && npm run typecheck", "cd frontend && npm run build"],
)

add(
    id="ADM-007",
    title="Feature Flag 管理 API（ADM-05）",
    status="backlog",
    priority="P1",
    depends_on=[],
    phase=5,
    problem="需求 ADM-05 要求可配置 Feature Flag；v1.0 约定只提供 API，尚无对应任务卡。",
    facts=[
        ("需求：GET/PATCH /api/admin/features；v1.0 仅 API。", "docs/product/项目产品需求方案.md §5.4"),
    ],
    allowed=["app/models/", "app/api/", "app/services/", "docs/", "tasks.yaml", "tests/", "alembic/"],
    forbidden=[".env", "生产数据"],
    deliverables=["可查看与切换 Feature Flag", "持久化可覆盖代码默认值"],
    non_goals=["不做监控大盘可视化", "不做 EE License 解锁全集"],
    acceptance=[
        "system_admin 可切换 Flag",
        "未授权请求被拒绝",
        "重启后状态保持",
    ],
    risk="L2",
    side="feature-flag-api",
    rollback="回退迁移与路由；Flag 回默认。",
    cases=["授权切换、未授权拒绝、重启保持"],
    commands=["pytest -k feature -v"],
)

add(
    id="SEC-007",
    title="注入检测可阻断",
    status="backlog",
    priority="P1",
    depends_on=[],
    phase=5,
    problem="SECURITY_BLOCK_ON_INJECTION 默认 false，检测到只告警；打开阻断时缺少锁定验收。",
    facts=[
        ("需求 §5.8：检测恶意注入 + 可选阻断。", "docs/product/项目产品需求方案.md §5.8"),
        ("差距表：配置为阻断时要能拦住。", "docs/product/企业级上线差距-2026-10-02.md §2"),
    ],
    allowed=["app/security/", "app/services/", "app/core/config.py", ".env.example", "docs/", "tasks.yaml", "tests/"],
    forbidden=[".env", "生产数据"],
    deliverables=["阻断开关打开时注入请求不进入后续管线", "返回稳定错误"],
    non_goals=["不改默认仍可告警不阻断", "不在本条重做日志脱敏"],
    acceptance=[
        "打开阻断后，注入样例返回稳定错误且无助手成功落库回复",
        "关闭时行为与现网一致",
    ],
    risk="L2",
    side="injection-block",
    rollback="将开关改回 false 并回退提交。",
    cases=["开关开/关各测注入样例"],
    commands=["pytest -k injection -v"],
)

add(
    id="NFR-013",
    title="硬编码 URL 扫描",
    status="backlog",
    priority="P0",
    depends_on=["NFR-006"],
    phase=6,
    problem="§4.5 第 10 条密钥形状半边已核对；硬编码 URL 半边未扫，不能把整条写成通过。",
    facts=[
        ("差距表：硬编码 URL 这一半还没扫。", "docs/product/企业级上线差距-2026-10-02.md §2"),
        ("NFR-006 交付是密钥与依赖漏洞，不含 URL。", "tasks.yaml NFR-006"),
    ],
    allowed=[".pre-commit-config.yaml", ".github/", "scripts/", "docs/", "tasks.yaml", "frontend/", "app/"],
    forbidden=[".env", "生产密钥"],
    deliverables=["硬编码 URL 扫描纳入 CI 或 pre-commit", "可配置白名单"],
    non_goals=["不替代 NFR-006 的密钥与依赖漏洞扫描", "不误杀文档示例域名而不给白名单"],
    acceptance=[
        "故意放入的硬编码内网或生产 URL 被拦截或报告",
        "白名单外命中导致门禁失败",
    ],
    risk="L1",
    side="url-scan-gate",
    rollback="关闭扫描门禁并回退配置。",
    cases=["放置违例 URL 应失败；白名单内应通过"],
    commands=["pre-commit run --all-files"],
)

add(
    id="DOC-001",
    title="CHANGELOG 与 Breaking 区",
    status="backlog",
    priority="P1",
    depends_on=[],
    phase=6,
    problem="仓库没有 CHANGELOG；§6.7 要求不兼容变更写入 Breaking 区。",
    facts=[
        ("差距表：仓库没有 CHANGELOG。", "docs/product/企业级上线差距-2026-10-02.md §2"),
        ("OPS-003 允许改 CHANGELOG，但不替代本卡建立约定。", "tasks.yaml OPS-003"),
    ],
    allowed=["CHANGELOG.md", "README.md", "docs/", "tasks.yaml"],
    forbidden=[".env", "app/", "frontend/src/"],
    deliverables=["建立 CHANGELOG.md", "含 Breaking 区约定"],
    non_goals=["不代替 OPS-003 升级回滚演练本身"],
    acceptance=[
        "CHANGELOG.md 存在且含 Breaking 区标题",
        "OPS-003 演练记录可链到该区",
    ],
    risk="L0",
    side="changelog-bootstrap",
    rollback="删除或回退 CHANGELOG 提交。",
    cases=["文件存在且含 Breaking 标题"],
    commands=["rg -n Breaking CHANGELOG.md"],
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
    lines.append(
        f'        - claim: "本卡来自差距表第 2 表与需求正文核对，阶段 {c["phase"]}。"'
    )
    lines.append(
        '          evidence: "docs/plans/plan_v1_other_chapters_cards_20261003.md；docs/product/企业级上线差距-2026-10-02.md"'
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

    map_path = ROOT / "docs/plans/plan_v1_other_chapters_card_ids_20261003.md"
    lines = [
        "# 其他章节缺口新卡编号",
        "",
        "> 日期：2026-10-03",
        "> 来源：差距表第 2 表 + 需求第四～六章；拆分见 `docs/plans/plan_v1_other_chapters_cards_20261003.md`",
        "> 状态：已追加进 `tasks.yaml`，均为 `backlog`。未改业务代码。",
        "",
        "| 编号 | 标题 | 阶段 |",
        "|---|---|---|",
    ]
    for c in cards:
        lines.append(f"| `{c['id']}` | {c['title']} | {c['phase']} |")
    lines += [
        "",
        "未入卡：第 3 表未写清项、第 4 节未核项、`未定` 项、独立 Reranker、查询变换。",
        "",
    ]
    map_path.write_text("\n".join(lines), encoding="utf-8")

    from collections import Counter

    c = Counter(t.get("status") for t in data["tasks"])
    print(f"appended {len(cards)} cards; total {len(ids)}; counts {dict(c)}")
    print("ids:", ",".join(x["id"] for x in cards))


if __name__ == "__main__":
    main()
