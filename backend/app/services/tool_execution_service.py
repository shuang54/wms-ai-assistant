"""Tool Execution Service（Phase 3.11 Step 2）——最小 Tool 执行边界。

背景（Phase 3.11 Step 1 勘察结论）：
    ``AIOrchestratorService._run_tool`` 曾混合 5 件事：Tool 选择 /
    能力校验 / 参数构造 / Registry 执行 / 结果转换。本模块只把其中
    **执行边界**抽出来，形成唯一应用层 Tool 执行入口：

        tool_name + arguments（已解析 / 已构造）
            ↓
        ToolExecutionService.execute()
            ├── capability 校验（项目白名单；被拒 → Handler 0 次调用）
            ↓
        ToolRegistry.execute()        ← Tool 执行权威（不变）
            ├── validate_arguments()
            ├── Handler.__call__
            └── 异常归一化 → ToolResult
            ↓
        ToolResult（原样返回）

Phase 3.11 Step 13：``execute()`` 可接收 **ToolExecutionContext**
（``request_id`` / ``project_id`` / ``tool_call_id`` / ``round``）：

    ToolChatService（创建 Context）
        ↓
    ToolExecutionService.execute(..., context=Context)   ← 接收并校验
        ├── context 类型 / 作用域一致性校验
        ├── capability 校验
        ↓
    ToolRegistry.execute(tool_name, arguments)             ← **不接收** Context
        ↓
    Tool Handler(arguments)                                ← **不接收** Context

    * Context 是 **Runtime Execution Context**（观测 / 关联用），
      **不**进入 Tool arguments、**不**进入 LLM messages；
    * 本组件**不生成** request_id（创建者 = ToolChatService）、
      **不修改**传入 Context（frozen DTO；每轮由调用方创建新实例）、
      **不持久化**（无 audit / 无 log table / 无 tracing backend）、
      **不重试**；
    * Context 与授权作用域必须一致：``context.project_id`` 与
      构造期 ``project_id`` 不符 → ``ValueError``（防止调用方伪造作用域；
      LLM 无法影响 project_id）。

Phase 3.11 Step 15：``execute()`` 可把一次执行产生的 **ToolExecutionRecord**
交给**可选**进程内 observer（Observability Boundary）：

    execute(tool_name, arguments, context)
        ├── started_at = now_utc() / timer = perf_counter()
        ├── ToolRegistry.execute(...)              → ToolResult（原样返回）
        ├── finished_at = now_utc() / duration_ms = elapsed_ms(timer)
        ↓  from_execution(context, tool_name, result, timing)
    ToolExecutionRecord（frozen）
        ↓  observer.on_execution(record)           （observer=None → 不产生）
    return ToolResult                              （**契约不变 / 原样**）

    * observer 是**可选**构造参数（None = 旧行为，零开销：不计时、不建 Record）；
    * 只有存在 Execution Context 的执行才产生事件
      （context=None → 0 事件；边界**不**自行生成 request_id）；
    * **Observer / Record 失败隔离**：构建或回调中的任何异常只记 warning
      （``error_type`` + ``tool_name``；**不**写 exception message / traceback），
      绝不改变 ``ToolResult``、绝不 retry、绝不 fallback、绝不重跑 Tool；
    * capability 拒绝 / Registry 抛异常 → **0 事件**（未发生执行）；
    * 零持久化：无 audit table / metrics 聚合 / dashboard / tracing backend /
      EventBus / 消息队列（只定义 Protocol + 单次同步回调）。

职责边界（务必阅读）：
    * **不接受 question**：本模块不做 Tool 选择，不新增第三套选择
      算法（Router / Orchestrator 的现有选择逻辑本阶段不处理）；
    * **不构造参数**：参数构造属调用方（Orchestrator 调用
      ``ToolArgumentExtractor``；Phase 3.11 Step 6 起），本组件保持不变；
    * **不接触 Handler**：执行权力仍完全属于 `ToolRegistry.execute()`
      （Schema 校验 / 参数拒绝 / 安全边界 全部不绕过）；
    * **不重新包装 ToolResult**：Registry 返回什么就原样返回什么；
    * **不捕获 / 不吞异常**：capability 拒绝 → `AIOrchestratorCapabilityError`
      （延迟导入，保持既有 HTTP 403 语义不变）；Registry / Handler
      异常按既有机制传播（Registry 已把 ToolError 归一为
      `ToolResult(success=False)`）；
    * **无状态**：无缓存 / 无重试 / 无 fallback / 无 loop / 无多步。

安全边界：
    NO SQL / NO DB Session / NO Engine / NO Connection / NO HTTP /
    NO Shell / NO File IO / NO LLM / NO API Key / NO Password。
    唯一依赖：``ToolRegistry``（执行）+ ``ProjectCapabilities``
    （纯配置 DTO，只描述不执行）。

明确不做（Phase 3.11 Step 2）：
    Agent / LangGraph / MCP / Memory / Planning / Multi-Agent /
    自主 Loop / Tool 选择统一 / 自然语言参数解析 /
    ToolChatService 迁移 / 通用 JSON Schema 推理 / Retry / Cache。

依赖方向：
    AIOrchestrator → ToolExecutionService → ToolRegistry → Handler
    （本模块不依赖 Router / RAG / Text-to-SQL / API 层）
"""
from __future__ import annotations

import logging
import time
from typing import Any

from backend.app.projects.capabilities import ProjectCapabilities
from backend.app.services.tool_execution_context import ToolExecutionContext
from backend.app.services.tool_execution_observer import ToolExecutionObserver
from backend.app.services.tool_execution_record import (
    ToolExecutionRecord,
    elapsed_ms,
    now_utc,
)
from backend.app.tools.registry import ToolRegistry, ToolResult

logger = logging.getLogger(__name__)

__all__ = [
    "ToolExecutionService",
]


class ToolExecutionService:
    """应用层 Tool 执行边界：capability 校验 + ToolRegistry 执行。

    执行链（唯一职责）：

        tool_name
            ↓ _check_capability（abilities 白名单；None = 不限制）
        tool_name
            ↓ await registry.execute(tool_name, arguments=arguments)
        ToolResult（原样返回；参数校验 / Handler / 异常归一化均在 Registry）

    用法：

        service = ToolExecutionService(
            registry=tool_registry,
            capabilities=project_capabilities,   # None = 不限制（旧行为）
            project_id="vietnam-wms",            # 仅用于错误信息
        )
        result = await service.execute(
            "get_inventory", arguments={"material_code": "MAT-001"}
        )

    Args:
        registry:     Tool 注册中心（必须提供可调用的 ``execute()``；
                      生产传入 ``ToolRegistry``，测试可注入 Fake）。
        capabilities: 项目能力配置（frozen DTO）；None = 不限制
                      （与 Orchestrator 既有语义一致）。
        project_id:   项目 ID；仅用于 capability 拒绝错误的上下文
                      （尽力而为，可为 None）。
        observer:     Phase 3.11 Step 15 —— 可选的执行观测出口
                      （``ToolExecutionObserver``：只接收
                      ``ToolExecutionRecord``）。None = 不产生任何事件
                      （旧行为；且不计时 / 不建 Record）。

    Raises:
        TypeError: registry 为 None 或缺少 ``execute()``；
                   capabilities / project_id 类型非法；
                   observer 缺少可调用的 ``on_execution()``。
    """

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        capabilities: ProjectCapabilities | None = None,
        project_id: str | None = None,
        observer: ToolExecutionObserver | None = None,
    ) -> None:
        if registry is None:
            raise TypeError("registry 不能为 None")
        if not callable(getattr(registry, "execute", None)):
            raise TypeError(
                "registry 必须提供可调用的 execute()"
                f"（got {type(registry).__name__}）"
            )
        if capabilities is not None and not isinstance(
            capabilities, ProjectCapabilities
        ):
            raise TypeError(
                "capabilities 必须是 ProjectCapabilities 实例或 None"
                f"（got {type(capabilities).__name__}）"
            )
        if project_id is not None and not isinstance(project_id, str):
            raise TypeError(
                "project_id 必须是 str 或 None"
                f"（got {type(project_id).__name__}）"
            )
        if observer is not None and not callable(
            getattr(observer, "on_execution", None)
        ):
            raise TypeError(
                "observer 必须提供可调用的 on_execution()"
                f"（got {type(observer).__name__}）"
            )
        self._registry = registry
        self._capabilities = capabilities
        self._project_id = project_id
        self._observer = observer

    # ---------- 只读暴露（便于测试断言注入关系） ----------

    @property
    def registry(self) -> ToolRegistry:
        """底层 Tool Registry（执行权威）。"""
        return self._registry

    @property
    def capabilities(self) -> ProjectCapabilities | None:
        """项目能力配置（None = 不限制）。"""
        return self._capabilities

    @property
    def project_id(self) -> str | None:
        """项目 ID（仅用于 capability 错误上下文）。"""
        return self._project_id

    @property
    def observer(self) -> ToolExecutionObserver | None:
        """执行观测出口（None = 不产生事件）。"""
        return self._observer

    # ---------- 对外 Contract ----------

    async def execute(
        self,
        tool_name: str,
        *,
        arguments: dict[str, Any] | None = None,
        context: ToolExecutionContext | None = None,
    ) -> ToolResult:
        """执行 Tool：Context 校验 → capability 校验 → ``ToolRegistry.execute()``。

        Args:
            tool_name: 已解析的 Tool 名称（本模块不做选择）。
            arguments: 已构造的参数；None → 原样传给 Registry
                       （Registry 按 ``{}`` 处理）。
                       **只含业务参数**：Execution Context 不写入 arguments。
            context:   Phase 3.11 Step 13 —— 本次执行的运行时上下文
                       （``ToolExecutionContext``；由 ToolChatService 创建）。
                       ``None`` = 调用方未提供（链路 A / 旧调用形态保持可用）。
                       语义：
                           * 仅作为**执行层**上下文接收（校验 + 可观测点）；
                           * Registry / Handler **看不到**它
                             （Registry 签名不变：``execute(tool_name,
                             arguments)``；Handler 仍只接收 ``arguments``）；
                           * 本组件不生成 request_id、不修改 Context
                             （frozen DTO；每轮由调用方创建新实例）、
                             不持久化、不 retry；
                           * Phase 3.11 Step 15：``context`` 是产生
                             ``ToolExecutionRecord`` 的**前提**
                             （``context=None`` → observer 0 事件）。

        Returns:
            ``ToolResult``（Registry 返回值原样透传，不重新包装）：
                * 成功 → ``success=True, data=<handler 返回值>``
                * 参数校验失败 / Tool 未注册 / Handler 失败 →
                  ``success=False, error=<reason>``（Registry 归一化）
                * observer / Record 失败**不**改变返回值（隔离于执行结果）

        Raises:
            TypeError:  context 类型非法（非 ToolExecutionContext / None）。
            ValueError:  context.project_id 与构造期 project_id 不一致
                （Context 必须反映真实授权作用域；Registry 0 次调用）。
            AIOrchestratorCapabilityError: 该项目 capability 不允许该
                Tool（Handler 0 次调用；HTTP 层保持 403 语义；observer 0 事件）。
            Exception: Registry / Handler 抛出的未归一化异常原样传播
                （不吞、不包装、不 retry；无 ToolResult → observer 0 事件）。
        """
        self._check_context(context)
        self._check_capability(tool_name)
        # Phase 3.11 Step 15：只有「有 Context + 有 observer」的执行才计时 /
        # 建 Record（无 observer → 零开销，旧行为逐字不变）。
        emit_record = context is not None and self._observer is not None
        if emit_record:
            started_at = now_utc()
            timer_started = time.perf_counter()
        result = await self._registry.execute(tool_name, arguments=arguments)
        if emit_record:
            self._emit_record(
                context=context,  # type: ignore[arg-type]
                tool_name=tool_name,
                result=result,
                started_at=started_at,
                timer_started=timer_started,
            )
        return result

    # ---------- 内部：Observability（Phase 3.11 Step 15） ----------

    def _emit_record(
        self,
        *,
        context: ToolExecutionContext,
        tool_name: str,
        result: ToolResult,
        started_at: Any,
        timer_started: float,
    ) -> None:
        """构建 ``ToolExecutionRecord`` 并交给 observer（失败完全隔离）。

        隔离语义（Step 15 §十）：

            * Record 构建失败 或 observer 抛出任何异常 →
              只记 warning（``tool_name`` + ``error_type``；
              **不**写 exception message / traceback）→ 静默丢弃；
            * 绝不改变 ``ToolResult``、绝不 retry / fallback / 重跑 Tool；
            * 不捕获 ``BaseException``（KeyboardInterrupt / SystemExit 照旧传播）。
        """
        try:
            record = ToolExecutionRecord.from_execution(
                context=context,
                tool_name=tool_name,
                result=result,
                started_at=started_at,
                finished_at=now_utc(),
                duration_ms=elapsed_ms(timer_started),
            )
            observer = self._observer
            if observer is None:  # 防御（构造后不可变；仅静态收窄）
                return
            observer.on_execution(record)
        except Exception as exc:  # noqa: BLE001 — 观测失败必须隔离
            logger.warning(
                "tool execution observer failed",
                extra={
                    "tool_name": tool_name,
                    "error_type": type(exc).__name__,
                },
            )

    # ---------- 内部：Execution Context 校验（Phase 3.11 Step 13） ----------

    def _check_context(self, context: ToolExecutionContext | None) -> None:
        """校验执行上下文类型与作用域一致性（不改写 Context）。

        * 类型：``ToolExecutionContext | None``（否则 ``TypeError``）；
        * 作用域：``context.project_id`` 必须等于构造期的 ``project_id``
          （授权作用域唯一权威 = 服务器端 ProjectRegistry 解析结果）——
          不一致 → ``ValueError``，防止调用方把 A 项目的授权作用域
          伪造成 B 项目（LLM 无法影响该字段：它不来自 arguments）。
        """
        if context is None:
            return
        if not isinstance(context, ToolExecutionContext):
            raise TypeError(
                "context 必须是 ToolExecutionContext 实例或 None"
                f"（got {type(context).__name__}）"
            )
        if context.project_id != self._project_id:
            raise ValueError(
                "context.project_id 必须与执行边界的授权作用域一致"
                f"（context={context.project_id!r}, "
                f"scope={self._project_id!r}）"
            )

    # ---------- 内部：capability 校验 ----------

    def _check_capability(self, tool_name: str) -> None:
        """工具能力硬校验（Phase 3.8.2 语义原样迁移）。

        项目工具白名单不含该 Tool → 抛 ``AIOrchestratorCapabilityError``
        （与迁移前 Orchestrator 抛出的异常**类型 / 消息 / capability /
        project_id 完全一致**，HTTP 403 语义不变）。

        延迟导入原因：``AIOrchestratorCapabilityError`` 定义在
        ``ai_orchestrator_service``（其 import 本模块），模块级导入会
        形成循环依赖；该异常是唯一需要保持的共享类型。
        """
        if self._capabilities is None:
            return  # 不限制（旧行为 / 默认 Orchestrator）
        if not self._capabilities.allows_tool(tool_name):
            from backend.app.services.ai_orchestrator_service import (
                AIOrchestratorCapabilityError,
            )

            raise AIOrchestratorCapabilityError(
                f"Tool {tool_name!r} 未在该项目启用",
                capability=tool_name,
                project_id=self._project_id,
            )
