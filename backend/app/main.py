"""FastAPI 应用入口（Phase 1）。

启动方式（项目根目录）：

    python -m uvicorn backend.app.main:app --reload

或在 backend/app 目录下：

    uvicorn main:app --reload
"""
from __future__ import annotations

from fastapi import FastAPI

from backend.app.api import chat, health, rag, tool_chat
from backend.app.config import settings


def create_app() -> FastAPI:
    """应用工厂，便于测试与未来多实例启动。"""
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description=(
            "WMS AI Assistant — Phase 1 Project Foundation. "
            "当前为 MVP 项目骨架，未接入真实 LLM / RAG / Tool。"
        ),
    )
    app.include_router(health.router, prefix="/api", tags=["health"])
    app.include_router(chat.router, prefix="/api", tags=["chat"])
    app.include_router(rag.router, prefix="/api", tags=["rag"])
    app.include_router(tool_chat.router, prefix="/api", tags=["chat"])
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=True,
    )


__all__ = ["app", "create_app"]
