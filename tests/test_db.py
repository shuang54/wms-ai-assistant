"""Database Layer Tests（Phase 3.1）。

覆盖：
    1. Database 配置读取（mock-free，仅验证配置模块）
    2. Database URL 解析（mock-free）
    3. 真实数据库连接（无 DB 环境 → 自动 skip；**不**用 mock 冒充）
    4. 简单 SQL 查询（SELECT 1）
    5. pgvector extension 是否存在

跳过规则：
    - 默认无 DB 时跳过"真实连接 / SQL 查询 / pgvector" 三个集成测试
    - 配置读取 / URL 解析测试始终运行（不依赖外部 DB）

环境变量：
    - RUN_DB_TESTS=true      显式启用 DB 集成测试（默认 skip）
    - DATABASE_URL           必须指向一个真实可达的 PostgreSQL
"""
from __future__ import annotations

import os

import pytest


def _env_flag(name: str) -> bool:
    val = os.getenv(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


# 默认 skip：避免无 DB 环境时整 pytest 集合因连接超时拖慢
_RUN_DB = _env_flag("RUN_DB_TESTS")

# 集成测试 marker：仅在开启 + DATABASE_URL 存在时执行
requires_db = pytest.mark.skipif(
    not _RUN_DB,
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)


# ============================================================
# 配置读取（mock-free）
# ============================================================

class TestDatabaseConfig:
    """DatabaseSettings / Settings 配置测试，无需 DB 连接。"""

    def test_database_settings_defaults_when_no_env(self) -> None:
        """未配置 DATABASE_URL 时，url 应为空字符串、echo 应为 False。"""
        from backend.app.config import DatabaseSettings

        # 构造时不读 env（所有字段都有 default_factory，但 url 会读 os.getenv）
        # 这里直接验证类型与默认值在显式 None 时的行为
        ds = DatabaseSettings(url="")
        assert ds.url == ""
        assert ds.echo is False
        assert ds.pool_size >= 1
        assert ds.max_overflow >= 0

    def test_database_url_uses_psycopg_driver(self) -> None:
        """DATABASE_URL 推荐使用 psycopg 3 驱动前缀。"""
        from backend.app.config import settings

        url = settings.database.url
        if not url:
            pytest.skip("DATABASE_URL 未配置")
        # 验证前缀（兼容 postgresql:// 和 postgresql+psycopg://）
        assert url.startswith("postgresql"), (
            f"DATABASE_URL 必须以 postgresql 开头，当前：{url[:32]}..."
        )
        # 推荐使用 psycopg（3.x）
        assert "+psycopg" in url or url.startswith("postgresql://"), (
            f"DATABASE_URL 推荐使用 psycopg 3 驱动：{url[:64]}..."
        )

    def test_database_pool_size_positive(self) -> None:
        """DATABASE_POOL_SIZE 必须为正整数。"""
        from backend.app.config import settings

        assert settings.database.pool_size >= 1
        assert settings.database.max_overflow >= 0


# ============================================================
# Engine 工厂（mock-free）
# ============================================================

class TestDatabaseEngine:
    """Engine 工厂测试。"""

    def teardown_method(self) -> None:
        """每个测试后清空 Engine 缓存，避免污染。"""
        from backend.app.db import reset_engine_cache

        reset_engine_cache()

    def test_get_engine_returns_none_when_no_url(self) -> None:
        """DATABASE_URL 为空时，get_engine 返回 None（不抛异常）。

        说明：Settings 是 frozen dataclass，无法在测试中替换全局实例。
        因此采用更直接的方式——如果当前 settings.database.url 已为空，
        验证 get_engine() 返回 None；否则 skip（环境提供了真实 DB）。
        """
        from backend.app.config import settings
        from backend.app.db import reset_engine_cache
        from backend.app.db.session import get_engine

        reset_engine_cache()
        if settings.database.url.strip():
            pytest.skip(
                "当前环境已配置 DATABASE_URL；此测试仅在无 DB 配置时验证 get_engine() 返回 None"
            )
        assert get_engine() is None


# ============================================================
# 集成测试（有 DB 才跑）
# ============================================================

@requires_db
class TestDatabaseIntegration:
    """真实 PostgreSQL 集成测试。

    默认 skip；开启 RUN_DB_TESTS=true 且 DATABASE_URL 已配置时才执行。
    **不使用 mock** —— 必须能真实连接 PostgreSQL。
    """

    def teardown_method(self) -> None:
        from backend.app.db import reset_engine_cache

        reset_engine_cache()

    def test_real_connection(self) -> None:
        """真实连接到 PostgreSQL 并执行 SELECT 1。"""
        from backend.app.db import reset_engine_cache
        from backend.app.db.session import get_engine

        reset_engine_cache()
        engine = get_engine()
        assert engine is not None, "DATABASE_URL 未配置或 Engine 创建失败"

        from sqlalchemy import text

        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1")).scalar()
        assert result == 1

    def test_pgvector_extension_exists(self) -> None:
        """验证 pgvector extension 已安装。"""
        from backend.app.db import reset_engine_cache
        from backend.app.db.session import get_engine

        reset_engine_cache()
        engine = get_engine()
        assert engine is not None

        from sqlalchemy import text

        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
            ).first()

        if row is None:
            pytest.fail(
                "pgvector extension 未启用；请运行 `python -m backend.app.db.init_db` "
                "或确保 docker-compose 启动的是 pgvector/pgvector 镜像。"
            )
        assert row[0] == "vector"

    def test_pgvector_basic_query(self) -> None:
        """验证 pgvector 可以真正解析向量字面量。"""
        from backend.app.db import reset_engine_cache
        from backend.app.db.session import get_engine

        reset_engine_cache()
        engine = get_engine()
        assert engine is not None

        from sqlalchemy import text

        with engine.connect() as conn:
            result = conn.execute(text("SELECT '[1,2,3]'::vector")).scalar()
        assert result is not None

    def test_ping_database_returns_ok(self) -> None:
        """ping_database() 在真实连接可用时返回 (True, 'ok')。"""
        from backend.app.db import reset_engine_cache
        from backend.app.db.session import ping_database

        reset_engine_cache()
        ok, detail = ping_database()
        assert ok is True, f"ping_database 失败：{detail}"
        assert detail == "ok"


@requires_db
class TestInitDb:
    """init_db() 集成测试。"""

    def teardown_method(self) -> None:
        from backend.app.db import reset_engine_cache

        reset_engine_cache()

    def test_init_db_is_idempotent(self) -> None:
        """init_db() 重复调用不应报错。"""
        from backend.app.db import reset_engine_cache
        from backend.app.db.init_db import init_db
        from backend.app.db.session import get_engine

        reset_engine_cache()
        assert get_engine() is not None, "DATABASE_URL 未配置"

        # 重复执行必须幂等
        init_db()
        init_db()
        init_db()