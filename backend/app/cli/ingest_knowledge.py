"""知识文档导入 CLI（Phase 3.5.8）。

> 真实加载第一份 WMS 知识库专用入口。

调用现有 `KnowledgeIngestionService`，**不**直接操作 ORM / Parser /
Chunker / EmbeddingClient。

用法：

```bash
# 导入单个文件
python -m backend.app.cli.ingest_knowledge docs/knowledge/wms-basic-operations.md

# 退出码
#   0   成功（含已存在 / already_exists）
#   1   失败（参数错误 / 文件不存在 / 服务异常）
```

设计原则（AGENTS.md §4 / §13）：

- CLI 不持有业务规则
- CLI 不绕过现有 Service
- CLI 不直接构造 ORM Session
- CLI 不记录 API Key / Authorization header / embedding 向量

不在本阶段实现（后续 Phase）：

- 批量导入 / 目录递归
- 任务队列 / 异步 worker
- HTTP 接口 `POST /api/knowledge/ingest`
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from backend.app.services.knowledge_ingestion_service import (
    EmptyDocumentError,
    KnowledgeIngestionDatabaseError,
    KnowledgeIngestionError,
    KnowledgeIngestionService,
)
from backend.app.rag.parsers.base import (
    DocumentNotFoundError,
    UnsupportedDocumentTypeError,
    DocumentParseError,
)

logger = logging.getLogger("ingest_knowledge")


def _build_arg_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。

    故意保持极简：一个 path 参数。
    """
    parser = argparse.ArgumentParser(
        prog="ingest_knowledge",
        description=(
            "Import a Markdown / TXT knowledge document into the WMS knowledge base. "
            "Calls KnowledgeIngestionService (no direct ORM / embedding access)."
        ),
    )
    parser.add_argument(
        "path",
        type=str,
        help="Path to the knowledge document file (.md / .txt).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the result as a single-line JSON object (default: human-readable).",
    )
    return parser


def _format_human(result_dict: dict) -> str:
    """人类可读格式输出。"""
    lines = [
        "=== Knowledge Ingestion Result ===",
        f"document_id          : {result_dict['document_id']}",
        f"file_name            : {result_dict['file_name']}",
        f"title                : {result_dict['title']}",
        f"file_type            : {result_dict['file_type']}",
        f"chunk_count          : {result_dict['chunk_count']}",
        f"embedded_chunk_count : {result_dict['embedded_chunk_count']}",
        f"content_hash         : {result_dict['content_hash']}",
        f"status               : {result_dict['status']}",
    ]
    return "\n".join(lines)


async def _async_main(args: argparse.Namespace) -> int:
    """异步主流程。"""
    file_path = Path(args.path)
    if not file_path.exists():
        print(f"ERROR: file not found: {file_path}", file=sys.stderr)
        return 1
    if not file_path.is_file():
        print(f"ERROR: not a regular file: {file_path}", file=sys.stderr)
        return 1

    svc = KnowledgeIngestionService()

    try:
        result = await svc.ingest_one(file_path)
    except DocumentNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except UnsupportedDocumentTypeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except DocumentParseError as exc:
        print(f"ERROR: parse error: {exc}", file=sys.stderr)
        return 1
    except EmptyDocumentError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KnowledgeIngestionDatabaseError as exc:
        print(f"ERROR: database error: {exc}", file=sys.stderr)
        return 1
    except KnowledgeIngestionError as exc:
        print(f"ERROR: ingestion error: {exc}", file=sys.stderr)
        return 1

    result_dict = {
        "document_id": result.document_id,
        "file_name": result.file_name,
        "title": file_path.stem or result.file_name,
        "file_type": file_path.suffix.lstrip(".").lower(),
        "chunk_count": result.chunk_count,
        "embedded_chunk_count": result.embedded_chunk_count,
        "content_hash": result.content_hash,
        "status": result.status,
    }

    if args.json:
        print(json.dumps(result_dict, ensure_ascii=False))
    else:
        print(_format_human(result_dict))
    return 0


def main() -> int:
    """同步入口（供 `python -m ...` 调用）。"""
    parser = _build_arg_parser()
    args = parser.parse_args()
    return asyncio.run(_async_main(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())


__all__ = ["main"]