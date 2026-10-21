"""只读检查生产编排契约，不展开配置、不连接 Docker、不打印凭据。"""

import argparse
import sys
from pathlib import Path

import yaml


def check(root: Path) -> list[str]:
    """检查默认单实例与显式 Milvus 覆盖的声明。"""
    errors: list[str] = []
    files = [root / "docker-compose.prod.yml", root / "deploy/compose.milvus.prod.yml"]
    documents = []
    for path in files:
        if not path.is_file():
            errors.append("缺少生产编排文件：" + path.name)
            continue
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            errors.append("生产编排文件不可解析：" + path.name)
            continue
        if not isinstance(document, dict) or not isinstance(document.get("services"), dict):
            errors.append("生产编排缺少 services：" + path.name)
            continue
        documents.append(document)
    if errors:
        return errors
    base, overlay = documents
    services = base["services"]
    if set(services) != {"app", "db", "milvus", "etcd", "minio"}:
        errors.append("默认生产编排必须声明 app/db 与内部 Milvus、etcd、MinIO")
    app = services.get("app", {})
    db = services.get("db", {})
    environment = app.get("environment", {})
    if environment.get("RAG_VECTOR_STORE") != "milvus":
        errors.append("默认生产向量库必须为 milvus")
    if app.get("build", {}).get("args", {}).get("INSTALL_MILVUS") != "true":
        errors.append("默认镜像必须安装已批准的 Milvus 客户端")
    if app.get("depends_on", {}) != {
        "db": {"condition": "service_healthy"},
        "milvus": {"condition": "service_healthy"},
    }:
        errors.append("默认应用必须等待数据库和 Milvus 健康")
    for key in ("JWT_SECRET_KEY", "CORS_ORIGINS", "LLM_API_KEY", "DATABASE_URL",
                "EMBEDDING_PROVIDER", "EMBEDDING_API_KEY", "EMBEDDING_BASE_URL", "EMBEDDING_MODEL", "EMBEDDING_DIM"):
        value = environment.get(key, "")
        if not isinstance(value, str) or "${" + key + ":?" not in value:
            errors.append("生产应用缺少必填变量约束：" + key)
    if "POSTGRES_PASSWORD" in environment.get("DATABASE_URL", ""):
        errors.append("数据库连接串不得插入未编码明文口令")
    password = db.get("environment", {}).get("POSTGRES_PASSWORD", "")
    if not isinstance(password, str) or "${POSTGRES_PASSWORD:?" not in password:
        errors.append("生产数据库口令必须显式提供")
    if db.get("ports"):
        errors.append("数据库不得向宿主发布端口")
    if any(services.get(name, {}).get("ports") for name in ("milvus", "etcd", "minio")):
        errors.append("默认向量库与内部存储不得发布宿主端口")
    if not app.get("ports") or not str(app["ports"][0]).startswith("${APP_BIND_HOST:-127.0.0.1}"):
        errors.append("应用端口必须默认绑定 loopback")
    if not app.get("volumes") or not db.get("volumes") or not base.get("volumes"):
        errors.append("生产应用和数据库必须持久化")
    if "--workers" not in app.get("command", []) or app["command"][-1] != "1":
        errors.append("应用必须显式为单 worker")
    optional = overlay["services"]
    if set(optional) != {"app", "milvus", "etcd", "minio"}:
        errors.append("Milvus 覆盖必须包含受控三服务及 app")
    if optional.get("app", {}).get("environment", {}).get("RAG_VECTOR_STORE") != "milvus":
        errors.append("可选覆盖必须显式切换 Milvus")
    if optional.get("app", {}).get("depends_on", {}).get("milvus") != {"condition": "service_healthy"}:
        errors.append("可选应用必须等待 Milvus 健康")
    milvus = optional.get("milvus", {})
    if optional.get("app", {}).get("build", {}).get("args", {}).get("INSTALL_MILVUS") != "true":
        errors.append("Milvus 覆盖必须安装已批准的可选客户端")
    minio_environment = optional.get("minio", {}).get("environment", {})
    milvus_environment = milvus.get("environment", {})
    for source, destination in (("MINIO_ROOT_USER", "MINIO_ACCESS_KEY"), ("MINIO_ROOT_PASSWORD", "MINIO_SECRET_KEY")):
        value = minio_environment.get(source, "")
        if "${" + source + ":?" not in value or milvus_environment.get(destination) != value:
            errors.append("对象存储凭据必须必填且保持一致")
    if milvus.get("image") != "milvusdb/milvus:v2.5.11":
        errors.append("Milvus 覆盖必须沿用已批准镜像")
    if any(optional.get(name, {}).get("ports") for name in ("milvus", "etcd", "minio")):
        errors.append("向量库与内部存储不得发布宿主端口")
    if milvus.get("depends_on") != {"etcd": {"condition": "service_healthy"},
                                    "minio": {"condition": "service_healthy"}}:
        errors.append("Milvus 必须等待外部存储健康")
    for name, service in [*services.items(), *((key, value) for key, value in optional.items() if key != "app")]:
        if name != "app" and not service.get("healthcheck"):
            errors.append("服务缺少健康检查：" + name)
        if service.get("restart") != "unless-stopped":
            errors.append("服务缺少重启策略：" + name)
        if not service.get("mem_limit") or not service.get("cpus"):
            errors.append("服务缺少资源限制：" + name)
    try:
        dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
        ignored = set((root / ".dockerignore").read_text(encoding="utf-8").splitlines())
        if (
            "HEALTHCHECK " not in dockerfile
            or "r.status==200" not in dockerfile
            or "b.get('status')=='ok'" not in dockerfile
        ):
            errors.append("应用镜像缺少严格就绪探针")
        if "ARG INSTALL_MILVUS=true" not in dockerfile or ".[milvus]" not in dockerfile:
            errors.append("镜像必须默认安装已批准的 Milvus extra")
        if not {".env", ".env.*", "data", ".git", "frontend/node_modules"}.issubset(ignored):
            errors.append("构建上下文未排除敏感配置或本地状态")
    except OSError:
        errors.append("镜像或构建排除契约缺失")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    errors = check(parser.parse_args().root)
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        return 1
    print("生产编排静态契约通过；容器启动和就绪尚需独立验收。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
