"""技能清单加载器。

负责从 YAML 文件目录发现、解析、校验技能清单。
当前实现：仅支持 YAML 文件。可按需扩展：Python Class 模式。
"""

import logging
import re
from pathlib import Path
from typing import Any

import yaml

from app.agents.skills.base import (
    SkillManifest,
    SkillMode,
    SkillTrigger,
    TriggerType,
)
from app.agents.skills.versioning import validate_manifest

logger = logging.getLogger(__name__)

# 内置技能目录（相对于 skills 包）
_BUILTIN_DIR = Path(__file__).resolve().parent / "builtin"


class _UniqueSafeLoader(yaml.SafeLoader):
    """Reject ambiguous duplicate keys before constructing a manifest."""

    def construct_mapping(self, node: Any, deep: bool = False) -> Any:
        self.flatten_mapping(node)
        seen: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                if key in seen:
                    raise ValueError("skill_yaml_duplicate_key")
                seen.add(key)
            except TypeError:
                raise ValueError("skill_yaml_invalid_key") from None
        return super().construct_mapping(node, deep=deep)


def _parse_trigger(data: dict[str, Any]) -> SkillTrigger:
    """从 YAML 字典解析触发条件。"""
    trigger_type = TriggerType(data.get("type", "keyword"))
    return SkillTrigger(
        type=trigger_type,
        keywords=data.get("keywords", []),
        regex=data.get("regex", ""),
        min_confidence=data.get("min_confidence", 0.5),
    )


def _parse_manifest(data: dict[str, Any], source_path: str = "") -> SkillManifest:
    """从 YAML 字典解析技能清单。

    Args:
        data: YAML 反序列化后的字典。
        source_path: 来源文件路径（用于调试/审计）。

    Returns:
        解析后的 SkillManifest。

    Raises:
        ValueError: 必填字段缺失或格式错误。
    """
    name = data.get("name", "")
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9_-]{1,64}", name):
        raise ValueError("skill_invalid_name")

    trigger_data = data.get("trigger", {})
    if isinstance(trigger_data, dict):
        trigger = _parse_trigger(trigger_data)
    else:
        raise ValueError("skill_invalid_trigger")

    manifest = SkillManifest(
        name=name,
        version=data.get("version", "1.0"),
        description=data.get("description", ""),
        mode=SkillMode(data.get("mode", "prompt_injection")),
        trigger=trigger,
        system_prompt=data.get("system_prompt", ""),
        tools=data.get("tools", []),
        models=data.get("models", []),
        enabled=data.get("enabled", True),
        source_path=source_path,
    )
    validate_manifest(manifest)
    return manifest


def discover_skills(
    directories: list[Path] | None = None,
    *,
    strict: bool = False,
) -> list[SkillManifest]:
    """从指定目录发现并加载所有技能清单。

    扫描目录下所有 .yaml / .yml 文件，解析为 SkillManifest。
    同名技能后加载的覆盖先加载的（用户目录优先于内置目录）。

    Args:
        directories: 要扫描的目录列表。默认仅扫描内置目录。

    Returns:
        已解析的技能清单列表（仅包含 enabled=True 的技能）。
    """
    if directories is None:
        directories = [_BUILTIN_DIR]

    manifests: dict[str, SkillManifest] = {}

    for directory in directories:
        dir_path = Path(directory)
        if not dir_path.is_dir():
            if strict:
                raise ValueError("skill_directory_unavailable")
            logger.warning("skill_directory_unavailable")
            continue

        try:
            files = [*sorted(dir_path.glob("*.yaml")), *sorted(dir_path.glob("*.yml"))]
            if len(files) > 256:
                raise ValueError("skill_directory_size")
            for skill_file in files:
                _load_one(skill_file, manifests, strict=strict)
        except (OSError, ValueError):
            if strict:
                raise ValueError("skill_directory_load_failed") from None
            logger.error("skill_directory_load_failed")

    # 过滤禁用的技能
    active = [m for m in manifests.values() if m.enabled]
    logger.info(
        "已加载 %d 个技能（%d 个启用，%d 个禁用）",
        len(manifests),
        len(active),
        len(manifests) - len(active),
    )
    return active


def _load_one(file_path: Path, manifests: dict[str, SkillManifest], *, strict: bool = False) -> None:
    """加载单个 YAML 文件并合并到清单字典。"""
    try:
        if file_path.stat().st_size > 131072:
            raise ValueError("skill_file_size")
        content = file_path.read_text(encoding="utf-8")
        manifest = load_skill_from_yaml(content, str(file_path))
        if manifest.name in manifests:
            logger.info("skill_replaced: %s", manifest.name)
        manifests[manifest.name] = manifest
        logger.debug("skill_loaded: %s", manifest.name)
    except (OSError, UnicodeError, ValueError):
        logger.error("skill_file_load_failed")
        if strict:
            raise ValueError("skill_file_load_failed") from None


def load_skill_from_yaml(yaml_text: str, source: str = "<string>") -> SkillManifest:
    """从 YAML 字符串直接解析技能清单（用于 API 动态注册）。

    Args:
        yaml_text: YAML 格式的技能定义。
        source: 来源标识（用于错误提示）。

    Returns:
        解析后的 SkillManifest。
    """
    try:
        if not isinstance(yaml_text, str) or len(yaml_text.encode("utf-8")) > 131072:
            raise ValueError("skill_yaml_size")
        data = yaml.load(yaml_text, Loader=_UniqueSafeLoader)
        if not isinstance(data, dict):
            raise ValueError("skill_yaml_mapping_required")
        return _parse_manifest(data, source)
    except (yaml.YAMLError, ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError("skill_yaml_invalid") from None
