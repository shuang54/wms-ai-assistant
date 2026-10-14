"""WMS/ERP 业务库 Engine Provider（**独立**于应用库；只读）。

设计（与 ``db/session.py`` 的边界）：

```text
BusinessDatabaseSettings（WMS_DATABASE_*；config.py）
        ↓  URL.create（凭据只在本机 .env；**不**出现在日志 / repr / 异常）
require_business_engine() / get_business_engine()
        ↓  create_engine（独立连接池；**不复用**应用库 Engine）
        ↓  connect_args: default_transaction_read_only=on（会话级只读兜底）
库存 Tool（get_inventory；参数化查询 + BEGIN READ ONLY + statement_timeout）
        ↓
WMS/ERP 测试库（只读账号）
```

纪律：

* **不复用应用库**：独立 ``create_engine`` / 独立连接池 / 独立缓存；应用库
  （``DATABASE_URL`` / ``db.session.get_engine``）**完全不变**；
* **fail-closed**：``WMS_DATABASE_*`` 缺任一项（含密码为空）⇒
  :func:`get_business_engine` 返回 ``None``、:func:`require_business_engine`
  抛 :class:`BusinessDataSourceError`（**绝不**回退应用库 / 绝不返回假数据）；
* **只读兜底**：连接参数带 ``default_transaction_read_only=on``（即使调用方
  忘记只读事务，数据库也会拒绝写操作；与库存 Tool 的 ``BEGIN READ ONLY``
  形成双保险）；账号本身亦应为只读；
* **零凭据泄漏**：不记录 URL / 密码；:meth:`BusinessDatabaseSettings.summary`
  只含 host / port / database / configured；
* **不建表 / 不迁移**：本模块只做连接与查询边界，不含任何 DDL。
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Final

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, URL

from backend.app.config import BusinessDatabaseSettings, settings

logger = logging.getLogger(__name__)

__all__ = [
    "BusinessDataSourceError",
    "is_business_datasource_configured",
    "get_business_engine",
    "require_business_engine",
    "reset_business_engine_cache",
    "business_datasource_summary",
]

#: 未配置时的**明确**报错文案（含需要的环境变量名；无凭据）。
_UNCONFIGURED_HINT: Final[str] = (
    "WMS/ERP 业务数据源未配置：请在本机 .env 设置 "
    "WMS_DATABASE_HOST / WMS_DATABASE_PORT / WMS_DATABASE_NAME / "
    "WMS_DATABASE_USER / WMS_DATABASE_PASSWORD（只读账号；"
    "配置仅保存在忽略跟踪的本机环境文件，不写入代码 / 测试 / Git）"
)

#: 只读兜底：会话级默认只读事务（数据库端拒绝写操作）。
_READ_ONLY_OPTION: Final[str] = "-c default_transaction_read_only=on"

#: 连接标识（服务端可观测；不含任何凭据）。
_APPLICATION_NAME: Final[str] = "wms-ai-assistant"


class BusinessDataSourceError(RuntimeError):
    """业务（WMS/ERP）数据源不可用：未配置 / 参数非法 / 建连失败。

    上层（库存 Tool）把它归一为 ``ToolExecutionError``（显式失败，
    **不**回退应用库、**不**返回假数据）。
    """


# ------------------------------------------------------------------
# 进程内单例（独立于应用库的 Engine 缓存）
# ------------------------------------------------------------------

_business_engine: Engine | None = None
_business_engine_lock = threading.Lock()


def is_business_datasource_configured(
    *, business_settings: BusinessDatabaseSettings | None = None
) -> bool:
    """业务数据源是否已配置（全部连接参数齐备；只读判定，不建连）。"""
    cfg = business_settings if business_settings is not None else settings.business_database
    return cfg.is_configured


def _build_url(cfg: BusinessDatabaseSettings) -> URL:
    """构造 SQLAlchemy URL（``URL.create`` —— 密码自动转义，**不**手工拼接）。"""
    return URL.create(
        "postgresql+psycopg",
        username=cfg.user,
        password=cfg.password,
        host=cfg.host,
        port=cfg.port,
        database=cfg.name,
    )


def get_business_engine() -> Engine | None:
    """返回业务库 Engine（进程内单例）；**未配置 ⇒ ``None``**（不建连）。

    首次调用才 ``create_engine``（惰性）；日志只记录非敏感摘要。
    """
    global _business_engine
    cfg = settings.business_database
    if not cfg.is_configured:
        return None
    if _business_engine is not None:
        return _business_engine
    with _business_engine_lock:
        if _business_engine is None:
            try:
                engine = create_engine(
                    _build_url(cfg),
                    pool_size=cfg.pool_size,
                    max_overflow=cfg.max_overflow,
                    pool_pre_ping=True,
                    # echo 恒为 False：业务 SQL 不进入应用日志。
                    echo=False,
                    connect_args={
                        "connect_timeout": cfg.connect_timeout,
                        "application_name": _APPLICATION_NAME,
                        "options": _READ_ONLY_OPTION,
                    },
                )
            except Exception as exc:  # noqa: BLE001 —— 归一为该边界的显式错误
                raise BusinessDataSourceError(
                    "WMS/ERP 业务数据源 Engine 构建失败"
                    f"（{type(exc).__name__}；不输出连接参数）"
                ) from exc
            _business_engine = engine
            logger.info(
                "business datasource engine created",
                extra=cfg.summary(),
            )
    return _business_engine


def require_business_engine() -> Engine:
    """必须可用的业务库 Engine（库存 Tool 的 Engine Provider）。

    Returns:
        Engine：独立于应用库的只读业务库 Engine。

    Raises:
        BusinessDataSourceError: 未配置（消息含所需环境变量名；无凭据）。
    """
    engine = get_business_engine()
    if engine is None:
        raise BusinessDataSourceError(_UNCONFIGURED_HINT)
    return engine


def reset_business_engine_cache() -> None:
    """测试辅助：释放并清空业务库 Engine 缓存（不影响应用库缓存）。"""
    global _business_engine
    with _business_engine_lock:
        engine = _business_engine
        _business_engine = None
    if engine is not None:
        try:
            engine.dispose()
        except Exception as exc:  # noqa: BLE001 —— best-effort（不影响测试语义）
            logger.warning(
                "business engine dispose failed",
                extra={"error_type": type(exc).__name__},
            )


def business_datasource_summary() -> dict[str, Any]:
    """非敏感摘要（configured / host / port / database；**无** user / password）。

    供人工排查与脚本打印使用。
    """
    return settings.business_database.summary()
