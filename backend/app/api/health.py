"""Health Check 接口。

不依赖 LLM；可选择性探测数据库（连接失败不抛异常）。

返回：
    {
        "status": "ok",
        "service": "wms-ai-assistant",
        "database": "ok" | "error" | "disabled"
    }

- database="disabled"  → DATABASE_URL 未配置
- database="ok"        → 连接 + SELECT 1 成功
- database="error"     → 连接失败（service 仍为 ok，不让应用启动崩溃）
"""
from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from backend.app.config import settings
from backend.app.db.session import ping_database

logger = logging.getLogger(__name__)
router = APIRouter()


class HealthResponse(BaseModel):
    """健康检查响应。"""

    status: str
    service: str
    database: str


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """返回服务健康状态。

    service 使用稳定的机器标识符（不随 APP_NAME 变化），
    方便探针、监控与负载均衡按固定字符串识别服务。
    """
    # 无 DATABASE_URL：DB 功能禁用。
    if not settings.database.url.strip():
        return HealthResponse(
            status="ok",
            service="wms-ai-assistant",
            database="disabled",
        )

    ok, detail = ping_database()
    db_status = "ok" if ok else "error"
    if not ok:
        # 数据库不可达不应让 /api/health 返回 5xx，否则会触发 LB 摘除；
        # 业务层日志保留足够上下文。
        logger.warning("Database health check failed: %s", detail)

    return HealthResponse(
        status="ok",
        service="wms-ai-assistant",
        database=db_status,
    )


__all__ = ["router", "HealthResponse"]