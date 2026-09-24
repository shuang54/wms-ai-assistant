"""AI Router（Phase 3.7.8）。

职责：
    根据用户问题 + 能力元数据，决定问题应进入哪一种 AI 能力：

        RAG          知识 / 流程 / 操作说明 / 业务规则 / 配置 / 故障
        TOOL         已注册的固定业务能力（仅元数据匹配，不调用）
        TEXT_TO_SQL  数据分析 / 统计 / 聚合 / 排序 / 过滤

策略：
    1. **Rule-first**（确定性规则，无需 LLM）
       - RAG 特征：业务术语知识词 + 流程/操作/规则提问结构
       - TOOL 特征：命中已注册 Tool capability 的别名/描述
       - TEXT_TO_SQL 特征：统计 / 聚合动词 + 业务数据术语
    2. **LLM fallback**（一次调用，受 AI_ROUTER_LLM_FALLBACK_ENABLED 控制）
       - 复用现有 ``LLMClient.chat(messages)``，无新 LLM 客户端
       - 输出严格 JSON 解析，非法 / 越权 route 直接拒绝
    3. **Conservative fallback**：无法归类 → RAG（拒绝盲打 Text-to-SQL
       进入数据库的安全边界）

不做什么（边界）：
    ❌ 不调用 TextToSQL 生成链路（生成器 / 校验器 / 执行器）
    ❌ 不调用 RAG 检索
    ❌ 不调用 Tool Handler
    ❌ 不修改 Chat / API / Router（FastAPI）路由
    ❌ 不暴露 LLM / API Key / DB URL / 内部路径
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from string import Template
from typing import Any, Protocol

from backend.app.config import settings
from backend.app.llm.client import LLMClient, LLMError

logger = logging.getLogger(__name__)

__all__ = [
    "RouteType",
    "RouteDecision",
    "ToolCapability",
    "ToolCapabilityRegistry",
    "InMemoryToolCapabilityRegistry",
    "ToolRegistryCapabilityAdapter",
    "AIRouter",
    "AIRouterService",
    "AIRouterError",
    "AIRouterInputError",
    "AIRouterClassificationError",
    "get_default_router",
]


# ============================================================
# 枚举 / DTO
# ============================================================

class RouteType(str, Enum):
    """Router 三种合法路由（显式 enum，禁止 LLM 输出其他值）。"""

    RAG = "rag"
    TOOL = "tool"
    TEXT_TO_SQL = "text_to_sql"


@dataclass(frozen=True)
class RouteDecision:
    """路由决策结果（frozen，无副作用）。

    Attributes:
        route:       路由目标。
        confidence:  规则命中时的固定高置信度；LLM 路径为 None
                     （LLM 不提供稳定数值置信度，避免误导上层）。
        reason:      可解释依据（<= 30 词；不含敏感信息）。
        source:      "rule" / "tool_match" / "llm" / "fallback"。
    """

    route: RouteType
    confidence: float | None
    reason: str | None
    source: str = "rule"


@dataclass(frozen=True)
class ToolCapability:
    """Tool 能力元数据（**不**包含 handler / 执行参数 / 业务数据）。

    Attributes:
        name:        Tool 唯一名称。
        description: Tool 用途自然语言描述。
        aliases:     触发短语（同义说法），用于 Router 关键词匹配。
    """

    name: str
    description: str
    aliases: tuple[str, ...] = ()


# ============================================================
# Tool Capability Registry（只读元数据，Router 不执行 Tool）
# ============================================================

class ToolCapabilityRegistry(Protocol):
    """Tool 能力元数据抽象（Router 只读取，调用方可注入项目）。"""

    def list_capabilities(self) -> Sequence[ToolCapability]: ...


class InMemoryToolCapabilityRegistry:
    """最小内存实现：用于测试 / 未接入项目 ToolRegistry 的场景。"""

    def __init__(self, capabilities: Sequence[ToolCapability] = ()) -> None:
        self._capabilities: tuple[ToolCapability, ...] = tuple(
            c if isinstance(c, ToolCapability) else ToolCapability(*c)
            for c in capabilities
        )

    def list_capabilities(self) -> Sequence[ToolCapability]:
        return self._capabilities


class ToolRegistryCapabilityAdapter:
    """把已有 ``ToolRegistry`` 适配为 ``ToolCapabilityRegistry``。

    不暴露 handler / parameters（只取字段名做别名），保护 Tool 边界。
    """

    def __init__(self, registry: Any) -> None:
        self._registry = registry

    def list_capabilities(self) -> Sequence[ToolCapability]:
        try:
            definitions = self._registry.list_definitions()
        except Exception:
            logger.warning("ToolRegistry list_definitions failed",
                           exc_info=True)
            return ()
        out: list[ToolCapability] = []
        for d in definitions:
            name = getattr(d, "name", None)
            description = getattr(d, "description", None)
            if not isinstance(name, str) or not name:
                continue
            if not isinstance(description, str):
                description = ""
            params = getattr(d, "parameters", None) or {}
            aliases = _extract_param_aliases(name, params)
            out.append(ToolCapability(
                name=name, description=description, aliases=aliases
            ))
        return tuple(out)


# ============================================================
# Router Protocol
# ============================================================

class AIRouter(Protocol):
    """AI Router 协议（Phase 3.7.8）。"""

    async def route(
        self,
        question: str,
        *,
        context: str | None = None,
    ) -> RouteDecision: ...


# ============================================================
# 异常体系
# ============================================================

class AIRouterError(Exception):
    """AI Router 通用异常。"""


class AIRouterInputError(AIRouterError):
    """输入非法（空 question / 非 str）。"""


class AIRouterClassificationError(AIRouterError):
    """LLM fallback 失败（API 错误 / 非法 JSON / 越权 route 等）。"""


# ============================================================
# 规则匹配（纯函数，单测友好）
# ============================================================

_RAG_KNOWLEDGE_PATTERNS = (
    r"怎么.{0,4}(操作|做|使用|配置|登录|进入|查看)",
    r"(流程|步骤|操作指南|操作手册|审批流|权限)",
    r"(什么是|是什么|区别|含义|定义)",
    r"(故障|报错|异常处理|不能.{0,4}(提交|保存|过账))",
    r"为什么.{0,4}(不能|会|要|需要)",
)

_RAG_KNOWLEDGE_HINTS = (
    "流程", "步骤", "怎么", "如何", "为什么", "原因", "规则",
    "规范", "规定", "审批", "权限", "作用", "功能", "配置",
    "设置", "在哪里", "哪个菜单", "是什么", "什么是", "区别",
    "故障", "报错", "异常处理", "操作指南", "说明",
)

_ANALYTICS_VERBS = (
    "多少", "几个", "几条", "统计", "汇总", "求和", "平均",
    "最高", "最低", "最多", "最少", "最大", "最小", "排序",
    "占比", "趋势", "查询", "显示", "列出",
)

_DATA_SUBJECTS = (
    "库存", "入库", "出库", "调拨", "盘点", "采购", "销售",
    "订单", "物料", "仓库", "库位", "供应商", "客户", "单据",
    "批次", "序列号", "流水", "金额", "金额合计", "数量合计",
)

_PROMPTS_DIR: Path = Path(__file__).resolve().parent.parent / "prompts"


def _looks_like_knowledge(question: str) -> bool:
    """知识 / 流程 / 操作特征（纯词面）。

    若问题同时满足数据分析特征，**分析优先**——避免
    "库存最多的 10 个物料是什么？" 这类问句被 "是什么" 误判为 RAG。
    """
    if _looks_like_analytics(question):
        return False
    q = question
    for pat in _RAG_KNOWLEDGE_PATTERNS:
        if re.search(pat, q):
            return True
    if any(hint in q for hint in _RAG_KNOWLEDGE_HINTS):
        return True
    return False


def _looks_like_analytics(question: str) -> bool:
    """数据分析特征（动词 + 数据对象）。"""
    q = question
    has_verb = any(verb in q for verb in _ANALYTICS_VERBS)
    has_subject = any(sub in q for sub in _DATA_SUBJECTS)
    has_metric_hint = bool(
        re.search(r"(前\s*\d+|top\s*\d+|占比|趋势|近\s*\d+\s*天|本月|今日|昨天)", q)
    )
    if has_verb and has_subject:
        return True
    if has_metric_hint and has_subject:
        return True
    if has_verb and has_metric_hint:
        return True
    return False


def _match_tool(
    question: str,
    capabilities: Sequence[ToolCapability],
) -> RouteDecision | None:
    """基于 capability description / aliases 的轻量匹配。

    不执行 Tool，不拼参数；只描述"是否明确匹配某个已注册 Tool"。
    """
    q = question.lower()
    for cap in capabilities:
        desc_lower = cap.description.lower()
        alias_hits = [a for a in cap.aliases if a and a in question]
        # 描述关键词：跳过停用词，匹配任一 2+ 字片段
        if any(tok in q for tok in _tokenize(desc_lower) if len(tok) >= 2):
            return RouteDecision(
                route=RouteType.TOOL,
                confidence=0.85,
                reason=f"规则命中：匹配已注册 Tool '{cap.name}'",
                source="tool_match",
            )
        if alias_hits:
            return RouteDecision(
                route=RouteType.TOOL,
                confidence=0.85,
                reason=(
                    f"规则命中：命中 Tool '{cap.name}' 别名 "
                    f"{alias_hits[:2]}"
                ),
                source="tool_match",
            )
    return None


def _tokenize(text_lower: str) -> list[str]:
    """极简分词：拆出 2+ 字中文片段 / 英文单词。"""
    return [t for t in re.split(r"[\s,;.，。；、]+", text_lower) if t]


def _extract_param_aliases(
    tool_name: str, params: Mapping[str, Any]
) -> tuple[str, ...]:
    """从 Tool 参数 Schema 抽取字段名作为别名（不暴露值）。"""
    aliases: list[str] = []
    properties = params.get("properties") if isinstance(params, dict) else None
    if isinstance(properties, dict):
        aliases.extend(str(k) for k in properties.keys())
    return tuple(dict.fromkeys(aliases))  # 去重保序


def _read_prompt(path: Path) -> str:
    """读取 system prompt 文件（缺失抛清晰异常）。"""
    if not path.exists():
        raise AIRouterError(f"prompt 文件不存在: {path}")
    return path.read_text(encoding="utf-8").strip()


def _read_template(path: Path) -> Template:
    """读取 user prompt 文件并解析为 ``string.Template``。"""
    if not path.exists():
        raise AIRouterError(f"prompt 文件不存在: {path}")
    return Template(path.read_text(encoding="utf-8").strip())


def _extract_text(response: Any) -> str:
    """从 LLMClient 返回中提取纯文本（兼容 str / LLMResponse）。"""
    if isinstance(response, str):
        return response.strip()
    content = getattr(response, "content", None)
    if isinstance(content, str):
        return content.strip()
    return ""


def _extract_json(raw: str) -> dict[str, Any] | None:
    """极简 JSON 提取：支持 ```json ... ``` 围栏。"""
    text = raw.strip()
    if text.startswith("```"):
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            text = m.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


# ============================================================
# 实现
# ============================================================

class AIRouterService:
    """Rule-first + LLM fallback 的 AI Router。"""

    def __init__(
        self,
        *,
        llm_client: LLMClient | None = None,
        tool_capabilities: ToolCapabilityRegistry | None = None,
        llm_fallback_enabled: bool | None = None,
        system_prompt: str | None = None,
        user_prompt_template: str | None = None,
    ) -> None:
        """构造 Router（全部依赖可注入，测试用 Fake）。

        Args:
            llm_client:           LLM 客户端（fallback 使用）。
            tool_capabilities:    Tool 能力元数据；None 时为空。
            llm_fallback_enabled: 是否启用 LLM fallback。
                                  None 时使用 settings.ai_router.llm_fallback_enabled。
            system_prompt:        覆盖默认 system prompt（测试用）。
            user_prompt_template: 覆盖默认 user prompt 模板（测试用）。
        """
        self._llm = llm_client
        self._tools = tool_capabilities
        if llm_fallback_enabled is None:
            llm_fallback_enabled = settings.ai_router.llm_fallback_enabled
        self._llm_fallback_enabled = bool(llm_fallback_enabled)
        self._system_prompt = (
            system_prompt
            if system_prompt is not None
            else _read_prompt(_PROMPTS_DIR / "router_system.txt")
        )
        # user_prompt_template 含 JSON 示例，不能用 str.format（{} 会被解析）
        self._user_prompt_template = (
            Template(user_prompt_template)
            if user_prompt_template is not None
            else _read_template(_PROMPTS_DIR / "router_user.txt")
        )

    # ---------- 依赖解析 ----------

    def _get_capabilities(self) -> tuple[ToolCapability, ...]:
        if self._tools is None:
            return ()
        try:
            return tuple(self._tools.list_capabilities())
        except Exception:
            logger.warning(
                "ToolCapabilityRegistry.list_capabilities failed",
                exc_info=True,
            )
            return ()

    # ---------- 主入口 ----------

    async def route(
        self,
        question: str,
        *,
        context: str | None = None,
    ) -> RouteDecision:
        """决定问题应进入哪种 AI 能力。

        Raises:
            AIRouterInputError:         空 / 非字符串 question。
            AIRouterClassificationError: LLM fallback 失败（API / JSON /
                                        越权 route），不影响规则命中路径。
        """
        if not isinstance(question, str):
            raise AIRouterInputError(
                f"question 必须是 str（当前: {type(question).__name__}）"
            )
        normalized = question.strip()
        if not normalized:
            raise AIRouterInputError("question 不能为空或纯空白")

        # ---- 1) Rule-first: TOOL capability 匹配（最具体） ----
        tool_decision = _match_tool(normalized, self._get_capabilities())
        if tool_decision is not None:
            return tool_decision

        # ---- 2) Rule-first: 数据分析特征（优先级高于知识，避免"是什么"
        #        这类宽泛词误吞"库存最多是什么"等分析问句） ----
        if _looks_like_analytics(normalized):
            return RouteDecision(
                route=RouteType.TEXT_TO_SQL,
                confidence=0.9,
                reason="规则命中：数据分析 / 统计 / 聚合意图",
                source="rule",
            )

        # ---- 3) Rule-first: 知识 / 流程特征 ----
        if _looks_like_knowledge(normalized):
            return RouteDecision(
                route=RouteType.RAG,
                confidence=0.9,
                reason="规则命中：业务知识 / 流程 / 操作提问",
                source="rule",
            )

        # ---- 4) LLM fallback ----
        if self._llm_fallback_enabled:
            try:
                return await self._route_via_llm(normalized, context)
            except AIRouterClassificationError as exc:
                logger.info("LLM routing failed: %s", exc)
                # 落入 conservative fallback（不向上抛，归类为 RAG）
                return RouteDecision(
                    route=RouteType.RAG,
                    confidence=None,
                    reason=f"LLM fallback 失败，按保守策略走 RAG: {exc}",
                    source="fallback",
                )

        # ---- 5) Conservative fallback: RAG ----
        return RouteDecision(
            route=RouteType.RAG,
            confidence=0.5,
            reason="规则无法判定，按系统安全策略回退 RAG",
            source="fallback",
        )

    # ---------- LLM fallback 内部 ----------

    async def _route_via_llm(
        self, question: str, context: str | None
    ) -> RouteDecision:
        if self._llm is None:
            raise AIRouterClassificationError("LLM 客户端未配置")

        user_prompt = self._user_prompt_template.safe_substitute(
            question=question
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        try:
            response = await self._llm.chat(messages)
        except LLMError as exc:
            raise AIRouterClassificationError(
                f"LLM 调用失败: {type(exc).__name__}"
            ) from exc
        except Exception as exc:
            raise AIRouterClassificationError(
                f"LLM 调用失败: {type(exc).__name__}"
            ) from exc

        raw = _extract_text(response)
        if not raw:
            raise AIRouterClassificationError("LLM 响应为空")

        payload = _extract_json(raw)
        if payload is None:
            raise AIRouterClassificationError("LLM 响应无法解析为 JSON")

        route_value = payload.get("route")
        reason = payload.get("reason")
        if not isinstance(route_value, str):
            raise AIRouterClassificationError("LLM 响应缺少合法 route 字段")
        valid_routes = frozenset(r.value for r in RouteType)
        if route_value not in valid_routes:
            raise AIRouterClassificationError(
                f"LLM 输出未知 route: {route_value!r}"
            )

        # 安全：reason 长度截断，避免异常 payload 把大字符串塞进来
        if not isinstance(reason, str):
            reason = None
        elif len(reason) > 200:
            reason = reason[:200]

        return RouteDecision(
            route=RouteType(route_value),
            confidence=None,
            reason=reason,
            source="llm",
        )


# ============================================================
# 工厂
# ============================================================

def get_default_router(
    *,
    llm_client: LLMClient | None = None,
    tool_capabilities: ToolCapabilityRegistry | None = None,
) -> AIRouterService:
    """工厂：按需接入项目全局 LLM 客户端与 ToolRegistry。

    项目级 ToolRegistry 通过 ``ToolRegistryCapabilityAdapter`` 暴露为
    capability registry（**只读元数据，不暴露 handler**）。
    """
    if tool_capabilities is None:
        try:
            from backend.app.tools.registry import ToolRegistry
            from backend.app.tools.mock_tools import register_mock_tools

            registry = ToolRegistry()
            register_mock_tools(registry)
            tool_capabilities = ToolRegistryCapabilityAdapter(registry)
        except Exception:
            logger.info("未接入项目 ToolRegistry，Router 使用空 capability")
            tool_capabilities = InMemoryToolCapabilityRegistry()
    return AIRouterService(
        llm_client=llm_client,
        tool_capabilities=tool_capabilities,
    )