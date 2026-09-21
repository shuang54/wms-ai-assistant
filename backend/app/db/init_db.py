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
from typing import Final

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from backend.app.db.base import Base
from backend.app.db.session import get_engine

logger = logging.getLogger(__name__)

# 幂等 SQL：重复执行不会报错。
_ENABLE_PGVECTOR_SQL: Final[str] = "CREATE EXTENSION IF NOT EXISTS vector"


def init_db() -> None:
    """启用 pgvector extension + 创建 ORM 表。

    幂等行为：
        - pgvector extension 已存在 → 不报错
        - ORM 表已存在 → create_all 不会重复创建（只补缺失的表）

    Raises:
        RuntimeError: DATABASE_URL 未配置。
        RuntimeError: DB 不可达、extension 不可用或 DDL 失败。
    """
    engine = get_engine()
    if engine is None:
        raise RuntimeError("DATABASE_URL 未配置；无法初始化数据库。")

    try:
        with engine.begin() as conn:
            # 1. 启用 pgvector extension
            conn.execute(text(_ENABLE_PGVECTOR_SQL))

            # 2. 确保所有 ORM Model 已注册到 Base.metadata。
            #    显式 import models 包（即使上层已经 import 过），
            #    保证 `python -m backend.app.db.init_db` 单独执行时仍能找到 Model。
            from backend.app.db import models  # noqa: F401

            # 3. 创建所有未存在的表（幂等）
            Base.metadata.create_all(bind=conn)
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


__all__ = ["init_db"]


if __name__ == "__main__":
    sys.exit(main())