"""Relevant Table Selector（Phase 3.7.4）。

职责（本阶段唯一目标）：

    User Question
        + DatabaseSchema（数据库事实）
        + ProjectSemantic（人工业务语义）
                ↓ RelevantTableSelector
    TableSelectionResult（相关表 + 可解释得分）

设计要点：

- **Strategy 可替换**：上层只依赖 `RelevantTableSelector` Protocol；
  本阶段提供唯一实现 `RuleBasedRelevantTableSelector`（关键词 /
  业务语义匹配）。未来可平行新增 EmbeddingSelector / LLMSelector /
  HybridSelector，上层接口零改动。
- **候选表只来自 DatabaseSchema**（数据库事实）；ProjectSemantic
  只提供加分信号——引用了 Schema 中不存在的表直接忽略，绝不返回。
- **可解释**：每张入选表携带 matched_terms，"为什么选这张表"
  可以直接回答，不做黑盒分数。
- **纯内存**：不查询数据库、不调用 LLM / Embedding、不读 .env、
  不 import SQLAlchemy（测试静态断言锁定）。上层需要最新 Schema
  时自己先调 Schema Explorer。
- **职责边界**：只回答 Which tables？不生成 Prompt、不序列化
  （那是 SchemaSerializer / BusinessSemanticSerializer /
  DatabaseContextComposer 的职责）。
- **确定性**：相同输入 → 完全相同输出。排序 score DESC，
  schema.table ASC；matched_terms 排序去重。

匹配规则（MVP，稳定可测试）：

- 标准化：strip + lower + 连续空白压缩（中文保持原字符），
  不修改原始 question；
- 完整 substring 匹配：术语（标准化后）出现在标准化问题中即命中；
- 标识符额外生成下划线→空格变体（knowledge_document ↔
  knowledge document）；
- 忽略纯数字术语（"10001" 不是业务语义，§十一）；
- 忽略长度 < 2 的术语（防单字符噪音误命中）。

零匹配是**正常结果**（selections=()），不猜表、不返回全部表、
不抛异常——未来 Router 可据此决定转 RAG 或普通 Chat。
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Final, Protocol

from backend.app.projects.semantic import (
    ColumnSemantic,
    ProjectSemantic,
    TableSemantic,
)
from backend.app.services.schema_explorer_service import (
    DatabaseSchema,
    SchemaTable,
)

logger = logging.getLogger(__name__)

__all__ = [
    "RelevantTableSelectorError",
    "RelevantTableSelectorInputError",
    "TableSelectionWeights",
    "TableSelection",
    "TableSelectionResult",
    "RelevantTableSelector",
    "RuleBasedRelevantTableSelector",
    "DEFAULT_TOP_K",
    "MAX_TOP_K",
]


# ============================================================
# 常量
# ============================================================

#: 默认返回的相关表数量
DEFAULT_TOP_K: Final[int] = 5

#: top_k 上限（防上游 bug 一次拉出全库表）
MAX_TOP_K: Final[int] = 50

#: 术语最小长度（标准化后；单字符术语噪音过大，跳过）
_MIN_TERM_LEN: Final[int] = 2

#: 纯数字术语（如 "10001"）不是业务语义，跳过
_PURE_DIGITS_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9]+$")


# ============================================================
# 异常体系
# ============================================================

class RelevantTableSelectorError(Exception):
    """Relevant Table Selector 通用异常基类。"""


class RelevantTableSelectorInputError(RelevantTableSelectorError):
    """输入非法：question / schema / semantic 类型错误、top_k 越界。

    在产生任何结果之前拒绝。
    """


# ============================================================
# DTO（frozen，不暴露 ORM / 连接信息）
# ============================================================

@dataclass(frozen=True)
class TableSelectionWeights:
    """MVP 评分权重（默认值，可注入覆盖；不做进一步配置化）。

    权重含义：问题命中该类术语时的加分。数值只是 MVP 默认值，
    人工配置的业务语义（business_name / alias）权重高于数据库
    命名（table_name / column_name）。
    """

    table_name: float = 3.0
    table_business_name: float = 5.0
    table_alias: float = 4.0
    table_description: float = 2.0
    column_name: float = 2.0
    column_business_name: float = 4.0
    column_alias: float = 3.0


@dataclass(frozen=True)
class TableSelection:
    """单张入选表（可解释）。

    Attributes:
        table:         schema 限定表名（如 public.inventory），
                       可直接喂给 SchemaSerializer 的 tables 参数。
        score:         命中加分总和（各命中项按权重累加）。
        matched_terms: 去重排序后的命中术语（标准化形态），
                       回答"为什么选这张表"。
    """

    table: str
    score: float
    matched_terms: tuple[str, ...]


@dataclass(frozen=True)
class TableSelectionResult:
    """表选择结果。

    Attributes:
        question:   原始问题（未修改、未标准化）。
        selections: 按 score DESC、schema.table ASC 排序的入选表；
                    零匹配时为空 tuple（正常结果，不是错误）。
    """

    question: str
    selections: tuple[TableSelection, ...]


# ============================================================
# Protocol（上层只依赖本接口，不依赖具体算法）
# ============================================================

class RelevantTableSelector(Protocol):
    """问题 → 相关表选择器协议（Phase 3.7.4 引入）。

    实现必须：纯内存（不查库 / 不调 LLM）、确定性输出、
    候选表只来自 DatabaseSchema。
    """

    def select(
        self,
        question: str,
        schema: DatabaseSchema,
        semantic: ProjectSemantic,
        *,
        top_k: int = DEFAULT_TOP_K,
    ) -> TableSelectionResult:
        """选择与问题相关的表。

        Raises:
            RelevantTableSelectorInputError: 输入非法。
        """
        ...


# ============================================================
# 规则实现（MVP：关键词 / 业务语义匹配）
# ============================================================

class RuleBasedRelevantTableSelector:
    """基于规则的可解释表选择器（Phase 3.7.4 的唯一实现）。

    算法：对 DatabaseSchema 中每张表，收集候选术语
    （表名 / 表语义 / 列名 / 列语义），逐个做标准化 substring
    匹配；命中即按权重加分；score > 0 的表入选。

    复杂度 O(表数 × 列数)，纯内存扫描——企业库达到数千张表时
    再考虑 ANN / BM25 等优化（本阶段明确不做）。
    """

    def __init__(self, *, weights: TableSelectionWeights | None = None) -> None:
        """构造选择器。

        Args:
            weights: 评分权重；None 时使用 MVP 默认值。
        """
        self._weights = (
            weights if weights is not None else TableSelectionWeights()
        )

    # ---------- 主流程 ----------

    def select(
        self,
        question: str,
        schema: DatabaseSchema,
        semantic: ProjectSemantic,
        *,
        top_k: int = DEFAULT_TOP_K,
    ) -> TableSelectionResult:
        """选择与问题最相关的 top_k 张表。

        Args:
            question: 用户问题（原文保留在结果中，匹配用标准化副本）。
            schema:   数据库事实（候选表唯一来源）。
            semantic: 项目业务语义；可以是空 ProjectSemantic()
                      （此时退化为表名 / 列名匹配）。
            top_k:    返回表数量上限，1 <= top_k <= 50。

        Returns:
            TableSelectionResult；零匹配 → selections=()。

        Raises:
            RelevantTableSelectorInputError: 输入非法。
        """
        start_time = time.perf_counter()
        self._validate_inputs(
            question=question, schema=schema, semantic=semantic, top_k=top_k
        )

        question_norm = _normalize(question)
        if not question_norm:
            # 空白问题 → 零匹配（正常结果）
            return TableSelectionResult(question=question, selections=())

        table_semantics, column_semantics = _index_semantic(semantic, schema)

        scored: list[TableSelection] = []
        for table in sorted(
            schema.tables, key=lambda t: (t.schema_name, t.name)
        ):
            selection = self._score_table(
                question_norm=question_norm,
                table=table,
                table_semantic=table_semantics.get(
                    f"{table.schema_name}.{table.name}"
                ),
                column_semantics=column_semantics.get(
                    f"{table.schema_name}.{table.name}", ()
                ),
            )
            if selection is not None:
                scored.append(selection)

        # 稳定排序：score DESC，schema.table ASC
        scored.sort(key=lambda s: (-s.score, s.table))
        result = TableSelectionResult(
            question=question, selections=tuple(scored[:top_k])
        )

        logger.info(
            "relevant table selection completed",
            extra={
                "question_chars": len(question),
                "table_count": len(schema.tables),
                "selected_count": len(result.selections),
                "top_k": top_k,
                "elapsed_ms": (time.perf_counter() - start_time) * 1000,
            },
        )
        return result

    # ---------- 校验（先于一切处理） ----------

    @staticmethod
    def _validate_inputs(
        *,
        question: str,
        schema: DatabaseSchema,
        semantic: ProjectSemantic,
        top_k: int,
    ) -> None:
        if not isinstance(question, str):
            raise RelevantTableSelectorInputError(
                f"question 必须是 str（当前: {type(question).__name__}）"
            )
        if not isinstance(schema, DatabaseSchema):
            raise RelevantTableSelectorInputError(
                f"schema 必须是 DatabaseSchema 实例"
                f"（当前: {type(schema).__name__}）"
            )
        if not isinstance(semantic, ProjectSemantic):
            raise RelevantTableSelectorInputError(
                "semantic 必须是 ProjectSemantic 实例（空语义请传 "
                f"ProjectSemantic()；当前: {type(semantic).__name__}）"
            )
        # bool 是 int 子类，必须先排除（项目既有风格）
        if isinstance(top_k, bool) or not isinstance(top_k, int):
            raise RelevantTableSelectorInputError(
                f"top_k 必须是整数（当前: {type(top_k).__name__}）"
            )
        if not 1 <= top_k <= MAX_TOP_K:
            raise RelevantTableSelectorInputError(
                f"top_k 必须在 [1, {MAX_TOP_K}] 内（当前: {top_k}）"
            )

    # ---------- 单表评分 ----------

    def _score_table(
        self,
        *,
        question_norm: str,
        table: SchemaTable,
        table_semantic: TableSemantic | None,
        column_semantics: tuple[ColumnSemantic, ...],
    ) -> TableSelection | None:
        """对单张表打分；无任何命中 → None（不入选）。"""
        weights = self._weights
        hits: list[tuple[str, float]] = []

        # 1) 数据库事实：表名 + 列名
        hits.extend(_match(question_norm, (table.name,), weights.table_name))
        hits.extend(
            _match(
                question_norm,
                tuple(c.name for c in table.columns),
                weights.column_name,
            )
        )

        # 2) 人工语义：表级
        if table_semantic is not None:
            if table_semantic.business_name:
                hits.extend(
                    _match(
                        question_norm,
                        (table_semantic.business_name,),
                        weights.table_business_name,
                    )
                )
            if table_semantic.description:
                hits.extend(
                    _match(
                        question_norm,
                        (table_semantic.description,),
                        weights.table_description,
                    )
                )
            hits.extend(
                _match(
                    question_norm, table_semantic.aliases, weights.table_alias
                )
            )

        # 3) 人工语义：列级
        for cs in column_semantics:
            if cs.business_name:
                hits.extend(
                    _match(
                        question_norm,
                        (cs.business_name,),
                        weights.column_business_name,
                    )
                )
            hits.extend(
                _match(question_norm, cs.aliases, weights.column_alias)
            )

        if not hits:
            return None
        return TableSelection(
            table=f"{table.schema_name}.{table.name}",
            score=sum(weight for _, weight in hits),
            matched_terms=tuple(sorted({term for term, _ in hits})),
        )


# ============================================================
# 匹配辅助（模块级纯函数）
# ============================================================

def _normalize(text: str) -> str:
    """标准化：strip + lower + 连续空白压缩；中文保持原字符。"""
    return " ".join(text.lower().split())


def _is_matchable(term_norm: str) -> bool:
    """术语过滤：太短 / 纯数字的术语不参与匹配（防噪音）。"""
    return len(term_norm) >= _MIN_TERM_LEN and not _PURE_DIGITS_RE.match(
        term_norm
    )


def _variants(term_norm: str) -> tuple[str, ...]:
    """标识符变体：下划线 → 空格（knowledge_document / knowledge document）。"""
    if "_" in term_norm:
        return (term_norm, term_norm.replace("_", " "))
    return (term_norm,)


def _match(
    question_norm: str,
    terms: tuple[str, ...] | list[str],
    weight: float,
) -> list[tuple[str, float]]:
    """逐术语 substring 匹配；每个术语至多命中一次（任一变体命中即算）。"""
    hits: list[tuple[str, float]] = []
    for term in terms:
        if not isinstance(term, str):
            continue
        term_norm = _normalize(term)
        if not _is_matchable(term_norm):
            continue
        for variant in _variants(term_norm):
            if variant in question_norm:
                hits.append((variant, weight))
                break
    return hits


def _resolve_table_ref(
    schema: DatabaseSchema, reference: str
) -> SchemaTable | None:
    """解析语义配置的表引用（规则与 Validator / Serializer 一致）：
    "schema.table" 精确匹配优先，裸表名在当前 schema 内唯一匹配。"""
    for t in schema.tables:
        if f"{t.schema_name}.{t.name}" == reference:
            return t
    bare = [t for t in schema.tables if t.name == reference]
    return bare[0] if len(bare) == 1 else None


def _index_semantic(
    semantic: ProjectSemantic, schema: DatabaseSchema
) -> tuple[dict[str, TableSemantic], dict[str, tuple[ColumnSemantic, ...]]]:
    """把语义按 DatabaseSchema 事实索引到 "schema.table" 键上。

    Semantic 引用了 Schema 中不存在的表 → 直接忽略（§十八），
    绝不会出现在选择结果里。
    """
    ts_by_key: dict[str, TableSemantic] = {}
    for ts in sorted(semantic.tables, key=lambda t: t.table):
        table = _resolve_table_ref(schema, ts.table)
        if table is None:
            continue
        key = f"{table.schema_name}.{table.name}"
        ts_by_key.setdefault(key, ts)

    cs_by_key: dict[str, list[ColumnSemantic]] = {}
    for cs in sorted(semantic.columns, key=lambda c: (c.table, c.column)):
        table = _resolve_table_ref(schema, cs.table)
        if table is None:
            continue
        cs_by_key.setdefault(f"{table.schema_name}.{table.name}", []).append(cs)

    return ts_by_key, {k: tuple(v) for k, v in cs_by_key.items()}
