"""检查编排的口令、跨域与 JWT 必填声明，不展开或打印配置值。"""

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

import yaml


def check(root: Path) -> list[str]:
    failures = []
    for filename in ("docker-compose.yml", "docker-compose.prod.yml"):
        try:
            document = yaml.safe_load((root / filename).read_text(encoding="utf-8"))
            services = document["services"]
            database = services["db"]["environment"]
            application = services["app"]["environment"]
        except (OSError, yaml.YAMLError, KeyError, TypeError):
            failures.append(f"{filename}: 文件不可读或结构不完整")
            continue
        for source, key in ((database, "POSTGRES_PASSWORD"),
                            (application, "JWT_SECRET_KEY"), (application, "CORS_ORIGINS")):
            value = source.get(key)
            if not isinstance(value, str) or not value.startswith("${" + key + ":?") or not value.endswith("}"):
                failures.append(f"{filename}: {key} 必须显式提供且禁止空值")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--verify-compose", action="store_true", help="用合成配置验证真实 Compose 展开及缺失/空值拒绝")
    args = parser.parse_args()
    errors = check(args.root)
    for error in errors:
        print(error)
    if errors:
        return 1
    print("两份编排的数据库口令、JWT 和跨域来源均为必填，无默认凭据或通配来源。")
    if args.verify_compose:
        return verify_compose(args.root)
    return 0


def verify_compose(root: Path) -> int:
    """仅展开合成配置，不启动容器，不打印展开值。"""
    values = {
        "POSTGRES_PASSWORD": "synthetic-compose-password",
        "JWT_SECRET_KEY": "synthetic-compose-jwt-value-only",
        "CORS_ORIGINS": "https://frontend.example.invalid",
        "DATABASE_URL": "postgresql://aiassistant:synthetic-compose-password@db:5432/aiassistant",
        "LLM_API_KEY": "synthetic-model-key",
        "EMBEDDING_PROVIDER": "openai",
        "EMBEDDING_API_KEY": "synthetic-embedding-key",
        "EMBEDDING_BASE_URL": "https://embedding.example.invalid/v1",
        "EMBEDDING_MODEL": "synthetic-embedding-model",
        "EMBEDDING_DIM": "128",
    }
    environment = {
        key: value for key, value in os.environ.items()
        if key not in values and not key.startswith("COMPOSE_")
    }
    evidence = []
    try:
        with tempfile.NamedTemporaryFile(suffix=".env", mode="w", encoding="utf-8", delete=False) as stream:
            temporary = Path(stream.name)
        try:
            for filename in ("docker-compose.yml", "docker-compose.prod.yml"):
                cases = [(None, "valid")]
                cases += [(key, mode) for key in ("POSTGRES_PASSWORD", "JWT_SECRET_KEY", "CORS_ORIGINS")
                          for mode in ("missing", "empty")]
                for key, mode in cases:
                    current = dict(values)
                    if key:
                        if mode == "missing":
                            current.pop(key)
                        else:
                            current[key] = ""
                    temporary.write_text(
                        "\n".join(f"{name}={value}" for name, value in current.items()), encoding="utf-8",
                    )
                    result = subprocess.run(
                        ["docker", "compose", "--file", str((root / filename).resolve()),
                         "--env-file", str(temporary), "--project-name", "compose-defaults-check",
                         "config", "--format", "json"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace",
                        timeout=30, env=environment,
                    )
                    if mode == "valid":
                        if result.returncode:
                            raise ValueError("合法合成配置未能展开")
                        services = json.loads(result.stdout)["services"]
                        if services["db"]["environment"]["POSTGRES_PASSWORD"] != values["POSTGRES_PASSWORD"]:
                            raise ValueError("数据库口令注入不一致")
                        for name in ("JWT_SECRET_KEY", "CORS_ORIGINS"):
                            if services["app"]["environment"][name] != values[name]:
                                raise ValueError("应用配置注入不一致")
                    elif result.returncode == 0:
                        raise ValueError("缺失或空值未被拒绝")
                    evidence.append({"file": filename, "field": key, "case": mode,
                                     "compose_exit": result.returncode, "passed": True})
        finally:
            temporary.unlink(missing_ok=True)
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, AssertionError):
        print("Compose 合成配置验证失败；请检查 Docker Compose v2 与编排必填声明，底层输出未回显。")
        return 1
    print(json.dumps({"checks": evidence, "started_containers": 0, "result": "passed"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
