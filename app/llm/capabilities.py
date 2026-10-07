"""生成模型的能力契约（版本化）。

一条能力 = 「哪个部署 + 哪个模型 + 已核对的窗口与最大输出 + 用什么计数 + 依据来源」。
部署参与身份：同一个模型名在自建网关上跑，不等于厂商官方接口上的同一模型，
窗口不能跟着模型名一起被借走（RAG-021 已在 Embedding 侧立下同一条规矩）。

只登记**核对过厂商公开依据**的条目，登记时写下来源 URL 与核对日期。
没核对过的模型一律不猜：解析不到就是「无批准配置」，由护栏拒绝调用。

运营者显式声明通道：模型不在已核对表内时，运营者必须同时填写窗口/最大输出
与依据来源才放行，且**声明按部署+模型生效**（省略部署时只作用于主部署）。
数值由运营者给，不是我们替他猜——但也不能写多少都算，超出合理区间按未批准处理。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlsplit

from app.core.config import settings
from app.llm.counters import (
    OFFICIAL_METHOD,
    UTF8_METHOD,
    estimate_messages_tokens,
    official_counter_for,
)

# 已核对条目的核对日期（2026-10-07 实取厂商公开页面）。
VERIFIED_ON = "2026-10-07"
# 声明条目拿不到具体部署时的占位（如 LLM_BASE_URL 为空）。正常路径下声明
# 都带部署，这里的 "*" 不代表「任意部署都适用」。
OPERATOR_DECLARED_DEPLOYMENT = "*"
# 声明条目没有核对日期——我们没核对过，不能冒充已核对条目。
OPERATOR_DECLARED_VERIFIED_ON = "operator-declared"

OPENAI_GPT4O_MINI_SOURCE = "https://platform.openai.com/docs/models/gpt-4o-mini"
DEEPSEEK_PRICING_SOURCE = "https://api-docs.deepseek.com/quick_start/pricing"

# 运营者声明的合理上界。登记行为不能越过「不猜未知上限」，所以声明也不能是
# 「写多少都算」：超过这两个数几乎必然是填错或拍脑袋的，按未批准处理。
# 上界取当前公开在售模型的量级（最大公开窗口 1M~2M、最大输出 384K）。
MAX_DECLARED_CONTEXT_WINDOW = 2_000_000
MAX_DECLARED_OUTPUT_TOKENS = 400_000

logger = logging.getLogger(__name__)


def normalize_deployment(base_url: str) -> str:
    """把 base_url 归一成部署身份：去掉凭据，host 小写，去尾斜杠。"""
    raw = (base_url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return ""
    host = (parts.hostname or "").lower()
    if not host:
        return ""
    port = f":{parts.port}" if parts.port else ""
    path = (parts.path or "").rstrip("/")
    return f"{host}{port}{path}"


@dataclass(frozen=True)
class GenerationCapability:
    """一条已登记的生成能力。不可变，改一条就是换一个版本。"""

    deployment: str
    model: str
    context_window: int
    max_output_tokens: int
    counter: Callable[[Any], int]
    counting_method: str
    safety_margin: int
    source: str
    verified_on: str
    version: str
    notes: str = ""

    def __post_init__(self) -> None:
        for name in ("deployment", "model", "counting_method", "source", "verified_on", "version"):
            if not getattr(self, name):
                raise ValueError(f"能力契约缺少必填字段：{name}")
        for name in ("context_window", "max_output_tokens"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"能力契约的 {name} 必须为正整数")
        if not isinstance(self.safety_margin, int) or self.safety_margin < 0:
            raise ValueError("能力契约的 safety_margin 不能为负")
        if not callable(self.counter):
            raise ValueError("能力契约缺少可用的计数器")

    def metadata(self) -> dict[str, Any]:
        """不含计数器的可序列化描述，供日志与测试断言。"""
        return {
            "context_window": self.context_window,
            "counting_method": self.counting_method,
            "deployment": self.deployment,
            "max_output_tokens": self.max_output_tokens,
            "model": self.model,
            "notes": self.notes,
            "safety_margin": self.safety_margin,
            "source": self.source,
            "verified_on": self.verified_on,
            "version": self.version,
        }

    def describe(self) -> str:
        """一行人可读描述。用于文档与排障，不含密钥或请求正文。"""
        return (
            f"{self.model} @ {self.deployment}"
            f" 窗口={self.context_window}"
            f" 最大输出={self.max_output_tokens}"
            f" 计数={self.counting_method}"
            f" 附加余量={self.safety_margin}"
            f" 依据={self.source}"
            f" 核对={self.verified_on}"
            f" 版本={self.version}"
        )


# 已核对条目：只有拿到厂商公开依据的两条/三款。
# deployments 里同时登记带 /v1 与不带 /v1 两种写法——厂商文档两种都在用，
# 同一个官方端点不会因为路径写法不同而拥有另一套窗口。
_BUILTIN_SPECS: tuple[tuple[tuple[str, ...], str, int, int, str, str, str], ...] = (
    (
        ("api.openai.com", "api.openai.com/v1"),
        "gpt-4o-mini",
        128_000,
        16_384,
        OPENAI_GPT4O_MINI_SOURCE,
        "openai-gpt4o-mini-128k-16k-20261007",
        "厂商模型页公示的上下文窗口与最大输出。",
    ),
    (
        ("api.deepseek.com", "api.deepseek.com/v1"),
        "deepseek-flash",
        1_000_000,
        384_000,
        DEEPSEEK_PRICING_SOURCE,
        "deepseek-flash-1m-384k-20261007",
        "厂商定价页公示的上下文长度（1M）与最大输出长度（384K）。",
    ),
    (
        ("api.deepseek.com", "api.deepseek.com/v1"),
        "deepseek-v4-pro",
        1_000_000,
        384_000,
        DEEPSEEK_PRICING_SOURCE,
        "deepseek-v4-pro-1m-384k-20261007",
        "厂商定价页公示的上下文长度（1M）与最大输出长度（384K）。",
    ),
)


def _build_index() -> dict[tuple[str, str], GenerationCapability]:
    table: dict[tuple[str, str], GenerationCapability] = {}
    for deployments, model, window, max_output, source, version, notes in _BUILTIN_SPECS:
        for deployment in deployments:
            table[(deployment, model)] = GenerationCapability(
                deployment=deployment,
                model=model,
                context_window=window,
                max_output_tokens=max_output,
                counter=estimate_messages_tokens,
                counting_method=UTF8_METHOD,
                safety_margin=0,
                source=source,
                verified_on=VERIFIED_ON,
                version=version,
                notes=notes,
            )
    return table


_BUILTIN: dict[tuple[str, str], GenerationCapability] = _build_index()


def _with_best_counter(capability: GenerationCapability) -> GenerationCapability:
    """官方计数器可用就换上它，否则保留保守估算。"""
    official = official_counter_for(capability.deployment, capability.model)
    if official is None:
        return capability
    return replace(capability, counter=official, counting_method=OFFICIAL_METHOD)


def resolve_generation_capability(base_url: str, model: str) -> GenerationCapability | None:
    """按 (deployment, model) 查已核对能力。查不到返回 None，不猜。"""
    key = (normalize_deployment(base_url), (model or "").strip())
    capability = _BUILTIN.get(key)
    if capability is None:
        return None
    return _with_best_counter(capability)


def declared_capability(model: str, base_url: str = "") -> GenerationCapability | None:
    """运营者显式声明的能力。

    格式：``部署!模型=窗口:最大输出``，多项逗号分隔。``部署`` 用与内置表相同的
    归一化规则（去凭据、host 小写、去尾斜杠），**省略部署时只对主部署生效**
    ——即 ``LLM_BASE_URL`` 对应的那个部署。声明与依据来源缺一即视为未批准。

    声明必须绑定部署，理由与内置表一致：窗口不能跟着模型名一起被借走。否则
    「为 gateway-a 声明一次」会让任何别家网关上的同名模型都拿到同一个窗口。
    """
    raw = (settings.LLM_CAPABILITY_DECLARED or "").strip()
    source = (settings.LLM_CAPABILITY_DECLARED_SOURCE or "").strip()
    if not raw or not source:
        return None
    target = (model or "").strip()
    if not target:
        return None
    wanted = normalize_deployment(base_url or settings.LLM_BASE_URL or "")
    for part in raw.split(","):
        item = part.strip()
        if "=" not in item:
            continue
        left, _, spec = item.partition("=")
        left = left.strip()
        if "!" in left:
            deployment_text, _, name = left.partition("!")
            deployment = _declared_deployment(deployment_text)
            name = name.strip()
        else:
            # 省略部署：只认主部署，不得被 intent / fallback 指向的其它端点借用。
            deployment = normalize_deployment(settings.LLM_BASE_URL or "")
            name = left
        if name != target or deployment != wanted:
            continue
        window_text, _, output_text = spec.partition(":")
        try:
            context_window = int(window_text.strip())
            max_output_tokens = int(output_text.strip())
        except ValueError:
            return None
        if not _within_reasonable_range(target, context_window, max_output_tokens):
            return None
        return GenerationCapability(
            deployment=deployment or OPERATOR_DECLARED_DEPLOYMENT,
            model=target,
            context_window=context_window,
            max_output_tokens=max_output_tokens,
            counter=estimate_messages_tokens,
            counting_method="operator-declared",
            safety_margin=0,
            source=source,
            verified_on=OPERATOR_DECLARED_VERIFIED_ON,
            version=f"operator-declared-{target}",
            notes="运营者显式声明，非本仓库核对；数值与依据由运营者负责。",
        )
    return None


def _declared_deployment(text: str) -> str:
    """声明里的部署写法。允许省略 scheme（``gateway.internal/v1`` 也认）。"""
    raw = (text or "").strip()
    if raw and "://" not in raw:
        raw = f"https://{raw}"
    return normalize_deployment(raw)


def _within_reasonable_range(model: str, context_window: int, max_output_tokens: int) -> bool:
    """声明值必须落在合理区间内，越界按未批准处理并留下明确日志。

    「数值由运营者给」不等于「写多少都算」：1e9 的窗口不是声明，是把护栏关掉。
    """
    if context_window <= 0 or max_output_tokens <= 0:
        logger.error(
            "llm_capability_declared_out_of_range model=%s window=%d max_output=%d："
            "窗口与最大输出必须为正整数",
            model,
            context_window,
            max_output_tokens,
        )
        return False
    if context_window > MAX_DECLARED_CONTEXT_WINDOW:
        logger.error(
            "llm_capability_declared_out_of_range model=%s window=%d：超过合理上界 %d",
            model,
            context_window,
            MAX_DECLARED_CONTEXT_WINDOW,
        )
        return False
    if max_output_tokens > MAX_DECLARED_OUTPUT_TOKENS:
        logger.error(
            "llm_capability_declared_out_of_range model=%s max_output=%d：超过合理上界 %d",
            model,
            max_output_tokens,
            MAX_DECLARED_OUTPUT_TOKENS,
        )
        return False
    if max_output_tokens > context_window:
        logger.error(
            "llm_capability_declared_out_of_range model=%s window=%d max_output=%d："
            "最大输出不能超过上下文窗口",
            model,
            context_window,
            max_output_tokens,
        )
        return False
    return True


def resolve_effective_capability(base_url: str, model: str) -> GenerationCapability | None:
    """内置已核对优先，其次运营者声明，都没有则为 None（无批准配置）。"""
    builtin = resolve_generation_capability(base_url, model)
    if builtin is not None:
        return builtin
    return declared_capability(model, base_url)
