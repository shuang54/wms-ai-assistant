"""Health Check 接口。

不依赖 LLM / 数据库 / 外部系统，用于容器探针与负载均衡健康检查。
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from backend.app.config import settings


router = APIRouter()


class HealthResponse(BaseModel):
    """健康检查响应。"""

    status: str
    service: str


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """返回服务健康状态。

    service 使用稳定的机器标识符（不随 APP_NAME 变化），
    方便探针、监控与负载均衡按固定字符串识别服务。
    """
    return HealthResponse(status="ok", service="wms-ai-assistant")


__all__ = ["router"]
