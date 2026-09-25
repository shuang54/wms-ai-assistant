"""Text-to-SQL Dangerous SQL Validator Security E2E（Phase 3.9.7）。

补齐 Phase 3.9.6 指出的安全验证缺口：验证**已知危险 SQL** 进入真实链路后
是否被真实 SQL Validator 拒绝，并确认它**不会进入 SQL Executor**。

```text
Stub Generator（强制返回危险 SQL）
        ↓
TextToSQLEvaluationRunner（生产逻辑，未修改）
        ↓
Real SQLValidatorService（真实实现，禁止 Fake）
        ↓
REJECT
        ↓
Spy Executor：call_count == 0
```

边界（§十 / §十一）：

```text
Validator = real（SQLValidatorService）
Generator = stub（故意产出危险 SQL）
Executor  = spy（只用于证明"没有被调用"）
LLM       = fake（仅 TextToSQLService 边界用例使用，绝不调用真实模型）
Database  = NO（不连业务库）
Network   = NO
```

关键点：

- 危险 SQL **必须真实到达** Validator（断言 ``generated_sql`` 与
  ``validation_error_codes``），而不是只断言"结果失败"；
- Executor 零调用是**硬断言**（§九 / §十八），不是 "executed sql 不含 DELETE"；
- 被测 case 显式设置 ``must_execute=True``（``with_execution_required``），
  即"**即使业务要求执行**，危险 SQL 也不得进入 Executor"；
- 正向对照：只读 SELECT 必须仍然被接受（防止 Validator 变成"全部拒绝"）。
"""
from __future__ import annotations

import copy
from typing import Any

import pytest

from backend.app.services.sql_validator_service import (
    SQLValidationCode,
    SQLValidatorService,
)
from backend.app.services.text_to_sql_evaluation_service import (
    StaticEvaluationContextResolver,
    TextToSQLEvaluationCase,
    TextToSQLEvaluationRunner,
    TextToSQLExpectations,
    with_execution_required,
)
from backend.app.services.text_to_sql_service import (
    TextToSQLResult,
    TextToSQLRetryExceededError,
    TextToSQLService,
)
from scripts._text_to_sql_offline_bindings import build_offline_project_bindings

# ============================================================
# 危险 SQL 样例（均使用当前真实存在的表 / 字段）
# ============================================================

QUESTION = "查询知识文档列表"

SQL_DELETE = "DELETE FROM knowledge_document"
SQL_UPDATE = "UPDATE knowledge_document SET title = 'x'"
SQL_INSERT = "INSERT INTO knowledge_document (title) VALUES ('x')"
SQL_DDL_DROP = "DROP TABLE knowledge_document"
SQL_MULTI = "SELECT id FROM knowledge_document LIMIT 1; DELETE FROM knowledge_document"
SQL_CTE_DELETE = (
    "WITH x AS (DELETE FROM knowledge_document RETURNING id) SELECT * FROM x"
)
SQL_SELECT_OK = "SELECT id FROM knowledge_document LIMIT 1"

DANGEROUS_CASES: tuple[tuple[str, str, str], ...] = (
    ("delete", SQL_DELETE, SQLValidationCode.NON_READ_ONLY.value),
    ("update", SQL_UPDATE, SQLValidationCode.NON_READ_ONLY.value),
    ("insert", SQL_INSERT, SQLValidationCode.NON_READ_ONLY.value),
    ("ddl_drop", SQL_DDL_DROP, SQLValidationCode.DANGEROUS_OPERATION.value),
    ("multi_statement", SQL_MULTI, SQLValidationCode.MULTI_STATEMENT.value),
    ("cte_delete", SQL_CTE_DELETE, SQLValidationCode.NON_READ_ONLY.value),
)


# ============================================================
# Fake 组件（只允许 Generator / Executor / LLM 是 Fake）
# ============================================================

class SpyExecutor:
    """只记录调用，不执行任何 SQL（§十一：绝不连真实库）。"""

    def __init__(self) -> None:
        self.call_count: int = 0
        self.executed_sqls: list[str] = []

    async def execute(
        self,
        sql: str,
        *,
        schema: Any = None,
        allowed_tables: Any = None,
        max_rows: int | None = None,
    ) -> Any:
        self.call_count += 1
        self.executed_sqls.append(sql)
        return []


class StubDangerousGenerator:
    """强制返回指定危险 SQL 的 Stub Generator。

    ``validated=True`` 是**故意**伪造的（模拟一个"声称自己已校验"的
    生成器）——Runner 必须不信任它，仍然自己跑真实 Validator（3.9.3 纪律）。
    """

    def __init__(self, sql: str) -> None:
        self._sql = sql
        self.call_count: int = 0

    async def generate(
        self,
        question: str,
        *,
        database_context: str,
        allowed_tables: Any = None,
        schema: Any = None,
        max_rows: int = 1000,
    ) -> TextToSQLResult:
        self.call_count += 1
        return TextToSQLResult(
            question=question,
            sql=self._sql,
            attempts=1,
            validated=True,
            referenced_tables=tuple(allowed_tables or ()),
        )


class FakeLLMClient:
    """按脚本回放的 LLM Client（不发起任何网络请求）。"""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[list[dict[str, Any]]] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    async def chat(self, messages: list[dict[str, Any]], *, tools: Any = None
                   ) -> str:
        self.calls.append(copy.deepcopy(messages))
        if not self._responses:
            return ""
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


# ============================================================
# 构造工具
# ============================================================

def make_case(
    case_id: str = "security_case", *, require_execution: bool = True
) -> TextToSQLEvaluationCase:
    """构造被测 case；默认打开 ``must_execute`` 以验证执行前置拦截。"""
    case = TextToSQLEvaluationCase(
        case_id=case_id,
        question=QUESTION,
        project_id="vietnam-wms",
        expected=TextToSQLExpectations(),
    )
    return with_execution_required(case) if require_execution else case


def make_resolver() -> StaticEvaluationContextResolver:
    """真实 Resolver（真实 Selector / Filter / Composer + 离线 Schema）。"""
    return StaticEvaluationContextResolver(
        bindings=build_offline_project_bindings()
    )


async def run_through_runner(
    sql: str, *, require_execution: bool = True
) -> tuple[Any, SpyExecutor, StubDangerousGenerator]:
    """危险 SQL → 真实 Runner + 真实 Validator + Spy Executor。"""
    generator = StubDangerousGenerator(sql)
    executor = SpyExecutor()
    runner = TextToSQLEvaluationRunner(
        generator=generator,
        context_resolver=make_resolver(),
        executor=executor,
    )
    result = await runner.run_case(make_case(require_execution=require_execution))
    return result, executor, generator


# ============================================================
# 1. 危险 SQL → Validator Reject → Executor 0 calls
# ============================================================

class TestDangerousSqlRejectedBeforeExecutor:
    async def test_delete_rejected_before_executor(self) -> None:
        """§五：DELETE 专项。"""
        result, executor, _ = await run_through_runner(SQL_DELETE)
        assert result.validation_passed is False
        assert SQLValidationCode.NON_READ_ONLY.value in (
            result.validation_error_codes
        )
        assert executor.call_count == 0

    async def test_update_rejected_before_executor(self) -> None:
        """§六：UPDATE 专项。"""
        result, executor, _ = await run_through_runner(SQL_UPDATE)
        assert result.validation_passed is False
        assert SQLValidationCode.NON_READ_ONLY.value in (
            result.validation_error_codes
        )
        assert executor.call_count == 0

    async def test_insert_rejected_before_executor(self) -> None:
        """§七：INSERT 专项。"""
        result, executor, _ = await run_through_runner(SQL_INSERT)
        assert result.validation_passed is False
        assert SQLValidationCode.NON_READ_ONLY.value in (
            result.validation_error_codes
        )
        assert executor.call_count == 0

    async def test_ddl_rejected_before_executor(self) -> None:
        """§八：DROP TABLE（Validator 已明确禁止 DDL）。"""
        result, executor, _ = await run_through_runner(SQL_DDL_DROP)
        assert result.validation_passed is False
        assert SQLValidationCode.DANGEROUS_OPERATION.value in (
            result.validation_error_codes
        )
        assert executor.call_count == 0

    async def test_multi_statement_rejected_before_executor(self) -> None:
        """§14.1：SELECT + DELETE 多语句。"""
        result, executor, _ = await run_through_runner(SQL_MULTI)
        assert result.validation_passed is False
        codes = result.validation_error_codes
        assert SQLValidationCode.MULTI_STATEMENT.value in codes
        # 第二条 DELETE 也必须在只读检查中被识别
        assert SQLValidationCode.NON_READ_ONLY.value in codes
        assert executor.call_count == 0

    async def test_cte_wrapped_delete_rejected_before_executor(self) -> None:
        """§14.2：CTE 内嵌 DELETE（sqlglot 可解析，树内节点被捕获）。"""
        result, executor, _ = await run_through_runner(SQL_CTE_DELETE)
        assert result.validation_passed is False
        assert SQLValidationCode.NON_READ_ONLY.value in (
            result.validation_error_codes
        )
        assert executor.call_count == 0


# ============================================================
# 2. 正向对照：只读 SELECT 仍然可通过
# ============================================================

class TestReadOnlyStillAllowed:
    async def test_read_only_select_passes_and_reaches_executor(self) -> None:
        """§十五：只读 SELECT 必须被接受，并能真正到达 Executor。

        用于证明 Validator 不是"拒绝一切"，从而使上面的拒绝断言有意义。
        """
        result, executor, _ = await run_through_runner(SQL_SELECT_OK)

        assert result.validation_passed is True
        assert result.validation_error_codes == ()
        # 只读 SQL 被允许执行 → Executor 恰好被调用一次
        assert executor.call_count == 1
        assert executor.executed_sqls == [SQL_SELECT_OK]

    async def test_real_validator_accepts_read_only_directly(self) -> None:
        """真实 Validator 直接入口：只读 SELECT 通过（§十三 / §十五）。"""
        validator = SQLValidatorService()
        outcome = validator.validate(SQL_SELECT_OK)
        assert outcome.valid is True
        assert outcome.errors == ()


# ============================================================
# 3. 必须证明"危险 SQL 真的到达了 Validator"
# ============================================================

class TestDangerousSqlReachesRealValidator:
    async def test_runner_uses_real_validator_implementation(self) -> None:
        """§十：Runner 默认校验器必须是真实 SQLValidatorService。"""
        runner = TextToSQLEvaluationRunner(
            generator=StubDangerousGenerator(SQL_SELECT_OK),
            context_resolver=make_resolver(),
        )
        assert isinstance(runner._validator, SQLValidatorService)

    async def test_real_validator_rejects_dangerous_sql_directly(self) -> None:
        """真实 Validator 直接入口：各类危险 SQL 均被拒绝。"""
        validator = SQLValidatorService()
        for _label, sql, expected_code in DANGEROUS_CASES:
            outcome = validator.validate(sql)
            assert outcome.valid is False, sql
            codes = [e.code.value for e in outcome.errors]
            assert expected_code in codes, f"{sql} -> {codes}"

    async def test_generator_output_is_what_validator_inspected(self) -> None:
        """§十七-8：被校验的就是 Stub Generator 产出的那条危险 SQL。"""
        result, _executor, generator = await run_through_runner(SQL_DELETE)
        assert generator.call_count == 1
        assert result.generated_sql == SQL_DELETE
        # Runner 记录了校验错误码 → 说明这条 SQL 确实进了真实 Validator
        assert result.validation_error_codes


# ============================================================
# 4. 真实 TextToSQLService 边界（Fake LLM 产出危险 SQL）
# ============================================================

class TestRealTextToSqlServiceBoundary:
    async def test_real_service_blocks_dangerous_llm_output(self) -> None:
        """真实 TextToSQLService + Fake LLM（返回 DELETE）+ 真实 Validator。

        Generator 内部校验连续拒绝 → 预算耗尽抛 TextToSQLRetryExceededError
        → 没有 SQL 交付给 Runner → Executor 零调用。
        """
        llm = FakeLLMClient([SQL_DELETE] * 3)
        service = TextToSQLService(llm_client=llm)
        executor = SpyExecutor()
        runner = TextToSQLEvaluationRunner(
            generator=service,
            context_resolver=make_resolver(),
            executor=executor,
        )

        result = await runner.run_case(make_case())

        # LLM 被调用（重试预算内），但没有产出可交付 SQL
        assert llm.call_count >= 1
        assert result.error_code == TextToSQLRetryExceededError.__name__
        assert result.generated_sql is None
        assert result.validation_passed is False
        assert executor.call_count == 0


# ============================================================
# 5. 聚合：所有危险类型下 Executor 始终零调用
# ============================================================

class TestExecutorNeverCalled:
    async def test_single_spy_stays_untouched_across_all_dangerous_types(
        self,
    ) -> None:
        """§九：同一个 Spy 跑完全部危险类型，累计调用必须为 0。"""
        executor = SpyExecutor()
        runner = TextToSQLEvaluationRunner(
            generator=StubDangerousGenerator(SQL_DELETE),
            context_resolver=make_resolver(),
            executor=executor,
        )

        for _label, sql, _code in DANGEROUS_CASES:
            runner_for_sql = TextToSQLEvaluationRunner(
                generator=StubDangerousGenerator(sql),
                context_resolver=make_resolver(),
                executor=executor,
            )
            result = await runner_for_sql.run_case(make_case())
            assert result.validation_passed is False, sql

        assert executor.call_count == 0
        assert executor.executed_sqls == []
