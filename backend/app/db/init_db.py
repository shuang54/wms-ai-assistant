"""数据库初始化（Phase 3.1）。

职责：
    - 启用 pgvector extension（幂等）
    - 不创建任何业务表（Phase 3.2+ 才引入 knowledge_* 等）

为什么用 SQL 脚本而不是 Alembic：
    - 当前阶段表结构极少（只有 extension）
    - 引入 Alembic 会增加迁移文件 / 配置 / 版本管理复杂度
    - 后续 Phase 真正需要 schema 演进时再引入 Alembic；
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

from backend.app.db.session import get_engine

logger = logging.getLogger(__name__)

# 幂等 SQL：重复执行不会报错。
_ENABLE_PGVECTOR_SQL: Final[str] = "CREATE EXTENSION IF NOT EXISTS vector"


def init_db() -> None:
    """启用 pgvector extension。

    Raises:
        RuntimeError: DATABASE_URL 未配置。
        RuntimeError: DB 不可达或 extension 不可用。
    """
    engine = get_engine()
    if engine is None:
        raise RuntimeError("DATABASE_URL 未配置；无法初始化数据库。")

    try:
        with engine.begin() as conn:
            conn.execute(text(_ENABLE_PGVECTOR_SQL))
    except SQLAlchemyError as exc:
        logger.exception("Failed to enable pgvector extension")
        raise RuntimeError(
            f"无法启用 pgvector extension: {type(exc).__name__}: {exc}"
        ) from exc

    logger.info("pgvector extension ensured")


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