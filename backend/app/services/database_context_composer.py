"""Database Context Composer（Phase 3.7.3，轻量组合层）。

职责（刻意保持极简，**不是 Prompt Engine**）：

    ProjectContext（非敏感项目头）
        +
    SchemaSerializer 输出（数据库事实）
        +
    BusinessSemanticSerializer 输出（人工业务语义）
                ↓
    单段 AI Database Context（共享字符预算）

只做字符串组合 + 统一预算；不管 Prompt 模板、不管 Chat、
不管 Tool、不调 LLM、不访问数据库。

分工回顾（不改变 SchemaSerializer 职责）：

    SchemaSerializer            数据库事实
    BusinessSemanticSerializer  业务人工定义
    DatabaseContextComposer     两者拼接 + 预算（本模块）
"""
from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import Final

from backend.app.projects.models import ProjectContext
from backend.app.projects.semantic import ProjectSemantic
from backend.app.services.business_semantic_serializer import (
    BusinessSemanticSerializer,
)
from backend.app.services.schema_explorer_service import DatabaseSchema
from backend.app.services.schema_serializer_service import (
    SchemaSerializer,
    SchemaSerializerInputError,
    apply_line_budget,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DatabaseContextComposer",
    "DEFAULT_CONTEXT_MAX_CHARS",
]

#: 组合上下文默认字符预算（与 SchemaSerializer 默认一致；
#: 字符数近似，非精确 Token——MVP 不引入 Tokenizer 依赖）
DEFAULT_CONTEXT_MAX_CHARS: Final[int] = 12000


class DatabaseContextComposer:
    """Project + Schema + Semantic → 单段 AI Database Context。

    段落顺序固定（预算耗尽时从后往前丢）：
        1. Project 头（project_name / Data Source / Database Type）
        2. Database Schema（事实）
        3. Business Semantics（人工语义）
    截断规则复用 SchemaSerializer 的整行预算（绝不半截行），
    marker 为 `[Database Context truncated: max_chars=N]`。
    """

    def __init__(
        self,
        *,
        schema_serializer: SchemaSerializer | None = None,
        semantic_serializer: BusinessSemanticSerializer | None = None,
    ) -> None:
        self._schema_serializer = (
            schema_serializer if schema_serializer is not None else SchemaSerializer()
        )
        self._semantic_serializer = (
            semantic_serializer
            if semantic_serializer is not None
            else BusinessSemanticSerializer()
        )

    def compose(
        self,
        *,
        project: ProjectContext,
        schema: DatabaseSchema,
        semantic: ProjectSemantic | None = None,
        tables: Sequence[str] | None = None,
        max_chars: int = DEFAULT_CONTEXT_MAX_CHARS,
    ) -> str:
        """组合三段上下文并应用统一字符预算。

        Args:
            project:   项目上下文（只输出非敏感 metadata）。
            schema:    数据库事实 DTO。
            semantic:  项目业务语义；None / 空语义 → 跳过语义段。
            tables:    透传给 SchemaSerializer 的显式表选择。
            max_chars: 组合输出的字符预算。

        Returns:
            稳定的 AI Database Context 文本。

        Raises:
            SchemaSerializerInputError: 参数非法（透传自 SchemaSerializer）。
            SchemaSerializationError:  tables 指定不存在的表（透传）。
        """
        start_time = time.perf_counter()
        if max_chars <= 0:
            raise SchemaSerializerInputError(
                f"max_chars 必须为正整数（当前: {max_chars}）"
            )

        # 1. Project 头（与 SchemaSerializer 相同的非敏感字段）
        lines = [
            f"Project: {' '.join(project.project_name.splitlines())}",
            f"Data Source: {' '.join(project.data_source.name.splitlines())}",
            f"Database Type: {' '.join(project.data_source.type.splitlines())}",
        ]

        # 2. Schema 段（不让 SchemaSerializer 再输出项目头，避免重复）
        schema_text = self._schema_serializer.serialize(
            schema, tables=tables
        )
        if schema_text:
            lines.append("")
            lines.append("## Database Schema")
            lines.append("")
            lines.extend(schema_text.split("\n"))

        # 3. Semantic 段
        semantic_text = ""
        if semantic is not None:
            semantic_text = self._semantic_serializer.serialize(semantic)
        if semantic_text:
            lines.append("")
            lines.extend(semantic_text.split("\n"))

        result, truncated = apply_line_budget(
            lines, max_chars=max_chars, marker_prefix="Database Context"
        )
        logger.info(
            "database context composed",
            extra={
                "table_count": len(schema.tables),
                "has_semantic": bool(semantic_text),
                "truncated": truncated,
                "output_chars": len(result),
                "max_chars": max_chars,
                "elapsed_ms": (time.perf_counter() - start_time) * 1000,
            },
        )
        return result
