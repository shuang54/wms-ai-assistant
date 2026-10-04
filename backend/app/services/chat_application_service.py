"""Chat Application Service（Phase 4.1 Step 11 —— 最小 workflow composition）。

设计来源：Phase 4.1 Step 10（Chat / Application Layer Boundary Audit，方案 B 冻结）

```text
ChatApplicationService
        ├── ConversationService        → Conversation / Turn persistence
        └── AIOrchestrator             → AI execution
```

唯一职责 = **workflow composition**（不含任何 AI / SQL / prompt / RAG / Tool 逻辑）：

    1. get_conversation              （不存在 → ConversationNotFoundError）
    2. 状态守卫                      （ARCHIVED → ConversationArchivedError；AI = 0 次）
    3. append USER turn              （assistant_request_id = None；Repository 事务提交）
    4. list_turns(conversation_id)   （Step 14：读取历史；失败 → AI = 0，异常原样上抛）
    5. 排除 current USER turn        （按 append 返回的 turn_id **精确排除**，不猜 turns[-1]）
    6. build_context(previous_turns) （Step 13 Context Builder **唯一入口**；纯内存；
                                        失败 → AI = 0，异常原样上抛）
    7. orchestrator.execute(question, context=context)（**DB 事务之外**；
                                        question = 当前 USER turn 内容 —— 不同时进 context）
    8. 条件式 append ASSISTANT turn  （仅当存在可展示 content 且 metadata.request_id 合法）
    9. 原样返回 AIOrchestrationResult

Context 语义（Step 13 / Step 14 冻结）：

    * ``context`` 只包含**历史**（previous turns）；首个 turn →
      ``build_context(())`` → ``None``（不伪造 "" / "None" / 当前问题）；
    * 当前问题只作为 ``question`` 传给 Orchestrator（**不**重复进 context）；
    * Context Builder 只读 role / content（不读 turn_id / conversation_id /
      assistant_request_id / created_at）；
    * ChatApplicationService **不自己拼字符串**（不调用 build_messages /
      不手工 join）—— 唯一入口 = ``build_context``。

事务边界（Step 10 §5 / Step 14 冻结）：

    TX1: append USER turn → COMMIT
    history read（list_turns；commit 之后，纯读）
    Context Builder（纯内存，无 IO）
    AI execution（outside transaction）
    TX2: append ASSISTANT turn → COMMIT

真实接口事实（Step 11 阅读代码确认，**不修改**任何既有接口）：

    * ``AIOrchestratorService.execute(question, *, context=None)`` **没有**
      project_id 参数 —— project 绑定发生在 Orchestrator **构造期**
      （``build_orchestrator_for_project(project_id, base=...)``）。
      因此本服务通过 ``orchestrator_factory(project_id)`` 表达
      ``conversation.project_id → AI execution project_id``；
      **禁止**任何硬编码的项目名 / 默认项目回退。
    * ``assistant_request_id`` 的唯一生成点是 ``AIOrchestratorService.execute()``
      （``new_request_id()`` ⇒ ``metadata["request_id"]``）。
      本服务**不生成**任何 ID（无 uuid / 无第二套 ID generator）。
    * USER turn 的 ``assistant_request_id`` 必须为 None；
      ASSISTANT turn 的 ``assistant_request_id`` 必须非空
      （由 ConversationService 校验，本服务不重复实现）。

Content 语义（Step 2 §8 / Step 10 §7 冻结）：

    ConversationTurn.content 只保存 USER 输入 / Assistant 最终可展示回答；
    **不得**保存 metadata / SQL / prompt / RAG chunks / Tool raw result /
    LLM raw response（Conversation history ≠ observability store）。

失败语义（Step 10 §7 冻结）：

    * AI 抛异常 → USER turn 保留（不回滚），不创建 ASSISTANT turn，
      异常**原样**上抛（不包装 / 不 retry）；
    * AI 返回无可展示 content（None / 空白）→ 不创建 ASSISTANT turn
      （**不伪造** "[EMPTY]" 之类内容）；
    * content 存在但 metadata.request_id 缺失/非法 → 不创建 ASSISTANT turn
      （不伪造 ID；记 warning，仍返回原 result）。

不在本层（Deferred）：Memory / Summary / Token Budget / Regenerate /
Auth / Pagination / Prompt 模板 / Context 缓存。
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Final, Protocol

from backend.app.services.ai_orchestrator_service import AIOrchestrationResult
from backend.app.services.conversation_context_builder import (
    ConversationContextBuilder,
)
from backend.app.services.conversation_service import (
    ConversationArchivedError,
    ConversationNotFoundError,
    ConversationService,
)

__all__ = [
    "ChatApplicationService",
    "ChatOrchestrator",
    "OrchestratorFactory",
    "ASSISTANT_ROLE",
    "USER_ROLE",
    "CONVERSATION_STATUS_ARCHIVED",
    "REQUEST_ID_METADATA_KEY",
]

logger = logging.getLogger(__name__)

#: Turn role 字面量（= 持久化层 :data:`TURN_ROLE_USER` / :data:`TURN_ROLE_ASSISTANT`；
#: 本模块**不 import** ``backend.app.db`` —— 一致性由契约测试锁定）。
USER_ROLE: Final[str] = "USER"
ASSISTANT_ROLE: Final[str] = "ASSISTANT"

#: Conversation 归档状态字面量（= 持久化层 ``CONVERSATION_STATUS_ARCHIVED``）。
CONVERSATION_STATUS_ARCHIVED: Final[str] = "ARCHIVED"

#: Assistant Trace ID 在 AIOrchestrationResult.metadata 中的 key（Step 10 §6 冻结）。
REQUEST_ID_METADATA_KEY: Final[str] = "request_id"


class ChatOrchestrator(Protocol):
    """AI execution 的最小协议（真实实现 = ``AIOrchestratorService``）。"""

    async def execute(
        self,
        question: str,
        *,
        context: str | None = None,
    ) -> AIOrchestrationResult: ...


#: project_id → 绑定该项目数据源的 Orchestrator（构造期绑定，Step 10 §8）。
OrchestratorFactory = Callable[[str], ChatOrchestrator]


def _default_orchestrator_factory(project_id: str) -> ChatOrchestrator:
    """默认 factory：按 ``project_id`` 构造绑定项目数据源的 Orchestrator。

    延迟 import（保持模块加载不创建基础设施）；未注册 project_id →
    ``ProjectNotFoundError`` 原样上抛（由上层映射，本层不包装）。

    生产接线说明：组合根（API 层）应注入复用观测 base 的 factory
    （既有模式见 ``api/orchestrator_chat.py`` 的 ``_default_orchestrator``）——
    本默认实现仅保证"按 conversation.project_id 构造"这一语义，
    不改变任何既有装配。
    """
    from backend.app.services.project_orchestrator_factory import (
        build_orchestrator_for_project,
    )

    return build_orchestrator_for_project(project_id)


def _assistant_content(result: AIOrchestrationResult) -> str | None:
    """可展示给用户的最终内容（None / 空白 → 不创建 ASSISTANT turn）。"""
    content = result.content
    if isinstance(content, str) and content.strip():
        return content
    return None


def _assistant_request_id(result: AIOrchestrationResult) -> str | None:
    """从 ``metadata["request_id"]`` 取 Assistant Trace ID（**不生成**新 ID）。"""
    value = result.metadata.get(REQUEST_ID_METADATA_KEY)
    if isinstance(value, str) and value.strip():
        return value
    return None


class ChatApplicationService:
    """Conversation + AI 的最小 workflow composition（Application Layer）。"""

    def __init__(
        self,
        *,
        conversation_service: ConversationService | None = None,
        orchestrator_factory: OrchestratorFactory | None = None,
        context_builder: ConversationContextBuilder | None = None,
    ) -> None:
        """构造 Application Service。

        Args:
            conversation_service: 会话生命周期服务；None 时构造默认
                ``ConversationService()``（构造期**不连接数据库**）。
            orchestrator_factory: ``project_id → Orchestrator`` 工厂；
                None 时使用 ``_default_orchestrator_factory``
                （延迟 import 既有 per-project 工厂）。
            context_builder: 历史 → context 转换器（Step 13 实现）；
                None 时构造默认 ``ConversationContextBuilder()``
                （无状态 / 纯内存 / 无 IO）。
        """
        self._conversations = (
            conversation_service
            if conversation_service is not None
            else ConversationService()
        )
        self._orchestrator_factory: OrchestratorFactory = (
            orchestrator_factory
            if orchestrator_factory is not None
            else _default_orchestrator_factory
        )
        self._context_builder = (
            context_builder
            if context_builder is not None
            else ConversationContextBuilder()
        )

    # ---------- 只读暴露（便于测试 / 装配断言） ----------

    @property
    def conversation_service(self) -> ConversationService:
        """底层会话服务（持久化权威）。"""
        return self._conversations

    @property
    def orchestrator_factory(self) -> OrchestratorFactory:
        """``project_id → Orchestrator`` 工厂（构造期项目绑定）。"""
        return self._orchestrator_factory

    @property
    def context_builder(self) -> ConversationContextBuilder:
        """历史 → context 转换器（Step 13 Context Builder）。"""
        return self._context_builder

    # ---------- 对外 Contract ----------

    async def execute_message(
        self,
        *,
        conversation_id: str,
        content: str,
    ) -> AIOrchestrationResult:
        """执行一条会话消息（USER turn → AI → 条件式 ASSISTANT turn）。

        Raises:
            ValueError:                    conversation_id / content 非法
                                          （由 ConversationService 校验；AI = 0 次）。
            ConversationNotFoundError:     会话不存在（AI = 0 次）。
            ConversationArchivedError:     会话已归档（AI = 0 次；**写入前**拒绝）。
            ConversationRepositoryError:   持久化失败（USER 失败 → AI = 0 次；
                                          history 读失败 → AI = 0 次，USER 保留；
                                          ASSISTANT 失败 → AI = 1 次，不重试）。
            ValueError:                    Context Builder 拒绝（历史出现未知 role /
                                          非法字段）→ AI = 0 次，USER 保留，
                                          异常原样上抛（不包装 / 不降级）。
            Exception:                     AIOrchestrator 的异常**原样**上抛
                                          （USER turn 已提交保留；不创建
                                          ASSISTANT turn；不包装 / 不 retry）。
        """
        conversation = self._conversations.get_conversation(conversation_id)
        if conversation is None:
            raise ConversationNotFoundError(
                f"conversation 不存在: {conversation_id}"
            )
        if conversation.status == CONVERSATION_STATUS_ARCHIVED:
            raise ConversationArchivedError(
                f"conversation 已归档: {conversation.conversation_id}"
            )

        # TX1：USER turn（Repository 内提交；AI 执行前完成）
        user_turn = self._conversations.append_turn(
            conversation_id=conversation.conversation_id,
            role=USER_ROLE,
            content=content,
            assistant_request_id=None,
        )

        # 历史读（TX1 commit 之后；纯读）——
        # 读取失败：AI = 0，异常原样上抛（**不**降级为 [] / context=None）。
        history = self._conversations.list_turns(conversation.conversation_id)
        # 排除 current USER turn：按 append 返回的 turn_id 精确排除
        # （不依赖 turns[-1] 猜测）。
        previous_turns = tuple(
            turn for turn in history if turn.turn_id != user_turn.turn_id
        )
        # Context Builder（Step 13；唯一入口 build_context；纯内存）——
        # 首个 turn → previous_turns == () → context = None；
        # 构建失败：AI = 0，异常原样上抛（**不**包装 / **不**降级）。
        context = self._context_builder.build_context(previous_turns)

        # AI execution（**事务之外**；project = conversation.project_id；
        # question = 当前问题，context = 仅历史 —— 当前问题不重复进 context）
        orchestrator = self._orchestrator_factory(conversation.project_id)
        result = await orchestrator.execute(user_turn.content, context=context)

        # TX2：条件式 ASSISTANT turn（仅"有可展示内容 + 有合法 Trace ID"）
        assistant_content = _assistant_content(result)
        if assistant_content is None:
            logger.info(
                "assistant content 为空，不创建 ASSISTANT turn"
                "（conversation_id=%s, route=%s）",
                conversation.conversation_id,
                getattr(result.route, "value", result.route),
            )
            return result

        assistant_request_id = _assistant_request_id(result)
        if assistant_request_id is None:
            logger.warning(
                "AIOrchestrationResult.metadata 缺少合法 request_id，"
                "不创建 ASSISTANT turn（conversation_id=%s）—— 不伪造 ID",
                conversation.conversation_id,
            )
            return result

        self._conversations.append_turn(
            conversation_id=conversation.conversation_id,
            role=ASSISTANT_ROLE,
            content=assistant_content,
            assistant_request_id=assistant_request_id,
        )
        return result
