"""Text-to-SQL Generator（Phase 3.7.6）。

链路（本阶段实现到 Validated SQL 为止）：

    Question
        + Database Context（由 DatabaseContextComposer 产出，
          本模块不重新实现 Schema Serializer）
        + Allowed Tables（来自 RelevantTableSelector）
                ↓
    LLM（复用现有 LLMClient，不重新实现 HTTP Client）
                ↓
    extract_sql（纯文本提取，不做任何安全判断）
                ↓
    SQLValidator（Phase 3.7.5，安全强制边界）
                ↓
    通过 → TextToSQLResult(validated=True)
    拒绝 → 携带错误让 LLM 修正（预算内重试）
    预算耗尽 → 明确异常（绝不返回看似可执行的失败 SQL）

核心原则：

- **Generator 不拥有数据库执行能力**：模块不 import 任何 ORM /
  DB session / engine，不存在任何 SQL 执行代码路径；
- **Prompt 是软约束，Validator 是硬约束**：Prompt 引导 LLM，
  安全性绝不寄托在 Prompt 上；用户问题被视为数据而非指令
  （Prompt Injection 由 Validator 兜底拒绝）；
- **有限预算**：默认 max_attempts=3（钳制 [1, 10]），
  不存在 while True；
- **不可变**：frozen DTO；question / schema / allowed_tables
  原文不被修改；相同输入 + 相同 LLM 输出 → 相同结果。
"""
from __future__ import annotations

import logging
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol

from backend.app.llm.client import LLMClient
from backend.app.services.schema_explorer_service import DatabaseSchema
from backend.app.services.sql_validator_service import (
    DEFAULT_MAX_ROWS,
    SQLValidationError,
    SQLValidator,
    SQLValidatorInputError,
    SQLValidatorService,
)

logger = logging.getLogger(__name__)

__all__ = [
    "TextToSQLError",
    "TextToSQLInputError",
    "TextToSQLGenerationError",
    "TextToSQLRetryExceededError",
    "TextToSQLResult",
    "TextToSQLGenerator",
    "TextToSQLService",
    "extract_sql",
    "DEFAULT_MAX_ATTEMPTS",
]


# ============================================================
# 常量
# ============================================================

#: 默认最大 LLM 生成次数（含首次）
DEFAULT_MAX_ATTEMPTS: Final[int] = 3

_PROMPTS_DIR: Final[Path] = Path(__file__).resolve().parent.parent / "prompts"
_SYSTEM_PROMPT_FILE: Final[Path] = _PROMPTS_DIR / "text_to_sql_system.txt"
_USER_PROMPT_FILE: Final[Path] = _PROMPTS_DIR / "text_to_sql_user.txt"
_RETRY_PROMPT_FILE: Final[Path] = _PROMPTS_DIR / "text_to_sql_retry.txt"

#: markdown sql fence（```sql ... ``` / ``` ... ```）
_SQL_FENCE_RE: Final[re.Pattern[str]] = re.compile(
    r"```(?:sql)?\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE
)

#: SQL 语句起始行（提取 "Here is the query:" 之类前缀后的正文）
_SQL_START_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(SELECT|WITH)\b", re.IGNORECASE
)


# ============================================================
# 异常体系
# ============================================================

class TextToSQLError(Exception):
    """Text-to-SQL 通用异常基类。"""


class TextToSQLInputError(TextToSQLError):
    """输入非法：question / database_context 缺失或类型错误、
    max_rows / max_attempts 越界。在调用 LLM 之前拒绝。"""


class TextToSQLGenerationError(TextToSQLError):
    """LLM 输出无法产出候选 SQL（如连续返回空 / 纯说明文字），
    且重试预算耗尽。不携带任何可被误执行的 SQL。"""

    def __init__(self, question: str, attempts: int) -> None:
        super().__init__(
            f"LLM 在 {attempts} 次尝试内均未返回可用 SQL"
            f"（question 长度 {len(question)}）"
        )
        self.question = question
        self.attempts = attempts


class TextToSQLRetryExceededError(TextToSQLError):
    """重试预算耗尽：Validator 连续拒绝全部生成结果。

    注意：异常只携带 question / attempts / validation errors，
    **不携带最后一次失败的 SQL**——失败的 SQL 绝不能被上层
    误认为可执行结果（§二十八）。
    """

    def __init__(
        self,
        question: str,
        attempts: int,
        validation_errors: tuple[SQLValidationError, ...],
    ) -> None:
        codes = ", ".join(e.code.value for e in validation_errors) or "N/A"
        super().__init__(
            f"SQL 生成重试 {attempts} 次仍未通过校验"
            f"（最后错误: {codes}；question 长度 {len(question)}）"
        )
        self.question = question
        self.attempts = attempts
        self.validation_errors = validation_errors


# ============================================================
# DTO（frozen）
# ============================================================

@dataclass(frozen=True)
class TextToSQLResult:
    """生成结果（只有 validated=True 才会以本 DTO 返回）。

    Attributes:
        question:           原始问题（未修改）。
        sql:                通过 Validator 的 SQL。
        attempts:           实际 LLM 生成次数（1 起）。
        validated:          恒为 True（失败走异常，不返回假 SQL）。
        referenced_tables:  Validator 提取的规范化引用表。
    """

    question: str
    sql: str
    attempts: int
    validated: bool
    referenced_tables: tuple[str, ...]


# ============================================================
# Protocol（上层依赖接口，不依赖 DeepSeek / sqlglot）
# ============================================================

class TextToSQLGenerator(Protocol):
    """Text-to-SQL 生成器协议（Phase 3.7.6 引入）。"""

    async def generate(
        self,
        question: str,
        *,
        database_context: str,
        allowed_tables: Sequence[str] | None = None,
        schema: DatabaseSchema | None = None,
        max_rows: int = DEFAULT_MAX_ROWS,
    ) -> TextToSQLResult:
        """把自然语言问题转换成**通过 Validator 校验的**只读 SQL。

        Raises:
            TextToSQLInputError:        输入非法。
            TextToSQLGenerationError:   LLM 连续无法产出 SQL。
            TextToSQLRetryExceededError: 校验重试预算耗尽。
            LLMError:                   LLM 网络 / 响应错误（透传）。
        """
        ...


# ============================================================
# SQL 提取（§十八：极小，只做文本提取，不做安全判断）
# ============================================================

def extract_sql(output: str) -> str:
    """从 LLM 输出中提取候选 SQL（不做任何安全判断）。

    支持三种形态（其余原样返回，交给 Validator 裁决）：
        1. markdown sql fence（```sql ... ```）；
        2. "Here is the query:" 等前缀 + 以 SELECT/WITH 开头的正文；
        3. 本身就是 SQL-only 输出。
    """
    text = (output or "").strip()
    if not text:
        return ""

    fence = _SQL_FENCE_RE.search(text)
    if fence:
        return fence.group(1).strip()

    lines = text.splitlines()
    for i, line in enumerate(lines):
        if _SQL_START_RE.match(line):
            return "\n".join(lines[i:]).strip()

    return text


# ============================================================
# 实现
# ============================================================

class TextToSQLService:
    """Text-to-SQL Generator 默认实现（复用 LLMClient + SQLValidator）。

    职责边界：
        生成 SQL + 提取 + 交给 Validator + 预算内重试。
    绝不：执行 SQL、修改 SQL、重新实现 Schema 序列化 / Validator。
    """

    def __init__(
        self,
        *,
        llm_client: LLMClient | None = None,
        validator: SQLValidator | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        max_rows: int = DEFAULT_MAX_ROWS,
    ) -> None:
        """构造 Generator。

        Args:
            llm_client:  LLM 客户端；None 时懒加载全局默认
                         ``get_default_llm_client()``。测试注入 Fake。
            validator:   SQL 校验器；None 时使用 SQLValidatorService()。
            max_attempts: LLM 生成次数上限（含首次），[1, 10]。
            max_rows:   LIMIT 上限，与 Validator 一致。
        """
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int):
            raise TextToSQLInputError(
                f"max_attempts 必须是整数（当前: {type(max_attempts).__name__}）"
            )
        if not 1 <= max_attempts <= 10:
            raise TextToSQLInputError(
                f"max_attempts 必须在 [1, 10] 内（当前: {max_attempts}）"
            )
        if isinstance(max_rows, bool) or not isinstance(max_rows, int):
            raise TextToSQLInputError(
                f"max_rows 必须是整数（当前: {type(max_rows).__name__}）"
            )
        if max_rows < 1:
            raise TextToSQLInputError(
                f"max_rows 必须 >= 1（当前: {max_rows}）"
            )
        # 延迟 import 以便 FakeLLMClient 测试无需真实配置
        self._llm_client = llm_client
        self._validator = validator if validator is not None else SQLValidatorService()
        self._max_attempts = max_attempts
        self._max_rows = max_rows

    # ---------- 依赖解析（懒加载） ----------

    def _get_llm_client(self) -> LLMClient:
        if self._llm_client is not None:
            return self._llm_client
        from backend.app.llm.client import get_default_llm_client

        return get_default_llm_client()

    # ---------- 主流程 ----------

    async def generate(
        self,
        question: str,
        *,
        database_context: str,
        allowed_tables: Sequence[str] | None = None,
        schema: DatabaseSchema | None = None,
        max_rows: int = DEFAULT_MAX_ROWS,
    ) -> TextToSQLResult:
        """见 Protocol docstring。"""
        start_time = time.perf_counter()
        self._validate_inputs(
            question=question,
            database_context=database_context,
            allowed_tables=allowed_tables,
            schema=schema,
            max_rows=max_rows,
        )

        allowed_block = self._allowed_tables_block(allowed_tables)
        llm = self._get_llm_client()

        previous_sql: str | None = None
        previous_errors: tuple[SQLValidationError, ...] = ()

        for attempt in range(1, self._max_attempts + 1):
            messages = self._build_messages(
                question=question,
                database_context=database_context,
                allowed_block=allowed_block,
                max_rows=max_rows,
                previous_sql=previous_sql,
                previous_errors=previous_errors,
            )

            raw = await self._call_llm(llm, messages)
            sql = extract_sql(raw)
            if not sql.strip():
                # 生成失败（空输出）——预算内继续重试
                previous_sql = None
                previous_errors = ()
                continue

            try:
                validation = self._validator.validate(
                    sql,
                    schema=schema,
                    allowed_tables=allowed_tables,
                    max_rows=max_rows,
                )
            except SQLValidatorInputError as exc:
                raise TextToSQLInputError(
                    f"allowed_tables / max_rows 无法通过校验: {exc}"
                ) from exc

            if validation.valid:
                result = TextToSQLResult(
                    question=question,
                    sql=sql,
                    attempts=attempt,
                    validated=True,
                    referenced_tables=validation.referenced_tables,
                )
                logger.info(
                    "text-to-sql generation succeeded",
                    extra={
                        "attempts": attempt,
                        "question_chars": len(question),
                        "context_chars": len(database_context),
                        "referenced_tables": validation.referenced_tables,
                        "elapsed_ms": (time.perf_counter() - start_time) * 1000,
                    },
                )
                return result

            previous_sql = sql
            previous_errors = validation.errors

        # ---- 预算耗尽：明确失败，绝不返回失败 SQL ----
        if previous_errors:
            raise TextToSQLRetryExceededError(
                question=question,
                attempts=self._max_attempts,
                validation_errors=previous_errors,
            )
        raise TextToSQLGenerationError(
            question=question, attempts=self._max_attempts
        )

    # ---------- 输入校验（先于一切 LLM 调用） ----------

    @staticmethod
    def _validate_inputs(
        *,
        question: str,
        database_context: str,
        allowed_tables: Sequence[str] | None,
        schema: DatabaseSchema | None,
        max_rows: int,
    ) -> None:
        if not isinstance(question, str):
            raise TextToSQLInputError(
                f"question 必须是 str（当前: {type(question).__name__}）"
            )
        if not question.strip():
            raise TextToSQLInputError("question 不能为空或纯空白")
        if not isinstance(database_context, str):
            raise TextToSQLInputError(
                "database_context 必须是 str"
                f"（当前: {type(database_context).__name__}）"
            )
        if not database_context.strip():
            raise TextToSQLInputError(
                "database_context 不能为空或纯空白（不要让空上下文进入 LLM）"
            )
        if schema is not None and not isinstance(schema, DatabaseSchema):
            raise TextToSQLInputError(
                f"schema 必须是 DatabaseSchema 或 None"
                f"（当前: {type(schema).__name__}）"
            )
        if isinstance(max_rows, bool) or not isinstance(max_rows, int):
            raise TextToSQLInputError(
                f"max_rows 必须是整数（当前: {type(max_rows).__name__}）"
            )
        if max_rows < 1:
            raise TextToSQLInputError(
                f"max_rows 必须 >= 1（当前: {max_rows}）"
            )
        if allowed_tables is not None:
            if isinstance(allowed_tables, (str, bytes)):
                raise TextToSQLInputError(
                    "allowed_tables 必须是表名序列，而不是单个字符串"
                )
            for entry in allowed_tables:
                if not isinstance(entry, str):
                    raise TextToSQLInputError(
                        "allowed_tables 每项必须是 str"
                        f"（当前: {type(entry).__name__}）"
                    )

    # ---------- Prompt 构造 ----------

    @staticmethod
    def _load_prompt(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise TextToSQLError(f"Prompt 文件缺失: {path.name}") from exc

    @staticmethod
    def _allowed_tables_block(allowed_tables: Sequence[str] | None) -> str:
        if not allowed_tables:
            return (
                "(no additional restriction — use ONLY tables that "
                "appear in the database context above)"
            )
        return "\n".join(f"- {t}" for t in allowed_tables)

    def _build_messages(
        self,
        *,
        question: str,
        database_context: str,
        allowed_block: str,
        max_rows: int,
        previous_sql: str | None,
        previous_errors: tuple[SQLValidationError, ...],
    ) -> list[dict[str, Any]]:
        system = self._load_prompt(_SYSTEM_PROMPT_FILE).strip()
        if previous_sql is None:
            user_template = self._load_prompt(_USER_PROMPT_FILE)
            user = user_template.format(
                database_context=database_context,
                allowed_tables_block=allowed_block,
                max_rows=max_rows,
                question=question,
            ).strip()
        else:
            retry_template = self._load_prompt(_RETRY_PROMPT_FILE)
            user = retry_template.format(
                database_context=database_context,
                allowed_tables_block=allowed_block,
                max_rows=max_rows,
                question=question,
                previous_sql=previous_sql,
                validation_errors="\n".join(
                    f"- {e.code.value}: {e.message}" for e in previous_errors
                ),
            ).strip()
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    # ---------- LLM 调用（仅文本，无 Tool Calling） ----------

    @staticmethod
    async def _call_llm(
        llm: LLMClient, messages: list[dict[str, Any]]
    ) -> str:
        """调用 LLM 并取回文本 content。

        LLMError（网络 / 超时 / 响应异常）原样透传——
        属基础设施错误，不是生成质量问题，不做无意义重试。
        """
        result = await llm.chat(messages)
        if isinstance(result, str):
            return result
        # LLMResponse（仅 tools 非空时返回）——本模块不传 tools
        return result.content or ""
