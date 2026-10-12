"""Workflow **命令** API 认证与项目作用域授权（Phase 7 Step 1）。

与 ``workflow_read_auth`` **同形同机制**（不引入新的认证机制）：

```text
X-API-Key 请求头
    ↓  服务端配置比对（secrets.compare_digest —— 恒定时间）
服务端认证配置（WORKFLOW_COMMAND_API_KEY）
    ↓
服务端授权 project_id（WORKFLOW_COMMAND_API_PROJECT_ID —— **唯一**作用域来源）
    ↓
WorkflowCommandService（start / advance / cancel / approval decision；
                        仅作用于授权作用域内、且经服务端装配的 Runtime）
```

凭据独立性：与读取凭据（``WORKFLOW_READ_API_KEY`` / ``AGENT_READ_API_KEY``）
**独立**——读写最小权限分离、可分别轮换；部署层可将两者配置为相同值。

纪律（与 S7.2 / S8A 完全一致）：
    * **fail closed 三重**：
        1) 服务端未配置 key **或** 未配置 project ⇒ **503**（服务不可用；
           **绝不**退化为匿名可访问，也**绝不**回退到默认项目）；
        2) 缺失 / 错误凭据 ⇒ **401**（固定文案；与资源无关——不泄露目标
           Run / Approval 是否存在）；
        3) 授权作用域为空 / 空白 ⇒ 视同未配置 ⇒ 503。
    * 授权作用域**只**来自服务端配置——不接受 Query 参数、普通请求头
      （如 X-Project-ID）、请求体指定；不信任任何未经验证的代理身份头；
    * 凭据比较使用 ``secrets.compare_digest``（bytes；恒定时间）；
    * API Key **不进入**日志 / 异常 / 响应体——日志只记录结果类别
      （rejected / not configured），**绝不**记录原始凭据；
    * 认证与作用域解析发生在任何命令执行**之前**（FastAPI 依赖在端点体
      执行前解析）——未认证请求不会触发 Runtime / 外部调用 / DB 写入。

边界（AST 测试锁定）：仅 import stdlib + fastapi + backend.app.config；
不 import db / Repository / services / tools / agent / llm。
"""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from typing import Annotated, Final

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from backend.app.config import WorkflowCommandSettings, settings

logger = logging.getLogger(__name__)

__all__ = [
    "WorkflowCommandScope",
    "api_key_header",
    "get_workflow_command_settings",
    "require_workflow_command_scope",
]

#: 认证请求头（OpenAPI security scheme 的依据；命令端点共用）。
api_key_header: Final[APIKeyHeader] = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,  # 缺失时由本模块统一返回 401（固定文案）
)

#: 固定错误文案（**不**回传凭据 / 配置细节 / 目标资源信息）。
_NOT_AUTHENTICATED_DETAIL: Final[str] = "认证失败：缺少或无效的 API Key"
_AUTH_NOT_CONFIGURED_DETAIL: Final[str] = "Workflow 命令认证未配置（服务不可用）"


@dataclass(frozen=True)
class WorkflowCommandScope:
    """已认证的命令作用域（授权 ``project_id`` 来自服务端配置）。

    **不**由客户端构造 / 提交——仅由 :func:`require_workflow_command_scope`
    在凭据校验通过后产出。
    """

    project_id: str


def get_workflow_command_settings() -> WorkflowCommandSettings:
    """Composition Root：认证配置（测试以 monkeypatch 替换本函数）。"""
    return settings.workflow_command


def _reject_if_not_configured(config: WorkflowCommandSettings) -> None:
    """未配置（key / project 任一为空或纯空白）⇒ 503 fail closed。"""
    if not config.api_key.strip() or not config.api_project_id.strip():
        # 只记录"未配置"事实——**不**记录任何配置值
        logger.error("workflow command auth is not configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_AUTH_NOT_CONFIGURED_DETAIL,
        )


async def require_workflow_command_scope(
    api_key: Annotated[str | None, Security(api_key_header)] = None,
) -> WorkflowCommandScope:
    """命令端点统一的认证 + 项目作用域依赖。

    Returns:
        WorkflowCommandScope：授权作用域（project_id 来自服务端配置）。

    Raises:
        HTTPException 503: 服务端认证 / 作用域未配置（fail closed）。
        HTTPException 401: 缺失或错误凭据（固定文案；不泄露资源存在性）。
    """
    config = get_workflow_command_settings()
    _reject_if_not_configured(config)
    if api_key is None or not secrets.compare_digest(
        api_key.encode("utf-8"), config.api_key.encode("utf-8")
    ):
        # 只记录"拒绝"事实——**不**记录原始凭据 / 长度 / 前缀
        logger.warning("workflow command credential rejected")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_NOT_AUTHENTICATED_DETAIL,
            headers={"WWW-Authenticate": "X-API-Key"},
        )
    return WorkflowCommandScope(project_id=config.api_project_id)
