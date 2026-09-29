"""数据库初始化（Phase 3.2）。

职责：
    - 启用 pgvector extension（幂等）
    - 通过 `Base.metadata.create_all()` 创建所有已注册 ORM Model 对应的表（幂等）

为什么暂不引入 Alembic：
    - 当前阶段表结构极少（knowledge_document / knowledge_chunk + vector extension）
    - 引入 Alembic 会增加迁移文件 / 配置 / 版本管理复杂度
    - 后续 Phase 真正需要 schema 演进（如加 vector index、加多租户字段）时再引入；
      已在本文件保留扩展点（`init_db()` 函数），便于替换为 Alembic 入口。

执行方式：
    # 1. Python API
    from backend.app.db.init_db import init_db
    init_db()

    # 2. 命令行（推荐）
    python -m backend.app.db.init_db
"""
from __future__ import annotations

import logging
import sys
from typing import Any, Final

from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from backend.app.db.base import Base
from backend.app.db.models.llm_usage_record import (
    LLM_USAGE_ASSISTANT_REQUEST_ID_INDEX,
    LLM_USAGE_REQUEST_ID_INDEX,
    LLM_USAGE_REQUEST_ID_PREDICATE,
    LLM_USAGE_SCHEMA,
)
from backend.app.db.session import get_engine

logger = logging.getLogger(__name__)

# 幂等 SQL：重复执行不会报错。
_ENABLE_PGVECTOR_SQL: Final[str] = "CREATE EXTENSION IF NOT EXISTS vector"
# Phase 3.10.14：AI 内部运维 schema（与业务 schema public 隔离，
# 避免 llm_usage_record 被 Text-to-SQL 的 schema 发现当作业务表）。
_ENABLE_AI_OPS_SCHEMA_SQL: Final[str] = (
    f"CREATE SCHEMA IF NOT EXISTS {LLM_USAGE_SCHEMA}"
)
# Phase 3.10.16：request_id 幂等唯一 partial index。
#
# 为什么需要显式 DDL（而不是只靠 Model 的 __table_args__）：
# `Base.metadata.create_all(checkfirst=True)` 对**已存在**的表整体跳过，
# 不会补建后来新增的索引。因此已存在的库必须通过本 DDL 显式补齐，
# 同时对全新库仍然幂等（`IF NOT EXISTS`）。
_USAGE_TABLE: Final[str] = f"{LLM_USAGE_SCHEMA}.llm_usage_record"
_CREATE_REQUEST_ID_INDEX_SQL: Final[str] = (
    f"CREATE UNIQUE INDEX IF NOT EXISTS {LLM_USAGE_REQUEST_ID_INDEX} "
    f"ON {_USAGE_TABLE} (request_id) "
    f"WHERE {LLM_USAGE_REQUEST_ID_PREDICATE}"
)
_DUPLICATE_REQUEST_ID_SQL: Final[str] = (
    "SELECT request_id, COUNT(*) AS cnt FROM "
    f"{_USAGE_TABLE} "
    f"WHERE {LLM_USAGE_REQUEST_ID_PREDICATE} "
    "GROUP BY request_id HAVING COUNT(*) > 1 "
    "ORDER BY request_id"
)


def ensure_request_id_idempotency_index(conn: Any) -> bool:
    """确保 `ai_ops.llm_usage_record.request_id` 的幂等唯一索引存在
    （Phase 3.10.16）。

    幂等行为：索引已存在 → 什么都不做（`IF NOT EXISTS`）。

    已有库保护（§二十一 —— 绝不自动清理数据）：

        若检测到已存在重复 request_id
            → 明确报告（重复的 request_id / 条数）
            → **停止**（raise RuntimeError）
            → 不 DELETE / 不 MERGE / 不 UPDATE（由人决定如何处理）

    Args:
        conn: 已开启事务的 SQLAlchemy Connection。

    Returns:
        True:  索引已存在或本次成功创建。
        False: 表还不存在（由 `Base.metadata.create_all()` 随表创建）。

    Raises:
        RuntimeError: 检测到重复 request_id 数据（需要人工处理）。
    """
    if not inspect(conn).has_table("llm_usage_record", schema=LLM_USAGE_SCHEMA):
        # 表还不存在：create_all 会连同 Model 里的 partial index 一起创建
        return False

    duplicates = list(conn.execute(text(_DUPLICATE_REQUEST_ID_SQL)).all())
    if duplicates:
        detail = ", ".join(
            f"{request_id}={count} 条" for request_id, count in duplicates[:10]
        )
        total = len(duplicates)
        raise RuntimeError(
            "无法创建 request_id 幂等唯一索引："
            f"ai_ops.llm_usage_record 中已存在 {total} 组重复 request_id"
            f"（示例：{detail}）。"
            "本阶段不会自动删除 / 合并 / 更新任何数据——"
            "请先人工确认并处理重复记录后重试。"
        )

    conn.execute(text(_CREATE_REQUEST_ID_INDEX_SQL))
    logger.info(
        "LLM usage request_id idempotency index ensured",
        extra={"index": LLM_USAGE_REQUEST_ID_INDEX},
    )
    return True


# Phase 3.12 Step 36：Assistant Trace 关联列（assistant_request_id）。
#
# 与 request_id 索引同理：`Base.metadata.create_all(checkfirst=True)` 对
# **已存在**的表整体跳过，不会补建后来新增的列 → 必须有显式幂等 DDL，
# 对全新库同样幂等（`IF NOT EXISTS`）。
# nullable：旧链路（/api/chat · /api/rag/answer · /api/chat/with-tools）
# 与历史数据没有该关联，必须保持 NULL。
_CREATE_ASSISTANT_REQUEST_ID_COLUMN_SQL: Final[str] = (
    f"ALTER TABLE {_USAGE_TABLE} "
    "ADD COLUMN IF NOT EXISTS assistant_request_id VARCHAR(128)"
)


def ensure_assistant_request_id_column(conn: Any) -> bool:
    """确保 `ai_ops.llm_usage_record.assistant_request_id` 列存在
    （Phase 3.12 Step 36）。

    幂等行为：列已存在 → 什么都不做（`ADD COLUMN IF NOT EXISTS`）；
    不修改任何已有数据（历史行保持 NULL），不新建表、不引入 Alembic。

    Args:
        conn: 已开启事务的 SQLAlchemy Connection。

    Returns:
        True:  列已存在或本次成功新增。
        False: 表还不存在（由 `Base.metadata.create_all()` 随表创建）。
    """
    if not inspect(conn).has_table("llm_usage_record", schema=LLM_USAGE_SCHEMA):
        return False

    conn.execute(text(_CREATE_ASSISTANT_REQUEST_ID_COLUMN_SQL))
    logger.info("LLM usage assistant_request_id column ensured")
    return True


_CREATE_ASSISTANT_REQUEST_ID_INDEX_SQL: Final[str] = (
    f"CREATE INDEX IF NOT EXISTS {LLM_USAGE_ASSISTANT_REQUEST_ID_INDEX} "
    f"ON {_USAGE_TABLE} (assistant_request_id)"
)


def ensure_assistant_request_id_index(conn: Any) -> bool:
    """确保 `ai_ops.llm_usage_record.assistant_request_id` 的索引存在
    （Phase 3.12 Step 52）。

    用途：Assistant Trace 的 LLM Usage 读边界按
    ``WHERE assistant_request_id = ?`` 精确匹配（每次 Trace 请求一次），
    该列此前没有专用索引（Step 51 审计结论）。

    幂等行为：索引已存在 → 什么都不做（`CREATE INDEX IF NOT EXISTS`）；
    不修改列定义（仍 VARCHAR(128) NULL）· 不 backfill 历史 NULL 行 ·
    不建 UNIQUE / 不建复合索引 · 不删除或改动既有索引。

    Args:
        conn: 已开启事务的 SQLAlchemy Connection。

    Returns:
        True:  索引已存在或本次成功创建。
        False: 表还不存在（由 `Base.metadata.create_all()` 随表创建）。
    """
    if not inspect(conn).has_table("llm_usage_record", schema=LLM_USAGE_SCHEMA):
        return False

    conn.execute(text(_CREATE_ASSISTANT_REQUEST_ID_INDEX_SQL))
    logger.info("LLM usage assistant_request_id index ensured")
    return True


def init_db() -> None:
    """启用 pgvector extension + 创建 ORM 表 + 确保幂等索引 / 关联列。

    幂等行为：
        - pgvector extension 已存在 → 不报错
        - ORM 表已存在 → create_all 不会重复创建（只补缺失的表）
        - request_id 幂等索引已存在 → 不报错（IF NOT EXISTS）
        - assistant_request_id 列已存在 → 不报错（IF NOT EXISTS），
          历史数据保持 NULL（Phase 3.12 Step 36）
        - assistant_request_id 索引已存在 → 不报错（IF NOT EXISTS，
          Phase 3.12 Step 52；不改任何数据）

    Raises:
        RuntimeError: DATABASE_URL 未配置。
        RuntimeError: DB 不可达、extension 不可用或 DDL 失败。
        RuntimeError: llm_usage_record 中已存在重复 request_id
                      （见 :func:`ensure_request_id_idempotency_index`）。
    """
    engine = get_engine()
    if engine is None:
        raise RuntimeError("DATABASE_URL 未配置；无法初始化数据库。")

    try:
        with engine.begin() as conn:
            # 1. 启用 pgvector extension
            conn.execute(text(_ENABLE_PGVECTOR_SQL))

            # 2. 启用 AI 内部运维 schema（幂等；llm_usage_record 落在这里）
            conn.execute(text(_ENABLE_AI_OPS_SCHEMA_SQL))

            # 3. 确保所有 ORM Model 已注册到 Base.metadata。
            #    显式 import models 包（即使上层已经 import 过），
            #    保证 `python -m backend.app.db.init_db` 单独执行时仍能找到 Model。
            from backend.app.db import models  # noqa: F401

            # 4. 创建所有未存在的表（幂等）
            Base.metadata.create_all(bind=conn)

            # 5. Phase 3.10.16：request_id 幂等唯一 partial index
            #    （对**已存在**的表，create_all 不会补建新增索引，
            #     必须显式 DDL；有重复数据则不自动清理，直接停止）
            ensure_request_id_idempotency_index(conn)

            # 6. Phase 3.12 Step 36：Assistant Trace 关联列
            #    （同样对已存在的表显式补齐；不改任何历史数据）
            ensure_assistant_request_id_column(conn)

            # 7. Phase 3.12 Step 52：Assistant Trace 关联索引
            #    （普通 B-tree · 非唯一；对已存在的表显式补齐；
            #     只影响执行计划，不改变查询结果 / 数据）
            ensure_assistant_request_id_index(conn)
    except SQLAlchemyError as exc:
        logger.exception("Failed to initialize database")
        raise RuntimeError(
            f"无法初始化数据库: {type(exc).__name__}: {exc}"
        ) from exc

    logger.info("Database initialized (pgvector extension + ORM tables ensured)")


def main() -> int:
    """CLI 入口：`python -m backend.app.db.init_db`"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    try:
        init_db()
    except RuntimeError as exc:
        print(f"[init_db] FAIL: {exc}", file=sys.stderr)
        return 1
    print("[init_db] OK")
    return 0


__all__ = [
    "init_db",
    "ensure_request_id_idempotency_index",
    "ensure_assistant_request_id_column",
    "ensure_assistant_request_id_index",
]


if __name__ == "__main__":
    sys.exit(main())