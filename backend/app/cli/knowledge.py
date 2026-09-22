"""知识库生命周期管理 CLI（Phase 3.5.10）。

> 文档生命周期（list / get / ingest / update / delete）专用入口。

调用现有 `KnowledgeIngestionService` 的生命周期方法，
**不**直接操作 ORM / Parser / Chunker / EmbeddingClient。

用法：

```bash
# 列出所有文档
python -m backend.app.cli.knowledge list

# 查询单个文档
python -m backend.app.cli.knowledge get 1

# 导入新文档（与 Phase 3.5.8 ingest_knowledge 等价语义）
python -m backend.app.cli.knowledge ingest docs/knowledge/wms-basic-operations.md

# 原地更新（document_id 不变；内容变化时重新 Chunk + Embedding）
python -m backend.app.cli.knowledge update 1 docs/knowledge/wms-basic-operations.md

# 删除文档（含 chunks）
python -m backend.app.cli.knowledge delete 1

# 输出 JSON
python -m backend.app.cli.knowledge --json list
```

退出码：
    0   成功
    1   失败（参数错误 / 文件不存在 / 文档不存在 / 服务异常）

不在本阶段实现：
    - HTTP 接口
    - 批量 / 目录递归
    - 任务队列 / 异步 worker
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from backend.app.services.knowledge_ingestion_service import (
    EmptyDocumentError,
    KnowledgeDocumentNotFoundError,
    KnowledgeIngestionDatabaseError,
    KnowledgeIngestionError,
    KnowledgeIngestionService,
)
from backend.app.rag.parsers.base import (
    DocumentNotFoundError,
    DocumentParseError,
    UnsupportedDocumentTypeError,
)

logger = logging.getLogger("knowledge_cli")


# ============================================================
# 输出格式化
# ============================================================

def _iso(dt: datetime | None) -> str:
    return dt.isoformat() if dt is not None else ""


def _doc_to_dict(info) -> dict:
    return {
        "id": info.id,
        "title": info.title,
        "file_name": info.file_name,
        "file_type": info.file_type,
        "source": info.source,
        "status": info.status,
        "created_at": _iso(info.created_at),
        "updated_at": _iso(info.updated_at),
        "chunk_count": info.chunk_count,
    }


def _ingest_to_dict(result) -> dict:
    return {
        "document_id": result.document_id,
        "file_name": result.file_name,
        "status": result.status,
        "chunk_count": result.chunk_count,
        "embedded_chunk_count": result.embedded_chunk_count,
        "content_hash": result.content_hash,
    }


def _format_docs_human(docs: tuple) -> str:
    if not docs:
        return "(no documents)"
    lines = [f"{'ID':<6}{'STATUS':<14}{'CHUNKS':<8}{'FILE':<48}TITLE"]
    for d in docs:
        lines.append(
            f"{d.id:<6}{d.status:<14}{d.chunk_count:<8}"
            f"{(d.file_name or '')[:47]:<48}{d.title}"
        )
    return "\n".join(lines)


# ============================================================
# 子命令实现
# ============================================================

async def _cmd_list(svc: KnowledgeIngestionService, as_json: bool) -> int:
    docs = svc.list_documents()
    payload = {"total": len(docs), "documents": [_doc_to_dict(d) for d in docs]}
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"=== Knowledge Documents ({len(docs)}) ===")
        print(_format_docs_human(docs))
    return 0


async def _cmd_get(svc: KnowledgeIngestionService, doc_id: int, as_json: bool) -> int:
    try:
        info = svc.get_document(doc_id)
    except KnowledgeDocumentNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    payload = _doc_to_dict(info)
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print("=== Knowledge Document ===")
        for k, v in payload.items():
            print(f"{k:<16}: {v}")
    return 0


async def _cmd_ingest(svc: KnowledgeIngestionService, path: Path, as_json: bool) -> int:
    if not path.exists() or not path.is_file():
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        return 1
    try:
        result = await svc.ingest_one(path)
    except (DocumentNotFoundError, UnsupportedDocumentTypeError,
            DocumentParseError, EmptyDocumentError,
            KnowledgeIngestionDatabaseError, KnowledgeIngestionError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    payload = _ingest_to_dict(result)
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print("=== Knowledge Ingest ===")
        for k, v in payload.items():
            print(f"{k:<22}: {v}")
    return 0


async def _cmd_update(
    svc: KnowledgeIngestionService, doc_id: int, path: Path, as_json: bool
) -> int:
    if not path.exists() or not path.is_file():
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        return 1
    try:
        result = await svc.update_document(doc_id, path)
    except KnowledgeDocumentNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except (DocumentNotFoundError, UnsupportedDocumentTypeError,
            DocumentParseError, EmptyDocumentError,
            KnowledgeIngestionDatabaseError, KnowledgeIngestionError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    payload = _ingest_to_dict(result)
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print("=== Knowledge Update ===")
        for k, v in payload.items():
            print(f"{k:<22}: {v}")
    return 0


async def _cmd_delete(
    svc: KnowledgeIngestionService, doc_id: int, as_json: bool
) -> int:
    try:
        chunks_deleted = svc.delete_document(doc_id)
    except KnowledgeDocumentNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KnowledgeIngestionDatabaseError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    payload = {"document_id": doc_id, "status": "deleted", "chunks_deleted": chunks_deleted}
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print("=== Knowledge Delete ===")
        for k, v in payload.items():
            print(f"{k:<16}: {v}")
    return 0


# ============================================================
# argparse
# ============================================================

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="knowledge",
        description=(
            "WMS knowledge base lifecycle CLI "
            "(list / get / ingest / update / delete)."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print results as JSON (default: human-readable).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List all knowledge documents.")

    p_get = sub.add_parser("get", help="Get a single document info by id.")
    p_get.add_argument("doc_id", type=int)

    p_ingest = sub.add_parser("ingest", help="Ingest (create) a knowledge document.")
    p_ingest.add_argument("path", type=str)

    p_upd = sub.add_parser(
        "update",
        help=(
            "In-place update of an existing document by id "
            "(content changed → same document_id, replaced chunks)."
        ),
    )
    p_upd.add_argument("doc_id", type=int)
    p_upd.add_argument("path", type=str)

    p_del = sub.add_parser("delete", help="Delete a document (and its chunks).")
    p_del.add_argument("doc_id", type=int)

    return parser


async def _async_main(args: argparse.Namespace) -> int:
    svc = KnowledgeIngestionService()
    if args.command == "list":
        return await _cmd_list(svc, args.json)
    if args.command == "get":
        return await _cmd_get(svc, args.doc_id, args.json)
    if args.command == "ingest":
        return await _cmd_ingest(svc, Path(args.path), args.json)
    if args.command == "update":
        return await _cmd_update(svc, args.doc_id, Path(args.path), args.json)
    if args.command == "delete":
        return await _cmd_delete(svc, args.doc_id, args.json)
    print(f"ERROR: unknown command: {args.command}", file=sys.stderr)
    return 1


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return asyncio.run(_async_main(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())


__all__ = ["main"]