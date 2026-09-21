"""Database Session / Engine（Phase 3.1）。

职责：
    - 构造 SQLAlchemy Engine（懒加载 + pool_pre_ping）
    - 提供 SessionLocal 工厂
    - 提供 FastAPI Depends 友好的 get_db 上下文管理器

注意事项：
    - DATABASE_URL 为空时，**不创建 Engine**，避免应用启动即抛异常。
      这样本地无 DB 时，/api/health 仍能返回 database="disabled"，
      /api/chat 不受影响（chat 路径根本不连 DB）。
    - 所有函数均采用延迟导入，避免循环依赖。
"""
from __future__ import annotations

import logging
from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.config import DatabaseSettings, settings

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Engine 构造（延迟）
# ------------------------------------------------------------------

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _build_engine(db_settings: DatabaseSettings) -> Engine:
    """根据 DatabaseSettings 构造 SQLAlchemy Engine。

    - 使用 psycopg 3 驱动（postgresql+psycopg://...）
    - pool_pre_ping=True 自动剔除失效连接
    - echo 仅在显式开启 DATABASE_ECHO=true 时输出 SQL
    """
    return create_engine(
        db_settings.url,
        echo=db_settings.echo,
        pool_size=db_settings.pool_size,
        max_overflow=db_settings.max_overflow,
        pool_pre_ping=True,
        future=True,
    )


def get_engine() -> Engine | None:
    """获取全局 Engine。

    Returns:
        Engine: 当 DATABASE_URL 已配置时。
        None:  当 DATABASE_URL 未配置（DB 功能禁用）。
    """
    global _engine
    if _engine is None:
        url = settings.database.url.strip()
        if not url:
            return None
        _engine = _build_engine(settings.database)
        logger.info(
            "Database engine initialized",
            extra={"pool_size": settings.database.pool_size},
        )
    return _engine


def get_session_factory() -> sessionmaker[Session] | None:
    """获取 SessionLocal 工厂。

    Returns:
        sessionmaker[Session]: 当 DATABASE_URL 已配置时。
        None: 当 DATABASE_URL 未配置。
    """
    global _SessionLocal
    if _SessionLocal is None:
        engine = get_engine()
        if engine is None:
            return None
        _SessionLocal = sessionmaker(
            bind=engine,
            autoflush=False,
            expire_on_commit=False,
            future=True,
        )
    return _SessionLocal


def reset_engine_cache() -> None:
    """测试辅助：清空 engine / session factory 缓存。"""
    global _engine, _SessionLocal
    _engine = None
    _SessionLocal = None


# ------------------------------------------------------------------
# FastAPI Depends
# ------------------------------------------------------------------

def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖注入：提供 DB Session，结束后自动关闭。

    用法（Phase 3.2+ 的 API 路由）:
        from fastapi import Depends
        from backend.app.db.session import get_db
        from sqlalchemy.orm import Session

        @router.get(...)
        def handler(db: Session = Depends(get_db)):
            ...

    注意：调用方负责 commit / rollback / 处理异常。
    """
    factory = get_session_factory()
    if factory is None:
        raise RuntimeError(
            "DATABASE_URL 未配置；get_db() 不可用。"
            "请在 .env 中设置 DATABASE_URL 后再使用 DB 相关接口。"
        )
    db = factory()
    try:
        yield db
    finally:
        db.close()


# ------------------------------------------------------------------
# Health 探测
# ------------------------------------------------------------------

def ping_database() -> tuple[bool, str]:
    """探测数据库连接状态。

    Returns:
        (ok, detail):
            - (True,  "ok")                连接成功
            - (False, "<error-class>: <msg>") 连接失败
    """
    engine = get_engine()
    if engine is None:
        return False, "DATABASE_URL 未配置"
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, "ok"
    except SQLAlchemyError as exc:
        return False, f"{type(exc).__name__}: {exc}"


__all__ = [
    "Engine",
    "get_engine",
    "get_session_factory",
    "get_db",
    "ping_database",
    "reset_engine_cache",
]