"""技能系统数据类型 — 无外部依赖，仅使用 Python 标准库。

定义 Skill 的全生命周期数据：
- ``SkillMode``：技能激活模式（Prompt 注入 / 工具模式）
- ``SkillTrigger``：触发条件（关键词 / 正则 / 始终激活）
- ``SkillManifest``：技能的完整声明（YAML 反序列化目标）
- ``SkillMatch``：匹配结果（哪个技能被触发、置信度）
"""

import logging
import re
from collections.abc import Callable, Iterable
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum


class SkillMode(str, Enum):
    """技能激活模式。

    - ``prompt_injection``：匹配时将技能指令注入系统提示词，Agent 全程按此角色行事
    - ``tool``：技能注册为 Agent 可调用的工具，Agent 自主决定何时调用
    """

    PROMPT_INJECTION = "prompt_injection"
    TOOL = "tool"


class TriggerType(str, Enum):
    """触发条件类型。

    - ``keyword``：用户消息包含指定关键词（不区分大小写）
    - ``regex``：用户消息匹配正则表达式
    - ``always``：始终激活（不判断触发条件，适用于全局行为）
    """

    KEYWORD = "keyword"
    REGEX = "regex"
    ALWAYS = "always"


@dataclass
class SkillTrigger:
    """技能触发条件。

    支持三种触发类型，优先级：keyword > regex > always。
    多个条件为 OR 关系（任一命中即触发）。
    """

    type: TriggerType = TriggerType.KEYWORD
    keywords: list[str] = field(default_factory=list)
    regex: str = ""
    # 最低置信度（0.0 ~ 1.0），仅 keyword / regex 模式生效
    min_confidence: float = 0.5

    def matches(self, user_input: str) -> tuple[bool, float]:
        """检查用户输入是否匹配触发条件。

        Returns:
            (是否匹配, 置信度 0.0~1.0)
        """
        if self.type == TriggerType.ALWAYS:
            return True, 1.0

        text = user_input.lower()

        if self.type == TriggerType.KEYWORD and self.keywords:
            hits = sum(1 for kw in self.keywords if kw.lower() in text)
            if hits > 0:
                # 置信度：匹配数 / 最少需匹配数（默认 1 个即触发，越多越确信）
                # 至少匹配 1 个关键词给予 0.5 基础分，每多匹配一个递增
                base = 0.5
                extra = min(0.5, (hits - 1) * 0.15)
                confidence = base + extra
                return True, confidence

        if self.type == TriggerType.REGEX and self.regex:
            import re

            try:
                matches = re.findall(self.regex, user_input, re.IGNORECASE)
                if matches:
                    confidence = min(1.0, 0.5 + len(matches) * 0.2)
                    return True, confidence
            except re.error:
                pass

        return False, 0.0


@dataclass
class SkillManifest:
    """技能的完整 YAML 声明。

    当前实现：仅支持 YAML 文件定义。可按需扩展：Python Class 模式。
    """

    name: str
    version: str = "1.0"
    description: str = ""
    mode: SkillMode = SkillMode.PROMPT_INJECTION
    trigger: SkillTrigger = field(default_factory=SkillTrigger)
    # Prompt 注入模式：注入到系统提示词中的指令文本
    system_prompt: str = ""
    # 工具模式：绑定的工具名称列表（引用 ToolRegistry 中已注册的工具）
    tools: list[str] = field(default_factory=list)
    # 技能适用的模型列表（空 = 所有模型）
    models: list[str] = field(default_factory=list)
    # 是否启用
    enabled: bool = True
    # 来源文件路径（由 SkillManager 加载时自动填充）
    source_path: str = ""
    # builtin：内置 YAML。private：创建者私有。global：系统全局。
    origin: str = "builtin"

    @property
    def content_revision(self) -> str:
        """Canonical content identity; never accepts a caller-supplied digest."""
        from app.agents.skills.versioning import content_revision

        return content_revision(self)

    @property
    def effective_version(self) -> str:
        """Publishing version bound to the content that will actually execute."""
        from app.agents.skills.versioning import effective_version

        return effective_version(self)


@dataclass
class SkillMatch:
    """技能匹配结果。

    由 SkillManager.match() 返回，包含匹配到的技能及其置信度。
    """

    skill: SkillManifest
    confidence: float = 0.0
    trigger_reason: str = ""

    @property
    def is_match(self) -> bool:
        return self.confidence >= self.skill.trigger.min_confidence


@dataclass
class SkillContext:
    """技能激活后的上下文 — 注入到 Agent 管线中。

    包含 Prompt 注入文本和/或工具绑定信息。
    """

    # 注入到系统提示词中的技能指令
    prompt_injection: str = ""
    # 技能绑定的工具名称列表
    tool_names: list[str] = field(default_factory=list)
    # 匹配到的技能名称（用于调试/审计）
    skill_name: str = ""
    # 匹配置信度
    confidence: float = 0.0
    # 选中技能的来源：builtin、private 或 global。围栏只套后两种。
    origin: str = "builtin"
    # Exact activated snapshots, not all matching candidates or an authority grant.
    versions: list[dict[str, str]] = field(default_factory=list)
    mode: SkillMode = SkillMode.PROMPT_INJECTION


class SkillBindingRejected(RuntimeError):  # noqa: N818 -- stable typed resume rejection
    """A skill authority/version conflict cannot safely resume old execution."""

    def __init__(self) -> None:
        super().__init__("技能授权或版本已变化，无法继续执行，请重新发起请求。")


def empty_skill_binding() -> dict:
    return {"version": 1, "skills": [], "mode": "none", "tool_names": []}


def valid_skill_binding(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {"version", "skills", "mode", "tool_names"}:
        return False
    if type(value["version"]) is not int or value["version"] != 1:
        return False
    skills, names = value["skills"], value["tool_names"]
    if (not isinstance(skills, list) or len(skills) > 32 or not isinstance(names, list)
            or len(names) > 128 or any(not isinstance(n, str) or not n or len(n) > 256 for n in names)
            or len(set(names)) != len(names)):
        return False
    if not skills:
        return value == empty_skill_binding()
    if not isinstance(value["mode"], str) or value["mode"] not in {"prompt_injection", "tool"}:
        return False
    required = {"name", "version", "content_revision", "effective_version", "origin"}
    return all(isinstance(item, dict) and set(item) == required
               and all(isinstance(v, str) and 0 < len(v) <= 512 for v in item.values())
               and item["origin"] in {"builtin", "global", "private"} for item in skills)


class SkillRuntime:
    """Request-local metadata and discrete service authorization callbacks; no DB access."""

    def __init__(self, ctx: SkillContext | None = None, verify: Callable[[], None] | None = None):
        self.ctx = deepcopy(ctx) if ctx is not None else SkillContext()
        self.verify = verify
        self.registered_tool_names: frozenset[str] = frozenset()

    def binding(self) -> dict:
        if not self.ctx.versions:
            return empty_skill_binding()
        value = {"version": 1, "skills": deepcopy(self.ctx.versions),
                 "mode": self.ctx.mode.value, "tool_names": list(self.ctx.tool_names)}
        if not valid_skill_binding(value):
            raise SkillBindingRejected()
        return value

    def check(self) -> None:
        if self.ctx.versions and self.verify is not None:
            self.verify()

    def record(self, path: str, status: str, tool: str | None = None) -> None:
        safe_tool = "none" if tool is None else "unknown"
        if (isinstance(tool, str) and tool in self.registered_tool_names
                and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_:-]{0,127}", tool)):
            safe_tool = tool
        for identity in self.ctx.versions:
            logging.getLogger(__name__).info(
                "skill_runtime name=%s origin=%s effective_version=%s path=%s status=%s tool=%s",
                identity["name"], identity["origin"], identity["effective_version"], path, status, safe_tool)

    def prompt(self, base: str, path: str = "self") -> str:
        if not self.ctx.prompt_injection:
            return base
        self.check()
        self.record(path, "injected")
        return f"{base}\n\n---\n# 激活的技能指令\n{self.ctx.prompt_injection}"

    def allowed(self, original: frozenset[str] | None) -> frozenset[str] | None:
        if self.ctx.mode != SkillMode.TOOL or not self.ctx.versions:
            return original
        bound = frozenset(self.ctx.tool_names)
        return bound if original is None else bound & original

    def notice(self, path: str, available: Iterable[str] | None = None) -> str:
        if self.ctx.mode != SkillMode.TOOL or not self.ctx.versions:
            return ""
        self.check()
        if path in {"supervisor", "simple", "rag"}:
            self.record(path, "unsupported")
            return "【技能提示】当前路径不支持执行技能绑定工具。"
        if not self.ctx.tool_names or (
                available is not None and not set(self.ctx.tool_names).issubset(set(available))):
            self.record(path, "binding_missing")
            return "【技能提示】技能绑定工具缺失或在当前路径不可用。"
        self.record(path, "binding_available")
        return ""
