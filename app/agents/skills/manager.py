"""技能管理器。

技能系统的核心编排器，负责：
1. 加载技能清单（YAML 文件 → SkillManifest）
2. 匹配用户意图（用户输入 → 匹配的技能列表）
3. 激活技能上下文（匹配结果 → SkillContext，注入管线）
4. 技能注册为工具（Tool 模式：SkillManifest → AgentTool）

当前实现：仅支持 YAML 声明式技能。可按需扩展：
- Python Class 模式（SkillProtocol）
- LLM 语义匹配（替代关键词/正则的粗粒度匹配）
- 技能热加载（文件监控 + 自动重载）
- 技能效果评估与自动淘汰（结合 Reflect 闭环）
"""

import logging
from pathlib import Path

from app.agents.skills.base import (
    SkillContext,
    SkillManifest,
    SkillMatch,
    SkillMode,
)
from app.agents.skills.loader import discover_skills, load_skill_from_yaml
from app.agents.tools.base import Tool

logger = logging.getLogger(__name__)


class SkillManager:
    """技能管理器。

    使用方式：:

        manager = SkillManager()
        manager.load()  # 加载内置技能

        # 匹配技能
        matches = manager.match("帮我审查这段代码")
        ctx = manager.activate(matches)

        # 注入到 Agent 管线
        system_prompt = manager.inject_prompt(ctx, base_prompt)
        tools = manager.collect_tools(ctx, base_tool_registry)
    """

    def __init__(self) -> None:
        self._skills: dict[str, SkillManifest] = {}
        self._loaded: bool = False

    # ── 加载 ──────────────────────────────────────

    def load(self, directories: list[Path] | None = None) -> int:
        """从指定目录加载技能清单。

        Args:
            directories: 技能 YAML 文件目录列表。默认仅内置目录。

        Returns:
            加载的技能数量。
        """
        manifests = discover_skills(directories)
        self._skills = {m.name: m for m in manifests}
        self._loaded = True
        logger.info("SkillManager 已加载 %d 个技能", len(self._skills))
        return len(self._skills)

    def register(self, manifest: SkillManifest) -> None:
        """动态注册一个技能（API 调用）。

        同名技能会被覆盖。
        """
        self._skills[manifest.name] = manifest
        logger.info("已注册技能：%s（v%s）", manifest.name, manifest.version)

    def register_from_yaml(self, yaml_text: str, source: str = "<api>") -> SkillManifest:
        """从 YAML 字符串动态注册技能。

        Args:
            yaml_text: YAML 格式的技能定义。
            source: 来源标识。

        Returns:
            解析并注册的 SkillManifest。
        """
        manifest = load_skill_from_yaml(yaml_text, source)
        self.register(manifest)
        return manifest

    def unregister(self, name: str) -> bool:
        """移除一个技能。"""
        if name in self._skills:
            del self._skills[name]
            logger.info("已移除技能：%s", name)
            return True
        return False

    def get(self, name: str) -> SkillManifest | None:
        """按名称获取技能清单。"""
        return self._skills.get(name)

    def list_all(self) -> list[SkillManifest]:
        """列出所有已加载的技能。"""
        return list(self._skills.values())

    def reload(self) -> int:
        """重新加载技能清单（热加载）。"""
        self._skills.clear()
        return self.load()

    # ── 匹配 ──────────────────────────────────────

    def match(
        self,
        user_input: str,
        *,
        max_results: int = 3,
        min_confidence: float = 0.3,
    ) -> list[SkillMatch]:
        """匹配用户输入到技能。

        按置信度降序排列，返回前 max_results 个匹配。

        Args:
            user_input: 用户输入文本。
            max_results: 最多返回的匹配数。
            min_confidence: 最低置信度阈值。

        Returns:
            匹配的技能列表。
        """
        if not user_input:
            return []

        matches: list[SkillMatch] = []
        for skill in self._skills.values():
            if not skill.enabled:
                continue
            is_match, confidence = skill.trigger.matches(user_input)
            if is_match and confidence >= min_confidence:
                # 确定触发原因
                reason = self._trigger_reason(skill, user_input)
                matches.append(
                    SkillMatch(
                        skill=skill,
                        confidence=confidence,
                        trigger_reason=reason,
                    )
                )

        # 按置信度降序
        matches.sort(key=lambda m: m.confidence, reverse=True)
        return matches[:max_results]

    def _trigger_reason(self, skill: SkillManifest, user_input: str) -> str:
        """生成可读的触发原因描述。"""
        trigger = skill.trigger
        if trigger.type.value == "always":
            return "always_active"
        if trigger.type.value == "keyword":
            hits = [kw for kw in trigger.keywords if kw.lower() in user_input.lower()]
            return f"keyword_match: {', '.join(hits)}"
        if trigger.type.value == "regex":
            return f"regex_match: {trigger.regex}"
        return "unknown"

    # ── 激活 ──────────────────────────────────────

    def activate(self, matches: list[SkillMatch]) -> SkillContext:
        """激活匹配到的技能，生成 SkillContext。

        多个技能匹配时：
        - Prompt 注入模式：合并所有技能的 system_prompt
        - Tool 模式：合并所有技能的工具列表

        Args:
            matches: 匹配结果列表。

        Returns:
            合并后的 SkillContext。
        """
        ctx = SkillContext()
        prompt_parts: list[str] = []
        tool_names: list[str] = []
        skill_names: list[str] = []

        for match in matches:
            if not match.is_match:
                continue
            skill = match.skill
            skill_names.append(skill.name)

            if skill.mode == SkillMode.PROMPT_INJECTION and skill.system_prompt:
                prompt_parts.append(
                    f"## 技能：{skill.name}\n{skill.system_prompt}"
                )

            if skill.mode == SkillMode.TOOL:
                tool_names.extend(skill.tools)

        ctx.prompt_injection = "\n\n".join(prompt_parts)
        ctx.tool_names = list(dict.fromkeys(tool_names))  # 去重保序
        ctx.skill_name = ", ".join(skill_names) if skill_names else ""
        ctx.confidence = max((m.confidence for m in matches), default=0.0)

        if skill_names:
            logger.info(
                "技能已激活：%s（置信度 %.2f，模式 %s）",
                ctx.skill_name,
                ctx.confidence,
                "prompt_injection" if prompt_parts else "tool",
            )

        return ctx

    # ── 注入管线 ──────────────────────────────────

    def inject_prompt(self, ctx: SkillContext, base_prompt: str) -> str:
        """将技能上下文注入到基础系统提示词中。

        Args:
            ctx: 技能激活上下文。
            base_prompt: 基础系统提示词。

        Returns:
            注入后的完整系统提示词。
        """
        if not ctx.prompt_injection:
            return base_prompt
        return f"{base_prompt}\n\n---\n# 激活的技能指令\n{ctx.prompt_injection}"

    def collect_tools(
        self,
        ctx: SkillContext,
        registry: "ToolRegistry | None" = None,
    ) -> list[Tool]:
        """从技能上下文收集工具模式绑定的工具。

        仅返回 Tool 模式的技能绑定的工具（需在 ToolRegistry 中已注册）。

        Args:
            ctx: 技能激活上下文。
            registry: 工具注册表，用于查找已注册的工具。

        Returns:
            技能绑定的工具列表。
        """
        from app.agents.tools.base import ToolRegistry

        tools: list[Tool] = []
        if registry is None:
            return tools

        for tool_name in ctx.tool_names:
            tool = registry.get(tool_name)
            if tool is not None:
                tools.append(tool)
            else:
                logger.warning(
                    "技能 '%s' 绑定的工具 '%s' 未在 ToolRegistry 中注册",
                    ctx.skill_name,
                    tool_name,
                )
        return tools

    # ── 描述 ──────────────────────────────────────

    def describe(self) -> str:
        """生成供大模型阅读的技能清单文本。

        用于注入到系统提示词中，让 Agent 知道有哪些可用技能。
        """
        if not self._skills:
            return "（当前无可用技能）"

        lines: list[str] = []
        for skill in self._skills.values():
            if not skill.enabled:
                continue
            trigger_desc = self._describe_trigger(skill)
            lines.append(
                f"- **{skill.name}**（v{skill.version}）：{skill.description}\n"
                f"  触发条件：{trigger_desc} | 模式：{skill.mode.value}"
            )
        return "\n".join(lines)

    def _describe_trigger(self, skill: SkillManifest) -> str:
        """生成触发条件的可读描述。"""
        trigger = skill.trigger
        if trigger.type.value == "always":
            return "始终激活"
        if trigger.type.value == "keyword":
            return f"关键词：{', '.join(trigger.keywords)}"
        if trigger.type.value == "regex":
            return f"正则：{trigger.regex}"
        return "未知"


# ── 全局单例（进程级缓存）──

_skill_manager: SkillManager | None = None


def get_skill_manager() -> SkillManager:
    """获取全局进程级 SkillManager 单例。

    首次调用时自动加载内置技能。
    """
    global _skill_manager
    if _skill_manager is None:
        _skill_manager = SkillManager()
        _skill_manager.load()
    return _skill_manager


def reset_skill_manager() -> None:
    """重置全局单例（测试用）。"""
    global _skill_manager
    _skill_manager = None