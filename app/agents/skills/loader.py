"""技能清单加载器。

负责从 YAML 文件目录发现、解析、校验技能清单。
骨架阶段仅支持 YAML 文件；内核打磨阶段补充 Python Class 模式。
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

logger = logging.getLogger(__name__)

# 内置技能目录（相对于 skills 包）
_BUILTIN_DIR = Path(__file__).resolve().parent / "builtin"


def _parse_trigger(data: dict[str, Any]) -> SkillTrigger:
    """从 YAML 字典解析触发条件。"""
    trigger_type = TriggerType(data.get("type", "keyword"))
    return SkillTrigger(
        type=trigger_type,
        keywords=data.get("keywords", []),
        regex=data.get("regex", ""),
        min_confidence=float(data.get("min_confidence", 0.5)),
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
    name = data.get("name", "").strip()
    if not name:
        raise ValueError(f"技能清单缺少必填字段 'name'：{source_path}")

    # 名称校验：仅允许小写字母、数字、下划线、连字符
    if not re.match(r"^[a-z0-9_-]+$", name):
        raise ValueError(
            f"技能名称 '{name}' 不合法：仅允许小写字母、数字、下划线、连字符。"
            f"来源：{source_path}"
        )

    trigger_data = data.get("trigger", {})
    if isinstance(trigger_data, dict):
        trigger = _parse_trigger(trigger_data)
    else:
        trigger = SkillTrigger()

    return SkillManifest(
        name=name,
        version=str(data.get("version", "1.0")),
        description=str(data.get("description", "")),
        mode=SkillMode(data.get("mode", "prompt_injection")),
        trigger=trigger,
        system_prompt=str(data.get("system_prompt", "")),
        tools=[str(t) for t in data.get("tools", [])],
        models=[str(m) for m in data.get("models", [])],
        enabled=bool(data.get("enabled", True)),
        source_path=source_path,
    )


def discover_skills(
    directories: list[Path] | None = None,
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
            logger.warning("技能目录不存在，跳过：%s", dir_path)
            continue

        for yaml_file in sorted(dir_path.glob("*.yaml")):
            _load_one(yaml_file, manifests)
        for yml_file in sorted(dir_path.glob("*.yml")):
            _load_one(yml_file, manifests)

    # 过滤禁用的技能
    active = [m for m in manifests.values() if m.enabled]
    logger.info(
        "已加载 %d 个技能（%d 个启用，%d 个禁用）",
        len(manifests),
        len(active),
        len(manifests) - len(active),
    )
    return active


def _load_one(file_path: Path, manifests: dict[str, SkillManifest]) -> None:
    """加载单个 YAML 文件并合并到清单字典。"""
    try:
        content = file_path.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
        if not isinstance(data, dict):
            logger.warning("技能文件格式无效（非字典），跳过：%s", file_path)
            return
        manifest = _parse_manifest(data, str(file_path))
        if manifest.name in manifests:
            logger.info("技能 '%s' 被覆盖：%s → %s", manifest.name, manifests[manifest.name].source_path, file_path)
        manifests[manifest.name] = manifest
        logger.debug("已加载技能：%s（%s）", manifest.name, file_path)
    except yaml.YAMLError as exc:
        logger.error("技能文件 YAML 解析失败：%s — %s", file_path, exc)
    except ValueError as exc:
        logger.error("技能清单校验失败：%s", exc)
    except Exception:  # noqa: BLE001 — 单个文件加载失败不应影响其他技能
        logger.exception("加载技能文件失败：%s", file_path)


def load_skill_from_yaml(yaml_text: str, source: str = "<string>") -> SkillManifest:
    """从 YAML 字符串直接解析技能清单（用于 API 动态注册）。

    Args:
        yaml_text: YAML 格式的技能定义。
        source: 来源标识（用于错误提示）。

    Returns:
        解析后的 SkillManifest。
    """
    data = yaml.safe_load(yaml_text)
    if not isinstance(data, dict):
        raise ValueError(f"YAML 内容不是有效的字典：{source}")
    return _parse_manifest(data, source)