"""Independent acceptance probes for the recorded VDB issues. Does not change product code."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import traceback
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[3]
RESULTS: list[dict] = []


def record(number: int, verdict: str, detail: str) -> None:
    RESULTS.append({"issue": number, "verdict": verdict, "detail": detail})
    print(f"ISSUE {number}\t{verdict}\t{detail}", flush=True)


def fail_detail(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def health_only() -> None:
    """Load settings only after the parent process has set the isolated environment."""
    import runpy

    runpy.run_path("tests/conftest.py")
    from sqlmodel import Session

    from app.api.routes.health import health_check
    from app.core.database import engine
    from app.rag.vectorstore import runtime

    runtime.reset_runtime_for_tests()
    response = health_check()
    body = json.loads(response.body)
    vector = body["checks"]["vector_store"]
    with Session(engine) as session:
        runtime.initialize_milvus_runtime(session)
        binding = runtime.load_persisted_runtime(session)
    again = health_check()
    again_body = json.loads(again.body)
    again_vector = again_body["checks"]["vector_store"]
    print("HEALTH_JSON " + json.dumps({
        "before_init": {
            "http": response.status_code,
            "status": body.get("status"),
            "backend": vector.get("backend"),
            "vector_status": vector.get("status"),
            "runtime": vector.get("runtime"),
            "fallback_reason": vector.get("fallback_reason"),
        },
        "after_init": {
            "http": again.status_code,
            "status": again_body.get("status"),
            "backend": again_vector.get("backend"),
            "vector_status": again_vector.get("status"),
            "runtime": again_vector.get("runtime"),
            "fallback_reason": again_vector.get("fallback_reason"),
            "failure": runtime.runtime_failure(),
            "locked": runtime.current_runtime() is not None,
            "binding": None if binding is None else binding.mode,
        },
    }, ensure_ascii=False))


def memory_session():
    from sqlmodel import Session, SQLModel, create_engine

    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def issue_startup(runtime, settings) -> None:
    from fastapi.testclient import TestClient
    from sqlmodel import Session

    from app.core.database import engine
    from app.main import app

    runtime.reset_runtime_for_tests()
    probe = Mock(return_value=runtime.ProbeReport("unreachable"))
    scheduler_patches = [
        patch(f"{module}.{function}", new=Mock())
        for module, function in [
            ("app.workflow.scheduler", "start_scheduler"),
            ("app.workflow.scheduler", "stop_scheduler"),
            ("app.rag.import_scheduler", "start_scheduler"),
            ("app.rag.import_scheduler", "stop_scheduler"),
            ("app.evolution.scheduler", "start_scheduler"),
            ("app.evolution.scheduler", "stop_scheduler"),
            ("app.services.agent_state_cleanup", "start_cleanup"),
            ("app.services.agent_state_cleanup", "stop_cleanup"),
        ]
    ]
    # These four are awaited. Replace them with completed coroutines.
    async def _idle():
        return None

    for item in scheduler_patches:
        item.new = _idle
    with ExitStack() as stack:
        stack.enter_context(patch.object(settings, "RAG_VECTOR_STORE", "milvus"))
        stack.enter_context(patch.object(settings, "ENV", "development"))
        stack.enter_context(patch.object(settings, "MILVUS_URI", "http://127.0.0.1:19530"))
        stack.enter_context(patch.object(settings, "MILVUS_LITE_FALLBACK", True))
        stack.enter_context(patch.object(runtime, "probe_configured_server", probe))
        stack.enter_context(patch.object(runtime, "lite_package_installed", lambda: False))
        for item in scheduler_patches:
            stack.enter_context(item)
        with TestClient(app) as client:
            root = client.get("/")
            auth = client.get("/api/v1/auth/me")
            health = client.get("/api/v1/health")
        body = health.json()
        vector = body["checks"]["vector_store"]
        with Session(engine) as session:
            binding = runtime.load_persisted_runtime(session)
        resolved_blocked = False
        try:
            runtime.resolved_uri()
        except runtime.RuntimeSelectionError:
            resolved_blocked = True
        ok = (
            root.status_code == 200
            and auth.status_code == 401
            and health.status_code == 503
            and body.get("status") != "ok"
            and vector.get("backend") != "local"
            and vector.get("status") != "ok"
            and vector.get("runtime") == "unavailable"
            and runtime.current_runtime() is None
            and binding is None
            and resolved_blocked
            and probe.call_count == 1
        )
        record(1, "通过" if ok else "失败", json.dumps({
            "root": root.status_code,
            "auth": auth.status_code,
            "health": health.status_code,
            "status": body.get("status"),
            "backend": vector.get("backend"),
            "vector_status": vector.get("status"),
            "runtime": vector.get("runtime"),
            "fallback_reason": vector.get("fallback_reason"),
            "locked": runtime.current_runtime() is not None,
            "binding": None if binding is None else binding.mode,
            "resolved_blocked": resolved_blocked,
            "probe_calls": probe.call_count,
        }, ensure_ascii=False))
    runtime.reset_runtime_for_tests()


def issue_persisted_lite(runtime) -> None:
    saved = runtime.RuntimeChoice("lite", "data/synthetic-lite.db", "startup_fallback:unreachable")
    cases = {
        "fallback_closed": {"fallback_enabled": False},
        "remote_uri": {"configured_uri": "http://synthetic.invalid:19530"},
        "lite_missing": {"lite_supported": False},
    }
    observed = {}
    ok = True
    for name, changes in cases.items():
        probe = Mock(return_value=runtime.ProbeReport("ready"))
        params = dict(
            env="development", configured_uri="http://127.0.0.1:19530",
            lite_uri="data/synthetic-lite.db", fallback_enabled=True,
            persisted=saved, probe=probe, lite_supported=True,
        )
        params.update(changes)
        try:
            choice = runtime.choose_runtime(**params)
            observed[name] = f"selected {choice.mode}"
            ok = False
        except runtime.RuntimeSelectionError as exc:
            observed[name] = f"rejected {exc}"
        if probe.called:
            observed[name] += " probe_called"
            ok = False
    record(2, "通过" if ok else "失败", json.dumps(observed, ensure_ascii=False))


def issue_same_mode(runtime) -> None:
    from app.rag.index_identity import EmbeddingIndexIdentity
    from app.rag.index_registry import IndexUnavailableError, ensure_index, resolve_read_index_for
    from app.rag.vectorstore.milvus import MilvusVectorStore

    observed = {}
    ok = True
    pairs = [
        ("standalone", "http://127.0.0.1:19530", "http://synthetic.invalid:19530"),
        ("lite", "data/first.db", "data/second.db"),
    ]
    with memory_session() as session:
        for mode, first, second in pairs:
            runtime.reset_runtime_for_tests()
            identity = EmbeddingIndexIdentity("milvus", "synthetic", f"issue3-{mode}", 2)
            runtime._LOCKED = runtime.RuntimeChoice(mode, first, "ready")
            index = ensure_index(session, identity)
            index.status = "active"
            session.add(index)
            session.commit()
            index_id = index.id
            runtime._LOCKED = runtime.RuntimeChoice(mode, second, "ready")
            read_rejected = False
            write_rejected = False
            try:
                resolve_read_index_for(session, identity, backend="milvus")
            except IndexUnavailableError:
                read_rejected = True
            try:
                ensure_index(session, identity)
            except IndexUnavailableError:
                write_rejected = True
            store = MilvusVectorStore(session)
            add_rejected = False
            try:
                store.validate_add([], identity, target_index_id=index_id)
            except IndexUnavailableError:
                add_rejected = True
            observed[mode] = {
                "read_rejected": read_rejected,
                "write_rejected": write_rejected,
                "add_rejected": add_rejected,
            }
            ok = ok and read_rejected and write_rejected and add_rejected
        runtime.reset_runtime_for_tests()
        try:
            runtime.choose_runtime(
                env="development", configured_uri="http://synthetic.invalid:19530",
                lite_uri="data/synthetic-lite.db", fallback_enabled=True,
                persisted=runtime.RuntimeChoice("standalone", "http://127.0.0.1:19530", "ready"),
                probe=lambda _: runtime.ProbeReport("ready"), lite_supported=True,
            )
            observed["persisted_endpoint"] = "selected"
            ok = False
        except runtime.RuntimeSelectionError as exc:
            observed["persisted_endpoint"] = f"rejected {exc}"
    record(3, "通过" if ok else "失败", json.dumps(observed, ensure_ascii=False))


def issue_activation_and_adoption(runtime) -> None:
    from app.rag.index_identity import EmbeddingIndexIdentity
    from app.rag.index_registry import (
        IndexUnavailableError,
        activate_index,
        active_index,
        adopt_legacy_chunks,
        ensure_index,
        resolve_write_index,
    )

    observed = {}
    ok = True
    with memory_session() as session:
        runtime.reset_runtime_for_tests()
        first = EmbeddingIndexIdentity("milvus", "synthetic", "active-boundary", 2)
        second = EmbeddingIndexIdentity("milvus", "synthetic", "target-boundary", 2)
        runtime._LOCKED = runtime.RuntimeChoice("standalone", "http://127.0.0.1:19530", "ready")
        old = ensure_index(session, first)
        old.status = "active"
        old.notes = "original-active-notes"
        session.add(old)
        session.commit()
        old_notes = old.notes
        runtime._LOCKED = runtime.RuntimeChoice("standalone", "http://synthetic.invalid:19530", "ready")
        target = ensure_index(session, second)
        target.notes = "original-target-notes"
        session.add(target)
        session.commit()
        target_notes = target.notes
        target_id = target.id
        write_rejected = False
        try:
            resolve_write_index(session, first)
        except IndexUnavailableError:
            write_rejected = True
        runtime._LOCKED = runtime.RuntimeChoice("standalone", "http://127.0.0.1:19530", "ready")
        activate_rejected = False
        try:
            activate_index(session, target_id, allow_empty=True)
        except IndexUnavailableError:
            activate_rejected = True
        session.refresh(old)
        session.refresh(target)
        current = active_index(session, "milvus")
        observed["activate"] = {
            "rejected": activate_rejected,
            "active_id_unchanged": current is not None and current.id == old.id,
            "active_status": None if current is None else current.status,
            "target_status": target.status,
            "active_notes_unchanged": old.notes == old_notes,
            "target_notes_unchanged": target.notes == target_notes,
        }
        ok = ok and all(observed["activate"][key] for key in (
            "rejected", "active_id_unchanged", "active_notes_unchanged", "target_notes_unchanged",
        )) and target.status == "preparing" and current is not None and current.status == "active"
        observed["resolve_write"] = {"rejected": write_rejected}
        ok = ok and write_rejected

        runtime.reset_runtime_for_tests()
        identity = EmbeddingIndexIdentity("milvus", "synthetic", "adopt-boundary", 2)
        runtime._LOCKED = runtime.RuntimeChoice("lite", "data/first.db", "ready")
        adopted = ensure_index(session, identity)
        adopted.notes = "original-adopt-notes"
        session.add(adopted)
        session.commit()
        notes = adopted.notes
        runtime._LOCKED = runtime.RuntimeChoice("lite", "data/second.db", "ready")
        adopt_rejected = False
        try:
            adopt_legacy_chunks(session, adopted, evidence="synthetic-acceptance-only")
        except IndexUnavailableError:
            adopt_rejected = True
        session.refresh(adopted)
        observed["adopt"] = {"rejected": adopt_rejected, "notes_unchanged": adopted.notes == notes}
        ok = ok and adopt_rejected and adopted.notes == notes
    runtime.reset_runtime_for_tests()
    record(4, "通过" if observed["activate"]["rejected"] and observed["activate"]["active_id_unchanged"]
           and observed["activate"]["active_notes_unchanged"] and observed["activate"]["target_notes_unchanged"]
           and observed["activate"]["target_status"] == "preparing" else "失败",
           json.dumps(observed["activate"], ensure_ascii=False))
    record(5, "通过" if observed["resolve_write"]["rejected"] else "失败",
           json.dumps(observed["resolve_write"], ensure_ascii=False))
    record(6, "通过" if observed["adopt"]["rejected"] and observed["adopt"]["notes_unchanged"] else "失败",
           json.dumps(observed["adopt"], ensure_ascii=False))


def issue_sdk_config(runtime) -> None:
    from pymilvus.exceptions import ConnectionConfigException

    error = ConnectionConfigException(message="invalid connection configuration")
    kind = runtime.classify_probe_error(error)
    selected = None
    try:
        choice = runtime.choose_runtime(
            env="development", configured_uri="http://127.0.0.1:19530",
            lite_uri="data/synthetic-lite.db", fallback_enabled=True, persisted=None,
            probe=lambda _: runtime.ProbeReport(kind), lite_supported=True,
        )
        selected = choice.mode
    except runtime.RuntimeSelectionError as exc:
        selected = f"rejected {exc}"
    ok = kind == "config" and str(selected).startswith("rejected")
    record(7, "通过" if ok else "失败", json.dumps({
        "classification": kind, "selection": selected,
    }, ensure_ascii=False))


def issue_lite_boundaries(runtime, settings) -> None:
    import builtins
    from pathlib import Path as FilePath

    from fastapi.testclient import TestClient
    from pymilvus import connections
    from sqlmodel import Session

    from app.core.database import engine
    from app.main import app

    observed = {}
    ok = True
    base = FilePath("data/t/vdb_issue_acceptance_20261021")
    base.mkdir(parents=True, exist_ok=True)

    def run_init(label: str, disconnect_error, missing_sdk: bool) -> None:
        nonlocal ok
        runtime.reset_runtime_for_tests()
        original = builtins.__import__

        def import_module(name, *args, **kwargs):
            if missing_sdk and name == "pymilvus":
                raise ImportError("synthetic SDK missing")
            return original(name, *args, **kwargs)

        with memory_session() as session, \
                patch.object(settings, "RAG_VECTOR_STORE", "milvus"), \
                patch.object(settings, "ENV", "development"), \
                patch.object(settings, "MILVUS_URI", "http://127.0.0.1:19530"), \
                patch.object(settings, "MILVUS_LITE_FALLBACK", True), \
                patch.object(settings, "MILVUS_LITE_PATH", str(base / f"{label}.db")), \
                patch.object(runtime, "probe_configured_server", return_value=runtime.ProbeReport("unreachable")), \
                patch.object(runtime, "lite_package_installed", return_value=True), \
                patch.object(connections, "connect", side_effect=OSError("synthetic open failure")), \
                patch.object(connections, "disconnect", side_effect=disconnect_error), \
                patch("builtins.__import__", side_effect=import_module):
            try:
                runtime.initialize_milvus_runtime(session)
                binding = runtime.load_persisted_runtime(session)
                escaped = None
            except Exception as exc:  # noqa: BLE001 - acceptance records an escaped failure
                escaped = type(exc).__name__
                binding = runtime.load_persisted_runtime(session)
        item = {
            "escaped": escaped,
            "failure": runtime.runtime_failure(),
            "locked": runtime.current_runtime() is not None,
            "binding": None if binding is None else binding.mode,
        }
        observed[label] = item
        isolated = (
            escaped is None
            and item["failure"] == "startup_selection_unavailable"
            and not item["locked"]
            and binding is None
        )
        if not isolated:
            ok = False

    run_init("disconnect_ok", None, False)
    run_init("disconnect_error", RuntimeError("synthetic disconnect failure"), False)
    run_init("missing_sdk", None, True)

    missing = base / "lost-persisted.db"
    if missing.exists():
        observed["lost_file"] = "preexisting file; refusal not executed"
        ok = False
    else:
        with patch.object(connections, "connect") as connect:
            try:
                runtime.prepare_lite_runtime(
                    runtime.RuntimeChoice("lite", str(missing), ""), persisted=True,
                )
                observed["lost_file"] = "accepted"
                ok = False
            except runtime.RuntimeSelectionError as exc:
                created = missing.exists()
                called = connect.called
                observed["lost_file"] = {
                    "rejected": True,
                    "detail": str(exc),
                    "connect_called": called,
                    "file_created": created,
                }
                if called or created:
                    ok = False

    runtime.reset_runtime_for_tests()
    probe = Mock(return_value=runtime.ProbeReport("unreachable"))

    async def _idle():
        return None

    scheduler_patches = [
        patch(target, new=_idle)
        for target in (
            "app.workflow.scheduler.start_scheduler",
            "app.workflow.scheduler.stop_scheduler",
            "app.rag.import_scheduler.start_scheduler",
            "app.rag.import_scheduler.stop_scheduler",
            "app.evolution.scheduler.start_scheduler",
            "app.evolution.scheduler.stop_scheduler",
            "app.services.agent_state_cleanup.start_cleanup",
            "app.services.agent_state_cleanup.stop_cleanup",
        )
    ]
    with ExitStack() as stack:
        stack.enter_context(patch.object(settings, "RAG_VECTOR_STORE", "milvus"))
        stack.enter_context(patch.object(settings, "ENV", "development"))
        stack.enter_context(patch.object(settings, "MILVUS_URI", "http://127.0.0.1:19530"))
        stack.enter_context(patch.object(settings, "MILVUS_LITE_FALLBACK", True))
        stack.enter_context(patch.object(settings, "MILVUS_LITE_PATH", str(base / "lifespan-disconnect.db")))
        stack.enter_context(patch.object(runtime, "probe_configured_server", probe))
        stack.enter_context(patch.object(runtime, "lite_package_installed", lambda: True))
        stack.enter_context(patch.object(connections, "connect", side_effect=OSError("synthetic open failure")))
        stack.enter_context(patch.object(
            connections, "disconnect", side_effect=RuntimeError("synthetic disconnect failure"),
        ))
        for item in scheduler_patches:
            stack.enter_context(item)
        try:
            with TestClient(app) as client:
                root = client.get("/")
                health = client.get("/api/v1/health")
            lifespan_escaped = None
        except Exception as exc:  # noqa: BLE001
            root = None
            health = None
            lifespan_escaped = type(exc).__name__ + ": " + str(exc)
    with Session(engine) as session:
        binding = runtime.load_persisted_runtime(session)
    if lifespan_escaped is None and health is not None and root is not None:
        body = health.json()
        vector = body["checks"]["vector_store"]
        observed["lifespan_disconnect"] = {
            "root": root.status_code,
            "health": health.status_code,
            "backend": vector.get("backend"),
            "runtime": vector.get("runtime"),
            "vector_status": vector.get("status"),
            "binding": None if binding is None else binding.mode,
        }
        if not (root.status_code == 200 and health.status_code == 503
                and vector.get("backend") != "local" and vector.get("runtime") == "unavailable"
                and binding is None):
            ok = False
    else:
        observed["lifespan_disconnect"] = lifespan_escaped
        ok = False
    runtime.reset_runtime_for_tests()
    record(8, "通过" if ok else "失败", json.dumps(observed, ensure_ascii=False))


def issue_empty_search(runtime) -> None:
    from app.rag.index_identity import EmbeddingIndexIdentity
    from app.rag.index_registry import IndexUnavailableError, ensure_index
    from app.rag.vectorstore.milvus import MilvusVectorStore

    observed = {}
    ok = True
    with memory_session() as session:
        runtime.reset_runtime_for_tests()
        runtime._LOCKED = runtime.RuntimeChoice("standalone", "http://127.0.0.1:19530", "ready")
        store = MilvusVectorStore(session)
        with patch.object(store, "_connect", side_effect=AssertionError("empty library must not connect")):
            found = asyncio.run(store.hybrid_search([1.0, 0.0], ["synthetic"], "tenant", 5))
        observed["empty"] = found
        if found != []:
            ok = False
        identity = EmbeddingIndexIdentity("milvus", "synthetic", "issue9", 2)
        index = ensure_index(session, identity)
        index.status = "active"
        session.add(index)
        session.commit()
        runtime._LOCKED = runtime.RuntimeChoice("standalone", "http://127.0.0.1:19531", "ready")
        store = MilvusVectorStore(session)
        try:
            swallowed = asyncio.run(store.hybrid_search([1.0, 0.0], ["synthetic"], "tenant", 5))
            observed["wrong_instance"] = swallowed
            ok = False
        except IndexUnavailableError as exc:
            observed["wrong_instance"] = f"rejected {exc}"
    runtime.reset_runtime_for_tests()
    record(9, "通过" if ok else "失败", json.dumps(observed, ensure_ascii=False))


def issue_unknown_health() -> None:
    env = os.environ.copy()
    env.update({
        "DATABASE_URL": "sqlite:///./data/vdb-issue-acceptance-20261021.db",
        "RAG_VECTOR_STORE": "unsupported-synthetic",
        "ENV": "development",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "LLM_API_KEY": "",
        "LLM_INTENT_API_KEY": "",
        "LLM_FALLBACK_API_KEY": "",
        "LLM_RERANK_API_KEY": "",
        "EMBEDDING_API_KEY": "",
        "INITIAL_ADMIN_USERNAME": "",
        "INITIAL_ADMIN_PASSWORD": "",
        "INITIAL_SYSTEM_PASSWORD": "",
        "INITIAL_CULTURE_PASSWORD": "",
    })
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--health-only"],
        cwd=str(ROOT), env=env, capture_output=True, text=True, encoding="utf-8",
    )
    payload = None
    for line in reversed(completed.stdout.splitlines()):
        if line.startswith("HEALTH_JSON "):
            payload = json.loads(line.removeprefix("HEALTH_JSON "))
            break
    forbidden = False
    if payload is not None:
        for item in (payload["before_init"], payload["after_init"]):
            if item.get("http") == 200 and item.get("backend") == "local" and item.get("status") == "ok":
                forbidden = True
            if item.get("backend") == "local":
                forbidden = True
    ok = (
        completed.returncode == 0 and payload is not None and not forbidden
        and payload["before_init"]["http"] == 503
        and payload["before_init"]["backend"] == "unknown"
        and payload["before_init"]["vector_status"] == "error"
        and payload["before_init"]["status"] == "error"
        and payload["after_init"]["http"] == 503
        and payload["after_init"]["backend"] == "unknown"
        and payload["after_init"]["binding"] is None
        and payload["after_init"]["locked"] is False
    )
    record(10, "通过" if ok else "失败", json.dumps({
        "exit": completed.returncode,
        "payload": payload,
        "stderr_tail": completed.stderr[-1500:],
    }, ensure_ascii=False))


def compare_vector_schema() -> None:
    import runpy
    runpy.run_path("tests/conftest.py")
    from app.main import app

    exported = json.loads((ROOT / "docs" / "swagger.json").read_text(encoding="utf-8"))
    runtime_schema = app.openapi()["components"]["schemas"]["VectorProbeStatus"]
    file_schema = exported["components"]["schemas"]["VectorProbeStatus"]
    print("SWAGGER_COMPARE\t" + json.dumps({
        "equal": runtime_schema == file_schema,
        "runtime_properties": sorted(runtime_schema.get("properties", {})),
        "file_properties": sorted(file_schema.get("properties", {})),
        "wrote_swagger": False,
    }, ensure_ascii=False), flush=True)


def main() -> int:
    os.chdir(ROOT)
    os.environ["DATABASE_URL"] = "sqlite:///./data/vdb-issue-acceptance-20261021.db"
    os.environ["ENV"] = "development"
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    for key in (
        "LLM_API_KEY", "LLM_INTENT_API_KEY", "LLM_FALLBACK_API_KEY",
        "LLM_RERANK_API_KEY", "EMBEDDING_API_KEY",
        "INITIAL_ADMIN_USERNAME", "INITIAL_ADMIN_PASSWORD",
        "INITIAL_SYSTEM_PASSWORD", "INITIAL_CULTURE_PASSWORD",
    ):
        os.environ[key] = ""
    import runpy
    runpy.run_path("tests/conftest.py")
    from importlib.util import find_spec

    import pymilvus

    from app.core.config import settings
    from app.rag.vectorstore import runtime

    print("ENV", sys.version.split()[0], "pymilvus", pymilvus.__version__,
          "milvus_lite", find_spec("milvus_lite") is not None, flush=True)
    steps = [
        lambda: issue_startup(runtime, settings),
        lambda: issue_persisted_lite(runtime),
        lambda: issue_same_mode(runtime),
        lambda: issue_activation_and_adoption(runtime),
        lambda: issue_sdk_config(runtime),
        lambda: issue_lite_boundaries(runtime, settings),
        lambda: issue_empty_search(runtime),
        issue_unknown_health,
        compare_vector_schema,
    ]
    for step in steps:
        try:
            step()
        except Exception as exc:  # noqa: BLE001 - one probe must not hide the remaining issues
            print("STEP_ERROR", step.__name__ if hasattr(step, "__name__") else "lambda",
                  fail_detail(exc), flush=True)
            traceback.print_exc()
    print("PROBE_DONE", json.dumps(RESULTS, ensure_ascii=False), flush=True)
    failed = [item for item in RESULTS if item["verdict"] != "通过"]
    return 1 if failed or len(RESULTS) < 10 else 0


if __name__ == "__main__":
    if "--health-only" in sys.argv:
        health_only()
    else:
        raise SystemExit(main())
