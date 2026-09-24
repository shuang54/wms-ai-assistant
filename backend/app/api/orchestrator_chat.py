"""AI Chat API（Phase 3.7.11：Orchestrator-backed Unified Chat）。

边界（API 层只做 HTTP 适配）：

    HTTP Client
        ↓
    POST /api/ai/chat
        ↓
    Chat API（仅校验 + 异常映射 + DTO 转换）
        ↓
    AIOrchestratorService
        ↓
    AIRouterService
        ↓
    ┌───────────┼────────────────┐
    ↓           ↓                ↓
  RAG          Tool             Text-to-SQL
    ↓
  AIOrchestrationResult
    ↓
  ChatResponse

设计纪律（纵深防御）：

* API 层**不**实现业务路由 / 内容生成 / SQL 执行
* API 层**不**直接调用：RagService / ToolRegistry / TextToSQLService /
  SQLValidator / SQLExecutor
* API 层**不**新建 Engine / LLMClient / Embedding Client；
  通过 ``get_default_orchestrator()`` 复用现有 Phase 3.7.9 工厂
* API 层**不**在 response 中暴露：
    - API Key / Bearer / Authorization header
    - DATABASE_URL / 数据库密码
    - 内部 traceback
    - SQLAlchemy Connection / Session
* ``project_id`` 不在 API 层硬编码判断，直接透传到 Orchestrator
  (由 ProjectContextProvider.resolve() 解释为 ProjectContext.project_id)
* 现有 ``/api/chat``（ChatService / RAG-only，Phase 3.5.6）行为**不变**：
  本端点是新增的 Orchestrator 总入口，不替换旧端点
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from backend.app.config import settings
from backend.app.projects.context import (
    ProjectContext,
    get_default_project_context,
)
from backend.app.services.ai_orchestrator_service import (
    AIOrchestrationResult,
    AIOrchestratorError,
    AIOrchestratorExecutionError,
    AIOrchestratorInputError,
    AIOrchestratorRouteError,
    AIOrchestratorService,
    AIOrchestratorUnavailableError,
    ProjectContextProvider,
    get_default_orchestrator,
)


logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================
# Request / Response DTO
# ============================================================

class ChatRequest(BaseModel):
    """Orchestrator-backed Chat 请求体。

    Attributes:
        question:   必填，非空字符串，自动 strip；纯空白 → 422。
        project_id: 可选；省略时使用项目默认 ProjectContext。
                    API 层不做项目存在性校验（当前项目无 project 注册中心），
                    仅在 Orchestrator 中作为 ProjectContext.project_id 透传。
    """

    question: str = Field(
        ..., min_length=1, description="用户问题，不可为空或纯空白"
    )
    project_id: str | None = Field(
        default=None,
        max_length=128,
        description=(
            "可选：项目上下文 ID；省略时使用配置默认。"
            "API 层不做业务校验，仅透传到 Orchestrator。"
        ),
    )

    @field_validator("question")
    @classmethod
    def _reject_blank_question(cls, value: str) -> str:
        """纯空白在 API 层直接拒绝（422）。"""
        if not value.strip():
            raise ValueError("question 不能为空或纯空白")
        return value


class ChatSourceResponse(BaseModel):
    """RAG 命中来源（仅元数据；不返回 chunk content）。"""

    chunk_id: int
    document_id: int
    chunk_index: int
    similarity: float
    metadata: dict


class ChatToolResponse(BaseModel):
    """Tool 调用结果（仅结构化字段）。"""

    tool_name: str
    success: bool
    data: object | None = None
    error: str | None = None


class ChatSqlResponse(BaseModel):
    """Text-to-SQL 执行结果（不含 Row / Cursor / 原始 SQLAlchemy 对象）。"""

    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    execution_time_ms: float
    referenced_tables: list[str] = Field(default_factory=list)
    project_id: str | None = None
    sql: str | None = None  # 仅测试方便；生产可由调用方选择是否返回


class ChatResponse(BaseModel):
    """Orchestrator-backed Chat 响应（统一 DTO）。

    Attributes:
        route:     实际执行的路由（rag / tool / text_to_sql）。
        content:   自然语言 / 自然化摘要（来自 Orchestrator）。
        data:      路由相关结构化数据；RAG → None 或 RAG Summary；
                   Tool → ChatToolResponse；SQL → ChatSqlResponse。
                   避免把 RagResponse / ToolResult / SQLExecutionResult 整体
                   直接 dump 到外层（响应体保持 Pydantic 兼容）。
        metadata:  路由 + 执行统计；不含敏感信息。
    """

    route: str = Field(..., description="Orchestrator 实际执行的路由")
    content: str | None = Field(
        default=None, description="Orchestrator 提供的自然语言内容 / 摘要"
    )
    data: object | None = Field(
        default=None,
        description="路由相关结构化数据（RAG 摘要 / Tool 元数据 / SQL 结果）",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="路由 + 执行统计（不含敏感信息）",
    )


# ============================================================
# 单例 / 工厂
# ============================================================

# 模块级 Orchestrator 单例（懒加载：首次请求时才构造，避免 import 时拉起 Engine）。
# 测试中可通过 monkeypatch 替换 ``_default_orchestrator`` 或
# ``_build_orchestrator_for_project_id`` 注入 fake orchestrator。
_default_orchestrator: AIOrchestratorService = get_default_orchestrator()


def _build_orchestrator_for_project_id(
    project_id: str,
) -> AIOrchestratorService:
    """根据 ``project_id`` 构造临时 Orchestrator 实例。

    复用默认 Orchestrator 的所有下游依赖（Router / RAG / Tool / T2S / Executor），
    仅替换 ``ProjectContextProvider``，使其 ``resolve()`` 返回 project_id 与
    传入值匹配的 ProjectContext。Engine / LLMClient / Embedding Client
    不重新创建。

    Args:
        project_id: 来自请求的 project_id（已通过 Pydantic 校验非空）。

    Returns:
        新构造的 ``AIOrchestratorService``，下游依赖与默认实例共享。
    """
    base = _default_orchestrator

    class _ProjectedProvider(ProjectContextProvider):
        """把 project_id 写入 ProjectContext，其他字段从默认 ProjectContext 复用。"""

        def __init__(self) -> None:
            self._cached = self._build_context(project_id)

        @staticmethod
        def _build_context(pid: str) -> ProjectContext:
            base_ctx = get_default_project_context()
            # 保留 data_source（来自默认配置），只覆盖 id / name / description
            return ProjectContext(
                project_id=pid,
                project_name=pid,
                description=base_ctx.description,
                data_source=base_ctx.data_source,
            )

        def resolve(self):
            return (
                self._cached,
                *base._project_provider.resolve()[1:],  # type: ignore[attr-defined]
            )

    return AIOrchestratorService(
        router=base._router,  # type: ignore[attr-defined]
        rag_service=base._rag,  # type: ignore[attr-defined]
        tool_registry=base._tools,  # type: ignore[attr-defined]
        text_to_sql=base._text_to_sql,  # type: ignore[attr-defined]
        sql_executor=base._sql_executor,  # type: ignore[attr-defined]
        table_selector=base._table_selector,  # type: ignore[attr-defined]
        context_composer=base._context_composer,  # type: ignore[attr-defined]
        project_context_provider=_ProjectedProvider(),
    )


# ============================================================
# DTO 映射（AIOrchestrationResult → ChatResponse）
# ============================================================

def _rag_data(result: AIOrchestrationResult) -> dict[str, Any] | None:
    """RAG 路径的 data 提取：只暴露 sources 元数据 + used_chunks_count。

    防止 ``content`` chunk 全文泄露到 API response。
    """
    rag_response = result.data
    if rag_response is None:
        return None
    sources = getattr(rag_response, "sources", ()) or ()
    return {
        "sources": [
            ChatSourceResponse(
                chunk_id=s.chunk_id,
                document_id=s.document_id,
                chunk_index=s.chunk_index,
                similarity=s.similarity,
                metadata=dict(s.metadata) if getattr(s, "metadata", None) else {},
            ).model_dump()
            for s in sources
        ],
        "used_chunks_count": getattr(rag_response, "used_chunks_count", 0),
    }


def _tool_data(result: AIOrchestrationResult) -> ChatToolResponse:
    """Tool 路径的 data 提取：ToolResult → ChatToolResponse。"""
    tool_result = result.data
    return ChatToolResponse(
        tool_name=getattr(tool_result, "tool_name", ""),
        success=bool(getattr(tool_result, "success", False)),
        data=getattr(tool_result, "data", None),
        error=getattr(tool_result, "error", None),
    )


def _sql_data(result: AIOrchestrationResult) -> ChatSqlResponse:
    """SQL 路径的 data 提取：SQLExecutionResult → ChatSqlResponse。

    不暴露 SQLAlchemy Row / Cursor；只导出 columns / rows / row_count 等。
    """
    execution = result.data
    metadata = result.metadata or {}
    return ChatSqlResponse(
        columns=list(getattr(execution, "columns", ()) or ()),
        rows=[list(r) for r in (getattr(execution, "rows", ()) or ())],
        row_count=int(getattr(execution, "row_count", 0)),
        truncated=bool(getattr(execution, "truncated", False)),
        execution_time_ms=float(getattr(execution, "execution_time_ms", 0.0)),
        referenced_tables=list(metadata.get("selected_tables", []) or []),
        project_id=str(metadata.get("project_id", settings.project.project_id)),
        sql=str(metadata.get("sql", "")) or None,
    )


def _to_chat_response(result: AIOrchestrationResult) -> ChatResponse:
    """Orchestrator 结果 → ChatResponse DTO（按 route 分支映射）。"""
    route = result.route.value if hasattr(result.route, "value") else str(result.route)

    data: object | None
    if route == "rag":
        data = _rag_data(result)
    elif route == "tool":
        data = _tool_data(result).model_dump() if result.data is not None else None
    elif route == "text_to_sql":
        data = _sql_data(result).model_dump()
    else:
        data = None

    metadata = dict(result.metadata or {})
    # 暴露 route reason 给调试 / 日志，但不暴露 SQL / internal trace
    metadata.setdefault("route_reason", metadata.get("route_reason", ""))

    return ChatResponse(
        route=route,
        content=result.content,
        data=data,
        metadata=metadata,
    )


# ============================================================
# Endpoint
# ============================================================

@router.post(
    "/ai/chat",
    response_model=ChatResponse,
    responses={
        400: {"description": "请求参数非法（question 等）"},
        422: {"description": "请求体校验失败（Pydantic）"},
        500: {"description": "AI 服务内部错误"},
        502: {"description": "路由决策失败（Router / Tool 路由未命中）"},
        503: {"description": "项目上下文不可用"},
    },
)
async def chat(request: ChatRequest) -> ChatResponse:
    """Orchestrator-backed 统一对话入口。

    Pipeline：

        AIOrchestratorService.execute(question)
            → Router → RAG | Tool | Text-to-SQL
            → AIOrchestrationResult → ChatResponse

    与 ``/api/chat``（ChatService / RAG-only）的关系：

        * ``/api/chat``     行为不变（Phase 3.5.6，向后兼容）；
        * ``/api/ai/chat``  本端点，路由可命中三条路径。

    异常映射：

        AIOrchestratorInputError          → 400  Bad Request
            （question 非法 / 纯空白等，防御性兜底；Pydantic 也已拦截大部分）
        AIOrchestratorRouteError          → 502  Bad Gateway
            （Router 决策失败 / Tool 路由未命中 / 未知 route）
        AIOrchestratorUnavailableError    → 503  Service Unavailable
            （Project context 解析失败 / DB Schema 不可用）
        AIOrchestratorExecutionError      → 500  Internal Server Error
            （RAG / Tool / T2S 能力执行失败；保留原始异常链）
        其它未预期异常                    → 500  Internal Server Error
            （不暴露 traceback / SQL / credentials）

    安全（与项目既有 ``/api/chat`` / ``/api/rag/answer`` 一致）：

        * response / error detail **不**包含：
          - API Key / Bearer / Authorization
          - DATABASE_URL / database password
          - 内部 traceback
          - SQL Validator 内部敏感信息
    """
    try:
        orchestrator = (
            _default_orchestrator
            if not request.project_id
            else _build_orchestrator_for_project_id(request.project_id)
        )
        # 注意：question 已被 Pydantic strip + 非空校验；不再预处理。
        result = await orchestrator.execute(request.question)
    except AIOrchestratorInputError as exc:
        # 服务层兜底（Pydantic 已覆盖大部分）
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"非法输入: {exc}",
        )
    except AIOrchestratorRouteError as exc:
        logger.warning(
            "orchestrator route error",
            extra={
                "error_type": type(exc).__name__,
                "project_id": request.project_id,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"路由决策失败: {exc}",
        )
    except AIOrchestratorUnavailableError as exc:
        logger.error(
            "orchestrator unavailable",
            extra={
                "error_type": type(exc).__name__,
                "project_id": request.project_id,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"项目上下文不可用: {exc}",
        )
    except AIOrchestratorExecutionError as exc:
        logger.error(
            "orchestrator execution error",
            extra={
                "error_type": type(exc).__name__,
                "project_id": request.project_id,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"AI 能力执行失败: {exc}",
        )
    except AIOrchestratorError as exc:
        # 其它 Orchestrator 已知异常 → 500
        logger.error(
            "orchestrator unknown error",
            extra={
                "error_type": type(exc).__name__,
                "project_id": request.project_id,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"AI 服务内部错误: {exc}",
        )
    except Exception as exc:  # noqa: BLE001
        # 真正未预期异常；不暴露细节给客户端
        logger.exception(
            "orchestrator unexpected error",
            extra={"project_id": request.project_id},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="AI 服务内部错误",
        )

    return _to_chat_response(result)


__all__ = [
    "router",
    "ChatRequest",
    "ChatResponse",
    "ChatSourceResponse",
    "ChatToolResponse",
    "ChatSqlResponse",
    "ProjectContext",
    "ProjectContextProvider",
]