"""Agent Read API 认证与项目作用域授权（Phase 6 Step 8A — 安全对称修复）。

背景（Project Recovery Audit R1）：Phase 5 的 Agent Read API 两个只读端点
（``GET /api/agent/runs/{run_id}`` + ``/timeline``）**无任何认证依赖**，且
``project_id`` 由客户端 Query 声明（缺省回退项目默认）——S7.1 安全审计确认的
T1（未认证可读）/ T2（任意 project_id 冒充）缺口在 Agent 侧仍然存在。
本模块按 **Phase 6 Step 7.2（Workflow Read API）已冻结的同一模式** 修复：

```text
X-API-Key 请求头
    ↓  服务端配置比对（secrets.compare_digest —— 恒定时间）
服务端认证配置（AGENT_READ_API_KEY；与 Workflow 凭据**独立**、可分别轮换）
    ↓
服务端授权 project_id（AGENT_READ_API_PROJECT_ID —— **唯一**作用域来源）
    ↓
AgentReadService（只读；仅在授权作用域内查询）
```

与 Workflow 的语义差异（薄适配，**不**耦合两域）：
    * 独立凭据配置（``AgentReadSettings``）；本模块**不** import Workflow
      任何模块（AST 边界测试锁定：stdlib + fastapi + backend.app.config）；
    * 错误文案按 Agent 域命名（未配置 503 文案不同；401 文案与 Workflow
      一致——调用方体验统一）。

纪律（与 S7.2 完全一致）：
    * **fail closed 三重**：
        1) 服务端未配置 key **或** 未配置 project（含纯空白）⇒ **503**；
        2) 缺失 / 错误凭据 ⇒ **401**（固定文案；与资源无关——不泄露目标
           Run 是否存在）；
        3) 授权作用域仅来自服务端配置——不接受 Query / 普通请求头
           （X-Project-ID 等）/ 请求体 / 代理身份头；
    * 恒定时间比较（``secrets.compare_digest``；bytes）；
    * API Key 不进入日志 / 异常 / 响应体——日志只记结果类别；
    * 认证与作用域解析发生在任何数据读取**之前**（FastAPI 依赖先于端点体）。
"""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from typing import Annotated, Final

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from backend.app.config import AgentReadSettings, settings

logger = logging.getLogger(__name__)

__all__ = [
    "AgentReadScope",
    "api_key_header",
    "get_agent_read_settings",
    "require_agent_read_scope",
]

#: 认证请求头（与 Workflow 相同的 ``X-API-Key``；OpenAPI scheme 名一致 ⇒
#: 生成单一 securityScheme，两个域的端点共同引用）。
api_key_header: Final[APIKeyHeader] = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,  # 缺失时由本模块统一返回 401（固定文案）
)

#: 固定错误文案（**不**回传凭据 / 配置细节 / 目标资源信息）。
#: 401 文案与 Workflow Read API 一致（统一调用方体验）；503 按域命名。
_NOT_AUTHENTICATED_DETAIL: Final[str] = "认证失败：缺少或无效的 API Key"
_AUTH_NOT_CONFIGURED_DETAIL: Final[str] = "Agent 读取认证未配置（服务不可用）"


@dataclass(frozen=True)
class AgentReadScope:
    """已认证的读取作用域（授权 ``project_id`` 来自服务端配置）。

    **不**由客户端构造 / 提交——仅由 :func:`require_agent_read_scope`
    在凭据校验通过后产出。
    """

    project_id: str


def get_agent_read_settings() -> AgentReadSettings:
    """Composition Root：认证配置（测试以 monkeypatch 替换本函数）。"""
    return settings.agent_read


def _reject_if_not_configured(config: AgentReadSettings) -> None:
    """未配置（key / project 任一为空或纯空白）⇒ 503 fail closed。"""
    if not config.api_key.strip() or not config.api_project_id.strip():
        # 只记录"未配置"事实——**不**记录任何配置值
        logger.error("agent read auth is not configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_AUTH_NOT_CONFIGURED_DETAIL,
        )


async def require_agent_read_scope(
    api_key: Annotated[str | None, Security(api_key_header)] = None,
) -> AgentReadScope:
    """Agent 两个只读端点统一的认证 + 项目作用域依赖。

    Returns:
        AgentReadScope：授权作用域（project_id 来自服务端配置）。

    Raises:
        HTTPException 503: 服务端认证 / 作用域未配置（fail closed）。
        HTTPException 401: 缺失或错误凭据（固定文案；不泄露资源存在性）。
    """
    config = get_agent_read_settings()
    _reject_if_not_configured(config)
    if api_key is None or not secrets.compare_digest(
        api_key.encode("utf-8"), config.api_key.encode("utf-8")
    ):
        # 只记录"拒绝"事实——**不**记录原始凭据 / 长度 / 前缀
        logger.warning("agent read credential rejected")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_NOT_AUTHENTICATED_DETAIL,
            headers={"WWW-Authenticate": "X-API-Key"},
        )
    return AgentReadScope(project_id=config.api_project_id)
