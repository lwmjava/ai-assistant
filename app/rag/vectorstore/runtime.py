"""启动时选择 Milvus 实例，并在进程内固定。

Standalone 与 Lite 不共享数据。选择只在启动发生一次；已经记下的 Lite
不会因为后来的服务恢复而自动换回去。
"""

from __future__ import annotations

import hashlib
import logging
import os
import socket
from collections.abc import Callable
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
from urllib.parse import urlsplit

from sqlmodel import Session

from app.core.config import settings

logger = logging.getLogger(__name__)

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_LOCKED: RuntimeChoice | None = None
_FAILURE: str | None = None


class RuntimeSelectionError(RuntimeError):
    """当前环境不能选择请求的向量实例。"""


@dataclass(frozen=True)
class RuntimeChoice:
    """一次已经确定的实例。"""

    mode: str
    uri: str
    reason: str

    @property
    def instance_id(self) -> str:
        """模式和规范化位置的摘要；不包含凭据或原始连接串。"""
        location = canonical_location(self.mode, self.uri)
        return self.mode + ":" + hashlib.sha256(location.encode("utf-8")).hexdigest()


def canonical_location(mode: str, uri: str) -> str:
    if mode == "lite":
        if not uri.strip() or "://" in uri:
            raise RuntimeSelectionError("Lite 必须配置本机文件路径")
        return os.path.normcase(str(Path(uri).resolve()))
    if mode != "standalone":
        raise RuntimeSelectionError("未知 Milvus 运行模式")
    try:
        parsed = urlsplit(uri.strip())
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
            raise ValueError("invalid server URI")
        host = parsed.hostname.lower()
        host = "127.0.0.1" if host == "localhost" else host
        port = parsed.port or 19530
        return f"{parsed.scheme}://{host}:{port}"
    except ValueError as exc:
        raise RuntimeSelectionError("Milvus 地址无效，请使用无内嵌凭据的服务地址") from exc


@dataclass(frozen=True)
class ProbeReport:
    """启动探测的分类结果，不包含凭据。"""

    kind: str
    detail: str = ""


def uri_allows_lite_fallback(uri: str) -> bool:
    """只有本机回环上的服务地址可以考虑 Lite。文件库和远程主机都不行。"""
    if uri.strip().lower().endswith(".db"):
        return False
    host = (urlsplit(uri).hostname or "").lower()
    return host in _LOCAL_HOSTS


def classify_probe_error(exc: BaseException) -> str:
    """把连接异常分成可兜底与必须暴露的两类。未知错误不兜底。"""
    text = f"{type(exc).__name__} {exc}".lower()
    if type(exc).__name__ in {
        "ConnectionConfigException", "ConnectionNotExistException", "ParamError",
    }:
        return "config"
    if "auth" in text or "unauthor" in text or "credential" in text or "illegal token" in text:
        return "auth"
    if "schema" in text or ("missing" in text and "field" in text):
        return "schema"
    if "invalid" in text or "illegal" in text or "configuration" in text:
        return "config"
    if "timeout" in text or "timed out" in text:
        return "unready"
    if (
        "refused" in text
        or "unavailable" in text
        or "failed to connect" in text
        or isinstance(exc, ConnectionError | TimeoutError)
    ):
        return "unreachable"
    return "config"


def choose_runtime(
    *,
    env: str,
    configured_uri: str,
    lite_uri: str,
    fallback_enabled: bool,
    persisted: RuntimeChoice | None,
    probe: Callable[[str], ProbeReport],
    lite_supported: bool,
) -> RuntimeChoice:
    """按已登记结果或一次探测决定实例。失败时不返回另一种库。"""
    server = canonical_location("standalone", configured_uri)
    development = env.strip().lower() == "development"
    if persisted is not None:
        if persisted.mode == "lite":
            if not (development and fallback_enabled and uri_allows_lite_fallback(configured_uri)):
                raise RuntimeSelectionError("当前配置禁止使用已登记的 Lite，请先受控切换")
            if not lite_supported:
                raise RuntimeSelectionError("已登记 Lite 的运行库不可用")
            if persisted.instance_id != RuntimeChoice("lite", lite_uri, "").instance_id:
                raise RuntimeSelectionError("Lite 文件归属已改变，拒绝自动切换")
            return persisted
        if persisted.mode != "standalone" or canonical_location(persisted.mode, persisted.uri) != server:
            raise RuntimeSelectionError("Milvus 实例归属已改变，拒绝自动切换")

    report = probe(configured_uri)
    if report.kind == "ready":
        return RuntimeChoice("standalone", configured_uri, "standalone_ready")
    if report.kind in {"auth", "schema", "config"}:
        raise RuntimeSelectionError(f"Milvus 拒绝启动兜底：{report.kind}")
    allowed = (
        development
        and fallback_enabled
        and uri_allows_lite_fallback(configured_uri)
        and report.kind in {"unreachable", "unready"}
    )
    if not allowed:
        raise RuntimeSelectionError(f"Milvus 不可用，当前环境禁止 Lite 兜底：{report.kind}")
    if not lite_supported:
        raise RuntimeSelectionError(
            "本机没有可用的 Milvus Lite 运行库。不要改用本地向量库；"
            "请在 Linux 或 WSL 中使用同一版本客户端。"
        )
    if persisted is not None:
        raise RuntimeSelectionError("已登记 Standalone 不可用，禁止自动切换到 Lite")
    canonical_location("lite", lite_uri)
    return RuntimeChoice("lite", str(Path(lite_uri).resolve()), f"startup_fallback:{report.kind}")


def current_runtime() -> RuntimeChoice | None:
    """返回本进程已经固定的实例；还没选择时是 None。"""
    return _LOCKED


def reset_runtime_for_tests() -> None:
    """只给测试清理进程内固定结果。"""
    global _LOCKED, _FAILURE
    _LOCKED = None
    _FAILURE = None


def assert_runtime_available() -> None:
    """启动选择失败后，只有向量能力失败；不逐请求重新选择。"""
    if _FAILURE is not None:
        raise RuntimeSelectionError("Milvus 启动选择不可用，请核对配置并重启服务")


def runtime_failure() -> str | None:
    return _FAILURE


def initialize_milvus_runtime(session: Session) -> None:
    """隔离可预期的向量选择错误，保留数据库等非向量启动门禁。"""
    global _FAILURE
    try:
        ensure_milvus_runtime(session)
    except RuntimeSelectionError:
        session.rollback()
        _FAILURE = "startup_selection_unavailable"
        logger.warning("milvus_runtime_unavailable reason=%s", _FAILURE)


def resolved_uri() -> str | None:
    assert_runtime_available()
    choice = _LOCKED
    return choice.uri if choice is not None else None


def lite_package_installed() -> bool:
    return find_spec("milvus_lite") is not None


def probe_configured_server(uri: str, token: str, timeout: float) -> ProbeReport:
    """对配置的服务做一次短连接。异常只保留分类，不向外带出令牌。"""
    alias = "startup-probe"
    try:
        canonical_location("standalone", uri)
        parsed = urlsplit(uri)
        # SDK把非法参数和服务不可用合并为同一错误，先独立核对TCP不可达。
        with socket.create_connection((parsed.hostname or "", parsed.port or 19530), timeout):
            pass
        from pymilvus import connections, utility

        connections.connect(alias=alias, uri=uri, token=token or None, timeout=timeout)
        try:
            utility.get_server_version(using=alias)
        finally:
            connections.disconnect(alias)
        return ProbeReport("ready")
    except Exception as exc:  # noqa: BLE001 - 启动探测必须把各类连接失败分类
        kind = classify_probe_error(exc)
        logger.warning("milvus_startup_probe kind=%s exception_type=%s", kind, type(exc).__name__)
        return ProbeReport(kind, type(exc).__name__)


def load_persisted_runtime(session: Session) -> RuntimeChoice | None:
    from app.models.rag import VectorRuntimeBinding

    row = session.get(VectorRuntimeBinding, "active")
    if row is None:
        return None
    return RuntimeChoice(row.mode, row.uri, row.reason)


def save_runtime(session: Session, choice: RuntimeChoice) -> None:
    from app.models.rag import VectorRuntimeBinding

    row = session.get(VectorRuntimeBinding, "active")
    if row is None:
        row = VectorRuntimeBinding(id="active", mode=choice.mode, uri=choice.uri, reason=choice.reason)
        session.add(row)
    else:
        row.mode = choice.mode
        row.uri = choice.uri
        row.reason = choice.reason
        session.add(row)
    session.commit()


def storage_for_index(session: Session, index_id: str) -> str | None:
    from app.models.rag import IndexStorageBinding

    row = session.get(IndexStorageBinding, index_id)
    return row.storage_instance if row is not None else None


def remember_index_storage(session: Session, index_id: str, mode: str) -> None:
    """只在索引第一次出现时记录归属，不把已有归属改写成当前进程。"""
    from app.models.rag import IndexStorageBinding

    if session.get(IndexStorageBinding, index_id) is not None:
        return
    session.add(IndexStorageBinding(index_id=index_id, storage_instance=mode))
    session.flush()


def verify_index_storage(session: Session, index_id: str) -> None:
    """所有真实应用读写都核对完整实例；历史模式记录不自动认领。"""
    assert_runtime_available()
    choice = current_runtime()
    if choice is None:
        return  # 未经应用lifespan的离线隔离调用保留原身份契约。
    recorded = storage_for_index(session, index_id)
    if recorded != choice.instance_id:
        from app.rag.index_registry import IndexUnavailableError

        legacy = recorded or "standalone（历史归属未核验）"
        raise IndexUnavailableError(
            f"索引属于 {legacy}，当前进程固定为 {choice.mode} 的另一实例；"
            "拒绝跨实例检索或写入，请先受控核验归属。"
        )


def prepare_lite_runtime(choice: RuntimeChoice, *, persisted: bool) -> None:
    """先确认文件真实可用再登记，丢失的既有文件不能换成空库。"""
    path = Path(choice.uri)
    if persisted and not path.is_file():
        raise RuntimeSelectionError("已登记 Lite 文件不存在，拒绝创建空替身")
    alias = "startup-lite-probe"
    try:
        from pymilvus import connections, utility

        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            connections.connect(alias=alias, uri=str(path), timeout=settings.MILVUS_STARTUP_PROBE_TIMEOUT_SECONDS)
            utility.list_collections(using=alias, timeout=settings.MILVUS_STARTUP_PROBE_TIMEOUT_SECONDS)
        finally:
            connections.disconnect(alias)
    except Exception as exc:  # noqa: BLE001 - 文件/SDK错误不包含凭据
        raise RuntimeSelectionError("Lite 文件初始化不可用，请核对目录和运行库") from exc


def ensure_milvus_runtime(session: Session) -> RuntimeChoice | None:
    """在 Milvus 模式下固定本进程实例。local 模式什么都不做。"""
    global _LOCKED
    from app.rag.index_identity import current_backend

    try:
        backend = current_backend()
    except ValueError as exc:
        raise RuntimeSelectionError("未知向量库配置，拒绝假定为local") from exc
    if backend == "local":
        return None
    assert_runtime_available()
    if _LOCKED is not None:
        return _LOCKED
    persisted = load_persisted_runtime(session)
    choice = choose_runtime(
        env=settings.ENV,
        configured_uri=settings.MILVUS_URI,
        lite_uri=settings.MILVUS_LITE_PATH,
        fallback_enabled=settings.MILVUS_LITE_FALLBACK,
        persisted=persisted,
        probe=lambda uri: probe_configured_server(
            uri, settings.MILVUS_TOKEN, settings.MILVUS_STARTUP_PROBE_TIMEOUT_SECONDS
        ),
        lite_supported=lite_package_installed(),
    )
    if choice.mode == "lite":
        prepare_lite_runtime(choice, persisted=persisted is not None)
    save_runtime(session, choice)
    _LOCKED = choice
    logger.info("milvus_runtime_selected mode=%s reason=%s", choice.mode, choice.reason)
    return choice
