"""向量索引重建、激活与回退（ADR-0008 / RAG-032）。

换模型不原地改写：登记新索引 → 全量重建 → 校验 → 显式激活 → 旧索引保留为回退。

用法::

    # 1) 准备新索引（不切换，旧索引继续服务）
    python scripts/rebuild_embedding_index.py prepare --model text-embedding-v4 --dim 1024
    # 2) 校验通过后显式激活（旧索引自动转 retired，不删）
    python scripts/rebuild_embedding_index.py activate --index-id <id>
    # 3) 出问题回退（会校验当前模型配置与目标索引身份一致）
    python scripts/rebuild_embedding_index.py rollback --index-id <id>
    # 4) 把身份未知的历史分块登记到当前索引（需 --confirm，且维度抽样必须全对）
    python scripts/rebuild_embedding_index.py adopt --confirm
    # 查看现状
    python scripts/rebuild_embedding_index.py status

硬约束：
- 不重建真实索引以外的东西；**默认只打印计划**，写操作需要显式子命令。
- 回退必须配套模型配置：只改集合名而仍用新模型查旧向量是 ADR-0008 明令禁止的，
  因此 activate / rollback 都会校验 settings 与目标索引身份一致，不一致直接拒绝。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import Session, select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import engine, init_db  # noqa: E402
from app.models.rag import (  # noqa: E402
    Document,
    DocumentChunk,
    DocumentIngestionSnapshot,
    EmbeddingIndex,
    IndexStatus,
)
from app.rag.index_identity import (  # noqa: E402
    EmbeddingIndexIdentity,
    IndexIdentityError,
    current_backend,
    identity_from_provider,
)
from app.rag.index_registry import (  # noqa: E402
    activate_index,
    active_index,
    adopt_legacy_chunks,
    ensure_index,
    legacy_chunk_count,
)


def _identity(model: str | None, dim: int | None, version: str | None) -> EmbeddingIndexIdentity:
    """构造目标索引身份。model/dim 未给时沿用当前配置。

    与运行时共用 :func:`identity_from_provider` 的字段来源（provider 标签、
    deployment、normalization、metric），避免脚本登记的 provider 与运行时不一致。
    """
    return EmbeddingIndexIdentity(
        backend=current_backend(),
        provider=settings.EMBEDDING_PROVIDER.strip().lower(),
        model=model or settings.EMBEDDING_MODEL,
        dim=int(dim or settings.EMBEDDING_DIM),
        index_version=version or settings.EMBEDDING_INDEX_VERSION,
        deployment="",
        normalization=settings.EMBEDDING_NORMALIZATION,
        metric=settings.EMBEDDING_METRIC,
    )


def _current_runtime_identity() -> EmbeddingIndexIdentity:
    """当前真实生效 provider 的完整身份（与运行时写入/检索用的是同一个函数）。"""
    from app.rag.embeddings.factory import get_embedding_provider

    return identity_from_provider(get_embedding_provider())


def _assert_config_matches(index: EmbeddingIndex) -> None:
    """回退/激活前的硬性校验：**完整身份**必须与当前配置一致。

    ADR-0008：回退路径必须具有对应模型能力配置，
    不能只改集合名而仍用新模型查旧向量。只比较 model/dim/version 会让
    provider、部署端点、归一化或度量变了也查不出来，因此这里比较完整 key。
    """
    expected = _current_runtime_identity()
    if expected.key() != index.identity_key:
        raise IndexIdentityError(
            f"当前配置身份 {expected.key()} 与目标索引 {index.name} 的身份 "
            f"{index.identity_key} 不一致。"
            "请先改配置再切换，不要只改索引名——那等于用新模型查旧向量。"
        )


def cmd_status(_args: argparse.Namespace) -> int:
    init_db()
    with Session(engine) as session:
        rows = session.exec(select(EmbeddingIndex)).all()
        active = active_index(session)
        print(f"backend={current_backend()} active={(active.name if active else None)}")
        print(f"legacy_chunks={legacy_chunk_count(session)}")
        for row in rows:
            print(
                f"  {row.status:10s} {row.name}  model={row.model} dim={row.dim} "
                f"v={row.index_version} chunks={row.chunk_count}"
            )
    return 0


def cmd_prepare(args: argparse.Namespace) -> int:
    init_db()
    identity = _identity(args.model, args.dim, args.version)
    with Session(engine) as session:
        index = ensure_index(session, identity)
        if index.status == IndexStatus.ACTIVE.value:
            print(f"目标身份已是生效索引，无需重建：{index.name}")
            return 0
        index.status = IndexStatus.PREPARING.value
        index.notes = "准备中：等待全量重建与校验"
        session.add(index)
        session.commit()
        print(f"已登记新索引 {index.name}（status=preparing）")
        print("旧索引仍为 active，读路径不受影响。")
        print("下一步：用该模型重新摄取全部当前文档，再执行 activate。")
        if args.json:
            print(json.dumps(index.describe() if hasattr(index, "describe") else {}, ensure_ascii=False))
    return 0


def cmd_activate(args: argparse.Namespace) -> int:
    init_db()
    with Session(engine) as session:
        index = session.get(EmbeddingIndex, args.index_id)
        if index is None:
            print(f"索引不存在：{args.index_id}", file=sys.stderr)
            return 2
        _assert_config_matches(index)
        # 校验下沉到领域入口 activate_index：脚本只是调用者，不再自己判一次。
        activate_index(
            session,
            index.id,
            expected_identity=_current_runtime_identity(),
        )
        print(f"已激活 {index.name}；同后端其它 active 索引已转 retired（保留未删）。")
    return 0


def cmd_rollback(args: argparse.Namespace) -> int:
    init_db()
    with Session(engine) as session:
        index = session.get(EmbeddingIndex, args.index_id)
        if index is None:
            print(f"索引不存在：{args.index_id}", file=sys.stderr)
            return 2
        _assert_config_matches(index)
        activate_index(
            session,
            index.id,
            expected_identity=_current_runtime_identity(),
        )
        print(f"已回退到 {index.name}。注意：模型配置需与该索引身份一致，本命令已在激活前校验。")
    return 0


def cmd_adopt(args: argparse.Namespace) -> int:
    """把身份未知的历史分块登记到当前索引。

    必须显式 ``--confirm``，并给出**证据**（声明人/依据/原模型）；维度抽样全部
    匹配且原模型声明与目标索引一致才写，任何一条不满足就整体拒绝。
    """
    init_db()
    with Session(engine) as session:
        index = active_index(session)
        if index is None:
            print("没有生效索引，无法登记。先执行一次摄取或 prepare。", file=sys.stderr)
            return 2
        # 先核对当前配置与目标索引身份：配置已经换了模型就不能把历史数据认成新模型。
        try:
            _assert_config_matches(index)
        except IndexIdentityError as exc:
            print(f"拒绝登记：{exc}", file=sys.stderr)
            return 3
        count = legacy_chunk_count(session)
        runtime = _current_runtime_identity()
        print(f"待登记历史分块：{count}")
        print(f"目标索引：{index.name}")
        print(f"目标索引完整身份：{index.identity_key}")
        print(f"当前配置完整身份：{runtime.key()}")
        print(
            "注意：维度相同不能证明向量空间相同。只有在能举证这些向量确由"
            f"{index.model}（{index.deployment or '默认部署'}）产生时才能登记。"
        )
        if not args.confirm:
            print("这是把历史数据认定与当前模型兼容，必须人工确认后加 --confirm。")
            return 1
        evidence = (args.evidence or "").strip()
        if not evidence:
            print("缺少 --evidence（声明人 / 依据 / 原模型与版本），拒绝登记。", file=sys.stderr)
            return 3
        try:
            adopted = adopt_legacy_chunks(
                session, index, evidence=evidence, declared_model=args.declared_model
            )
        except IndexIdentityError as exc:
            print(f"拒绝登记：{exc}", file=sys.stderr)
            return 3
        print(f"已登记 {adopted} 条历史分块到 {index.name}，证据已写入索引 notes。")
    return 0


def cmd_rebuild(args: argparse.Namespace) -> int:
    """按目标身份全量重建：重新摄取所有 is_current 文档。

    真实重建会调用嵌入接口产生费用，因此必须显式 --apply；
    默认只打印将要处理的文档数。
    """
    init_db()
    from app.rag.document_parsers.base import ParsedBlock, ParsedDocument
    from app.rag.embeddings.factory import get_embedding_provider
    from app.rag.service import RAGService

    provider = get_embedding_provider()
    identity = identity_from_provider(provider)
    if args.model or args.dim:
        identity = _identity(args.model, args.dim, args.version)

    with Session(engine) as session:
        docs = session.exec(select(Document).where(Document.is_current.is_(True))).all()
        print(f"目标身份 {identity.key()}")
        print(f"将重建 {len(docs)} 篇当前文档的向量")
        if not args.apply:
            print("未执行。加 --apply 才会真正调用嵌入接口（会产生费用）。")
            return 0
        index = ensure_index(session, identity)
        index.status = IndexStatus.PREPARING.value
        session.add(index)
        session.commit()

        rebuilt = 0
        failed: list[str] = []
        for doc in docs:
            snapshot = session.get(DocumentIngestionSnapshot, doc.id)
            if snapshot is None:
                failed.append(f"{doc.id}: 无摄取快照，无法回放（需重新上传）")
                continue
            try:
                blocks = [
                    ParsedBlock(**block)
                    for block in json.loads(snapshot.original_blocks or "[]")
                ]
            except (TypeError, ValueError) as exc:
                failed.append(f"{doc.id}: 快照块不可解析 {type(exc).__name__}")
                continue
            parsed = ParsedDocument(
                text=snapshot.original_text,
                title=doc.title,
                source=doc.source or doc.title,
                extension=Path(doc.source or "").suffix or ".txt",
                content_type=None,
                blocks=blocks,
            )
            try:
                # 显式把 preparing 目标传给写入链路：旧 active 继续服务，
                # 新分块写到目标索引名下，失败时旧索引数据完全不变。
                service = RAGService(
                    session,
                    doc.tenant_id,
                    embedding_provider=provider,
                    write_index_id=index.id,
                )
                asyncio.run(
                    service.reindex_document_in_place(
                        doc, parsed, content_hash=doc.content_hash or ""
                    )
                )
                rebuilt += 1
            except Exception as exc:  # noqa: BLE001 — 逐篇记录，不中断整批
                session.rollback()
                failed.append(f"{doc.id}: {type(exc).__name__}")

        chunks = session.exec(
            select(DocumentChunk).where(DocumentChunk.index_id == index.id)
        ).all()
        index.chunk_count = len(chunks)
        index.notes = f"重建完成：成功 {rebuilt} 篇，失败 {len(failed)} 篇"
        if failed:
            index.status = IndexStatus.FAILED.value
            index.notes += f"；失败清单 {failed[:10]}"
        session.add(index)
        session.commit()
        print(f"重建完成：成功 {rebuilt}，失败 {len(failed)}，分块 {index.chunk_count}")
        print(f"索引 {index.name} status={index.status}")
        if not failed:
            print(
                "下一步：python scripts/rebuild_embedding_index.py "
                f"activate --index-id {index.id}"
            )
        else:
            print(f"失败清单：{failed}", file=sys.stderr)
            print("重建未完成，旧索引仍是 active，读路径未受影响。")
            return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="向量索引重建、激活与回退（ADR-0008）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status", help="查看索引登记现状")
    p_status.set_defaults(func=cmd_status)

    p_prepare = sub.add_parser("prepare", help="登记新索引（不切换）")
    p_prepare.add_argument("--model")
    p_prepare.add_argument("--dim", type=int)
    p_prepare.add_argument("--version")
    p_prepare.add_argument("--json", action="store_true")
    p_prepare.set_defaults(func=cmd_prepare)

    p_activate = sub.add_parser("activate", help="显式激活（旧索引转 retired）")
    p_activate.add_argument("--index-id", required=True)
    p_activate.set_defaults(func=cmd_activate)

    p_rollback = sub.add_parser("rollback", help="回退到指定索引")
    p_rollback.add_argument("--index-id", required=True)
    p_rollback.set_defaults(func=cmd_rollback)

    p_adopt = sub.add_parser("adopt", help="把身份未知的历史分块登记到当前索引")
    p_adopt.add_argument("--confirm", action="store_true")
    p_adopt.add_argument("--evidence", default="", help="登记证据：声明人 / 依据 / 原模型与版本")
    p_adopt.add_argument("--declared-model", default=None, dest="declared_model")
    p_adopt.set_defaults(func=cmd_adopt)

    p_rebuild = sub.add_parser("rebuild", help="按目标身份全量重建")
    p_rebuild.add_argument("--model")
    p_rebuild.add_argument("--dim", type=int)
    p_rebuild.add_argument("--version")
    p_rebuild.add_argument("--apply", action="store_true", help="真正执行（调用嵌入接口）")
    p_rebuild.set_defaults(func=cmd_rebuild)

    args = parser.parse_args()
    try:
        return args.func(args)
    except IndexIdentityError as exc:
        print(f"索引身份错误：{exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
