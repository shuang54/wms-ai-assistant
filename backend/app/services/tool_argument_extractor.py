"""Tool Argument Extractor（Phase 3.11 Step 6）——Tool 参数提取边界。

背景（为什么独立出本组件）：
    Phase 3.11 Step 1～5 已确立 Tool Selection（Router 是唯一来源）/
    Tool Execution Boundary（ToolExecutionService）/ Multi-Parameter
    Argument Contract，但**参数提取**仍直接写在 ``AIOrchestratorService``
    内部（``_extract_tool_arguments_from_question`` + 正则 + 区间排除）。
    本模块把该职责抽出，成为「自然语言 → Tool 参数候选值」的唯一定义位置：

        Question + tool_name（+ Tool 声明字段）
            ↓
        ToolArgumentExtractor.extract()
            ↓
        arguments（dict；未命中的字段不出现）
            ↓
        ToolExecutionService
            ↓
        ToolRegistry（Schema 校验权威）
            ↓
        Tool（业务实现）

职责边界（务必阅读）：
    * 只做**确定性**匹配：字符串匹配 / 正则表达式 / 字符区间计算 /
      Tool-specific deterministic patterns（**不是** NLP、**不是** LLM）；
    * **不**做 Schema 校验：``required`` / ``type`` / ``additionalProperties``
      / 长度上限等全部仍由 ``ToolRegistry.validate_arguments`` 裁决
      （ToolRegistry 是唯一 Schema Authority）。缺必填字段时本模块
      **不会**伪造或补值，Registry 会返回 ``ToolResult(success=False)``；
    * **不**访问 ToolRegistry / ToolExecutionService / Handler：只接收调用方
      传入的「Tool 声明字段」（普通 Mapping），自身不持有任何执行能力；
    * **不**访问 DB / SQLAlchemy / Session / Engine / SQL 文本拼接；
    * **不**访问 Router / LLM / HTTP / 文件系统；
    * **无状态**：无缓存 / 无副作用 / 可安全复用（实例不保存任何提取结果）。

字段规则来源（优先级）：
    1. ``parameters``（调用方传入的 Tool 声明字段，来自
       ``ToolDefinition.parameters``）→ 按其中的 ``properties`` 键名决定
       哪些字段参与提取（**声明门槛**，不是校验）；
    2. ``parameters`` 缺失（Tool 未注册 / 独立调用）→ 回退到本模块的
       Tool 名称规则表（``_TOOL_FIELD_RULES``）；
    3. 两者都不适用（未知 Tool）→ 返回 ``{}``：**不猜 Tool、不 fallback
       到其它 Tool、不生成任何字段**。

明确不做（Phase 3.11 Step 6）：
    LLM 参数提取 / Function Calling / 通用 NLP Parser /
    ``parse_anything(question)`` / Schema-driven NLP 框架 /
    Agent / LangGraph / MCP / Memory / Planning / Multi-step Tool Calling。
    当前阶段**只**支持两个真实 Tool 所需字段
    （get_inventory：material_code + warehouse_code；
      get_work_order：work_order_no）；新增第三个 Tool 时在此显式扩展。

依赖方向（单向）：
    AIOrchestrator → ToolArgumentExtractor
    本模块不依赖 ai_orchestrator_service / tools / db / llm / api 任何模块
    （仅 Python 标准库 ``re`` / ``typing`` / ``collections.abc``）。
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Final

__all__ = ["ToolArgumentExtractor"]


# ============================================================
# 确定性模式（Phase 3.7.12 / 3.11 Step 4 & 5 原样迁移，字符级不变）
# ============================================================

#: 合法字面量（字母 / 数字 / dash / dot / underscore，长度 ≤ 64）。
#: 与 ``get_inventory`` / ``get_work_order`` 的 Handler 字符集一致
#: （Handler 仍是最终校验者；此处仅用于"识别候选字符串"）。
_TOOL_ARG_LITERAL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._\-]{0,63}"
)

#: warehouse_code 的最小确定性表达集合（Phase 3.11 Step 4）：
#:   "A01 仓库" / "A01仓库"
#:   "仓库 A01" / "仓库A01" / "仓库: A01" / "仓库=A01"
#:   "warehouse A01" / "warehouse_code=A01"（英文形式，大小写不敏感）
#: 顺序敏感：``[CODE] 仓库`` 必须优先于 ``仓库 [CODE]``，否则
#: "A01 仓库 MAT-001" 会误把 "仓库 MAT-001" 中的 MAT-001 当仓库编码。
_TOOL_WAREHOUSE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"([A-Za-z0-9][A-Za-z0-9._\-]{0,63})\s*仓库"),
    re.compile(r"仓库\s*[:：=]?\s*([A-Za-z0-9][A-Za-z0-9._\-]{0,63})"),
    re.compile(
        r"warehouse(?:_code)?\s*[:=]?\s*([A-Za-z0-9][A-Za-z0-9._\-]{0,63})",
        re.IGNORECASE,
    ),
)

#: work_order_no 的最小确定性表达集合（Phase 3.11 Step 5）：
#:   "工单 WO-202609-001" / "工单号 WO-001"
#:   "工单: WO-001" / "工单号=WO-001"
#:   "work_order WO-001" / "work_order_no=WO-001"（大小写不敏感）
_TOOL_WORK_ORDER_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"工单号?\s*[:：=]?\s*([A-Za-z0-9][A-Za-z0-9._\-]{0,63})"),
    re.compile(
        r"work_order(?:_no)?\s*[:=]?\s*([A-Za-z0-9][A-Za-z0-9._\-]{0,63})",
        re.IGNORECASE,
    ),
)

#: 本模块支持提取的字段全集（锁定范围：新增字段必须显式加在这里）。
_SUPPORTED_FIELDS: Final[frozenset[str]] = frozenset(
    {"material_code", "warehouse_code", "work_order_no"}
)

#: Tool 名称 → 该 Tool 的确定性字段规则（``parameters`` 缺失时的回退表）。
#: 只登记**真实** Tool；未知 Tool 名 → 空元组（不猜、不 fallback）。
_TOOL_FIELD_RULES: Final[Mapping[str, tuple[str, ...]]] = {
    "get_inventory": ("material_code", "warehouse_code"),
    "get_work_order": ("work_order_no",),
}


# ============================================================
# 内部工具函数（纯函数）
# ============================================================

def _spans_overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    """两个半开区间是否重叠（用于排除锚定上下文区间内的字面量）。"""
    return a[0] < b[1] and b[0] < a[1]


def _first_anchored_match(
    question: str,
    patterns: tuple[re.Pattern[str], ...],
) -> tuple[str, tuple[int, int]] | None:
    """按顺序匹配锚定表达，返回第一个命中的 ``(code, (start, end))``。

    区间用于 material_code 提取时排除（避免锚定值被误当物料编码）。
    """
    for pattern in patterns:
        match = pattern.search(question)
        if match is not None:
            code = match.group(1).strip()
            if code:
                return code, match.span()
    return None


def _match_warehouse_code(
    question: str,
) -> tuple[str, tuple[int, int]] | None:
    """识别 question 中的仓库编码及其字符区间（第一个命中）。

    Returns:
        ``(warehouse_code, (start, end))``；无仓库上下文 → None。
        区间用于 material_code 提取时排除（避免 A01 误当物料编码）。
    """
    return _first_anchored_match(question, _TOOL_WAREHOUSE_PATTERNS)


def _match_work_order_no(
    question: str,
) -> tuple[str, tuple[int, int]] | None:
    """识别 question 中的工单号及其字符区间（第一个命中）。

    Phase 3.11 Step 5（与 ``_match_warehouse_code`` 同构）。

    Returns:
        ``(work_order_no, (start, end))``；无工单上下文 → None。
        区间用于 material_code 提取时排除（避免 WO-001 误当物料编码）。
    """
    return _first_anchored_match(question, _TOOL_WORK_ORDER_PATTERNS)


def _declared_fields(
    tool_name: str,
    parameters: Mapping[str, Any] | None,
) -> tuple[str, ...]:
    """返回本次允许参与提取的候选字段（**声明门槛**，非 Schema 校验）。

    Args:
        tool_name:  已选中的 Tool 名称（调用方从 Router 的 RouteDecision 取得）。
        parameters: Tool 声明字段（``ToolDefinition.parameters``）；None 表示
                    调用方没有 Tool 定义（Tool 未注册 / 独立调用）。

    Returns:
        字段名元组：
            * ``parameters`` 提供 → 其 ``properties`` 中属于
              ``_SUPPORTED_FIELDS`` 的键（交集为空 → 空元组）；
            * ``parameters`` 为 None → ``_TOOL_FIELD_RULES[tool_name]``
              （未登记 → 空元组，**不** fallback 其它 Tool）。

    Note:
        这是"该 Tool 关心哪些字段"的过滤，**不是** required / 类型校验——
        那些仍由 ``ToolRegistry.validate_arguments`` 裁决。
    """
    if parameters is None:
        return _TOOL_FIELD_RULES.get(tool_name, ())
    properties = parameters.get("properties") or {}
    if not isinstance(properties, Mapping):
        return ()
    return tuple(field for field in properties if field in _SUPPORTED_FIELDS)


# ============================================================
# 提取器
# ============================================================

class ToolArgumentExtractor:
    """从用户问题中提取 Tool 参数候选值（纯确定性，无副作用）。

    当前支持的字段规则（字段本身与 Tool 的绑定见模块文档）：

        warehouse_code ← 显式仓库表达中的编码
                         （"A01 仓库" / "仓库 A01" / "warehouse A01" /
                          "warehouse_code=A01"）
        work_order_no  ← 显式工单表达中的单号
                         （"工单 WO-001" / "工单号 WO-001" /
                          "work_order WO-001" / "work_order_no=WO-001"）
        material_code  ← question 中第一个**不落在**任意锚定上下文
                         （仓库 / 工单表达）区间内的合法字面量

    用法：

        extractor = ToolArgumentExtractor()
        extractor.extract(
            "get_inventory", "查询物料 MAT-001 在 A01 仓库的库存"
        )
        # → {"warehouse_code": "A01", "material_code": "MAT-001"}

        extractor.extract("get_work_order", "查询工单 WO-202609-001")
        # → {"work_order_no": "WO-202609-001"}

        extractor.extract("unknown_tool", "查询物料 MAT-001 当前库存")
        # → {}（不猜 Tool、不 fallback 到 get_inventory / get_work_order）

    缺失字段（例如 "查询 A01 仓库库存" → ``{"warehouse_code": "A01"}``）
    不会被补齐：Schema 校验（required）由 ``ToolRegistry`` 负责。
    """

    def extract(
        self,
        tool_name: str,
        question: str,
        *,
        parameters: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """从 ``question`` 提取 ``tool_name`` 的参数候选值。

        Args:
            tool_name:  已选中的 Tool 名称（本模块**不**做 Tool 选择）。
            question:   用户问题（调用方已 strip；本模块不改变其内容）。
            parameters: Tool 声明字段（``ToolDefinition.parameters``）；
                        None → 回退到 ``_TOOL_FIELD_RULES``（见 ``_declared_fields``）。

        Returns:
            ``dict[str, Any]``：仅含确实命中的字段（顺序固定：
            warehouse_code → work_order_no → material_code）；
            未命中 / 未知 Tool / 无声明字段 → ``{}``（**不**返回 None，
            也不复制 Schema 校验结果）。

        Raises:
            TypeError: ``tool_name`` 非空 str / ``question`` 非 str
                       （调用方编程错误；Orchestrator 侧输入已校验）。
        """
        if not isinstance(tool_name, str) or not tool_name:
            raise TypeError(
                "tool_name 必须为非空 str"
                f"（got {type(tool_name).__name__}）"
            )
        if not isinstance(question, str):
            raise TypeError(
                f"question 必须为 str（got {type(question).__name__}）"
            )

        fields = _declared_fields(tool_name, parameters)
        if not fields:
            return {}

        arguments: dict[str, Any] = {}
        # 锚定上下文的字符区间（供 material_code 排除）
        anchored_spans: list[tuple[int, int]] = []

        # ---- 锚定字段：显式 Tool 上下文表达 ----
        if "warehouse_code" in fields:
            warehouse_match = _match_warehouse_code(question)
            if warehouse_match is not None:
                arguments["warehouse_code"] = warehouse_match[0]
                anchored_spans.append(warehouse_match[1])

        if "work_order_no" in fields:
            work_order_match = _match_work_order_no(question)
            if work_order_match is not None:
                arguments["work_order_no"] = work_order_match[0]
                anchored_spans.append(work_order_match[1])

        # ---- 字面量字段：第一个不落在任意锚定区间内的合法字面量 ----
        if "material_code" in fields:
            for match in _TOOL_ARG_LITERAL_PATTERN.finditer(question):
                if any(
                    _spans_overlap(match.span(), span)
                    for span in anchored_spans
                ):
                    continue
                candidate = match.group(0)
                if candidate.strip():
                    arguments["material_code"] = candidate
                    break

        return arguments
