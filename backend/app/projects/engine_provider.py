"""Database Engine Provider（Phase 3.8.1）。

职责（任务书 §七）：

    DataSource（数据源身份，非敏感）
        ↓
    DatabaseEngineProvider.get_engine(data_source)
        ↓
    SQLAlchemy Engine（服务器端受控连接）

设计约束（任务书 §五 / §七 / §八 安全边界）：

- **DataSource / ProjectContext 不含密码**：连接凭据只存在于
  服务器端的 ``DatabaseSettings``（.env / 代码级注册），
  EngineProvider 按 ``data_source.name``（连接键）解析；
- **不允许用户通过 HTTP 传 connection string / host / port / password**：
  ``get_engine`` 只接受 DataSource DTO；Provider 的连接注册表
  （``register_connection``）只能由服务器端代码 / 测试调用；
- **"primary" 委托现有全局 Engine**（``db.session.get_engine()``）：
  与既有全局连接池共享（不重复建池），并保持
  ``reset_engine_cache()`` 语义；其他连接键独立构建 + 进程内缓存；
- **Engine 缓存按连接键**：同一键重复调用返回同一 Engine 实例
  （SQLAlchemy Engine 本身线程安全、自带连接池）；
- 日志只记录 data_source_name / engine_cached，**不记录 URL / 密码**。
"""
from __future__ import annotations

import logging
import threading
from typing import Final, Mapping, Protocol

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from backend.app.config import DatabaseSettings
from backend.app.projects.models import DataSource

logger = logging.getLogger(__name__)

__all__ = [
    "DatabaseEngineProvider",
    "DatabaseEngineProviderError",
    "DataSourceNotFoundError",
    "DefaultDatabaseEngineProvider",
    "DEFAULT_CONNECTION_KEY",
    "get_default_engine_provider",
]

#: 默认连接键：与 Phase 3.7.1.1 的 DataSource(name="primary") 对应。
#: 该键映射到现有全局 Engine（db.session.get_engine），不重复建池。
DEFAULT_CONNECTION_KEY: Final[str] = "primary"


# ============================================================
# 异常体系
# ============================================================

class DatabaseEngineProviderError(Exception):
    """Engine Provider 通用异常（连接未配置 / URL 为空 / 注册非法等）。"""


class DataSourceNotFoundError(DatabaseEngineProviderError):
    """DataSource.name 未在服务器端注册（用户不能指定任意连接）。

    Attributes:
        data_source_name: 未注册的连接键。
    """

    def __init__(self, data_source_name: str) -> None:
        super().__init__(
            f"数据源 {data_source_name!r} 未在服务器端注册"
            "（project_id 只能选择已注册的数据源）"
        )
        self.data_source_name = data_source_name


# ============================================================
# Protocol（任务书 §七）
# ============================================================

class DatabaseEngineProvider(Protocol):
    """数据源 → Engine 解析协议（Phase 3.8.1 引入）。

    上层（Orchestrator 工厂 / Tool 工厂）只依赖本协议；
    未来可替换为按租户 / 配置中心解析的实现。
    """

    def get_engine(self, data_source: DataSource) -> Engine:
        """返回 data_source 对应的受控 Engine。

        Raises:
            DataSourceNotFoundError: 连接键未注册。
            DatabaseEngineProviderError: 连接配置非法（URL 为空等）。
        """
        ...


# ============================================================
# 默认实现
# ============================================================

class DefaultDatabaseEngineProvider:
    """基于服务器端注册表的 Engine Provider。

    连接注册表：``{连接键: DatabaseSettings}``。

    - ``"primary"``（默认存在）：委托 ``db.session.get_engine()``
      （共享现有全局连接池；DATABASE_URL 为空时抛清晰错误）；
    - 其他键：``register_connection`` 注册后独立构建 Engine，
      进程内按键缓存（同一键返回同一实例）；
    - **不接受 URL 字符串参数**：凭据只经 DatabaseSettings
      （服务器端 .env / 测试代码）进入，杜绝 HTTP 注入（任务书 §八）。
    """

    def __init__(
        self,
        *,
        default_settings: DatabaseSettings | None = None,
        extra_connections: Mapping[str, DatabaseSettings] | None = None,
    ) -> None:
        """构造 Provider。

        Args:
            default_settings: "primary" 键的配置（仅用于校验 URL 非空；
                              实际 Engine 委托 db.session 全局缓存）。
                              None 时使用全局 settings.database。
            extra_connections: 额外服务器端连接（代码级注册，测试 / 未来
                              第二生产库使用）；键即 DataSource.name。
        """
        self._default_settings = default_settings
        self._connections: dict[str, DatabaseSettings] = dict(
            extra_connections or {}
        )
        self._engines: dict[str, Engine] = {}
        self._lock = threading.Lock()

    # ---------- 注册（服务器端 / 测试） ----------

    def register_connection(
        self, name: str, db_settings: DatabaseSettings
    ) -> None:
        """注册一个服务器端连接（重复键 → 报错，防意外覆盖）。"""
        if not isinstance(name, str) or not name.strip():
            raise DatabaseEngineProviderError(
                f"连接键必须是非空 str（当前: {name!r}）"
            )
        if name == DEFAULT_CONNECTION_KEY:
            raise DatabaseEngineProviderError(
                f"连接键 {DEFAULT_CONNECTION_KEY!r} 保留（映射全局 DATABASE_URL），不可覆盖"
            )
        if not isinstance(db_settings, DatabaseSettings):
            raise DatabaseEngineProviderError(
                "db_settings 必须是 DatabaseSettings 实例"
                f"（当前: {type(db_settings).__name__}）"
            )
        if not db_settings.url.strip():
            raise DatabaseEngineProviderError(
                f"连接 {name!r} 的 DATABASE_URL 为空，拒绝注册"
            )
        with self._lock:
            if name in self._connections:
                raise DatabaseEngineProviderError(f"连接 {name!r} 已注册，不可覆盖")
            self._connections[name] = db_settings
        logger.info("database connection registered", extra={"key": name})

    def reset_cache(self) -> None:
        """测试辅助：清空非 primary 键的 Engine 缓存（不 dispose，交由 GC）。"""
        with self._lock:
            self._engines.clear()

    # ---------- 解析 ----------

    def get_engine(self, data_source: DataSource) -> Engine:
        """见 Protocol docstring。"""
        if not isinstance(data_source, DataSource):
            raise DatabaseEngineProviderError(
                "data_source 必须是 DataSource 实例"
                f"（当前: {type(data_source).__name__}）"
            )
        key = data_source.name

        if key == DEFAULT_CONNECTION_KEY:
            return self._get_primary_engine()

        with self._lock:
            cached = self._engines.get(key)
            if cached is not None:
                return cached
            db_settings = self._connections.get(key)
            if db_settings is None:
                raise DataSourceNotFoundError(key)
            engine = self._build_engine(db_settings)
            self._engines[key] = engine
        logger.info(
            "database engine created",
            extra={"data_source_name": key, "engine_cached": True},
        )
        return engine

    # ---------- primary（委托全局 Engine） ----------

    def _get_primary_engine(self) -> Engine:
        """primary 连接：委托 db.session.get_engine()（共享全局池）。"""
        # 延迟 import：保持 projects 包对 db.session 的弱耦合
        from backend.app.db.session import get_engine

        db_settings = self._default_settings
        if db_settings is None:
            from backend.app.config import settings

            db_settings = settings.database
        engine = get_engine()
        if engine is None:
            raise DatabaseEngineProviderError(
                "默认数据源 'primary' 不可用（DATABASE_URL 未配置）"
            )
        return engine

    # ---------- Engine 构建 ----------

    @staticmethod
    def _build_engine(db_settings: DatabaseSettings) -> Engine:
        """按 DatabaseSettings 构建 Engine（参数与 db.session._build_engine 一致）。"""
        if not db_settings.url.strip():
            raise DatabaseEngineProviderError(
                "连接的 DATABASE_URL 为空，无法创建 Engine"
            )
        return create_engine(
            db_settings.url,
            echo=db_settings.echo,
            pool_size=db_settings.pool_size,
            max_overflow=db_settings.max_overflow,
            pool_pre_ping=True,
            future=True,
        )


# ============================================================
# 默认单例（惰性）
# ============================================================

_default_provider: DefaultDatabaseEngineProvider | None = None
_default_provider_lock = threading.Lock()


def get_default_engine_provider() -> DefaultDatabaseEngineProvider:
    """返回进程级默认 Engine Provider（惰性创建，线程安全）。

    默认只含 "primary"（全局 DATABASE_URL）；第二生产库由部署方
    在服务器端代码调用 ``register_connection``（本阶段只设计接口，
    任务书 §十八）。
    """
    global _default_provider
    if _default_provider is None:
        with _default_provider_lock:
            if _default_provider is None:
                _default_provider = DefaultDatabaseEngineProvider()
    return _default_provider
