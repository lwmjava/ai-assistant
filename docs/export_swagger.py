"""导出 / 更新 FastAPI 的 OpenAPI 文档到 docs/swagger.json。

用法：
    python docs/export_swagger.py
    python docs/export_swagger.py --output docs/swagger.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 确保能找到 app 包（无论从哪运行此脚本）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.main import app


def export_openapi(*, output_path: Path | None = None) -> Path:
    """导出 FastAPI OpenAPI 规范到指定文件。

    Args:
        output_path: 输出文件路径，默认为项目根目录下的 docs/swagger.json。
    """
    if output_path is None:
        project_root = Path(__file__).resolve().parent.parent
        output_path = project_root / "docs" / "swagger.json"

    openapi = app.openapi()

    # 补充 / 覆写 API 文档元信息
    openapi["info"]["description"] = "企业级开源 AI 助手平台 — RAG + Agent 编排 + MCP 协议"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(openapi, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="导出 FastAPI OpenAPI 规范到 swagger.json")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="输出文件路径（默认：docs/swagger.json）",
    )
    args = parser.parse_args()

    output_path = export_openapi(output_path=args.output)
    print(f"Swagger 文档已生成：{output_path.resolve()}")


if __name__ == "__main__":
    main()
