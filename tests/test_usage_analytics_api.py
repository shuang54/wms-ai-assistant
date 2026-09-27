"""Usage Analytics HTTP Read API 测试（Phase 3.10.22 Step 2）。

通过 monkeypatch 替换 api.usage._usage_facade 为 FakeUsageFacade，
验证：

    1.  正常返回：200 + total / by_provider / by_model /
        by_provider_model 完整存在
    2.  Filter 透传：7 个 Query 参数 → LLMUsageQueryFilter 字段
        完全一致（含 tz-aware datetime / limit / offset）；
        无参数 → 默认 Filter（引用 Query 层常量）
    3.  单次 Facade 调用：只调用 query_snapshot() 一次，
        不调用 query_summary / query_by_* 便捷方法
    4.  Response Mapping：JSON 与 Service DTO 一致；
        None → null；*_known = False 原样保留
    5.  空数据：200 + 零值 Snapshot + 空数组（不是错误）
    6.  Invalid Filter：limit=0 / limit=101 / offset=-1 /
        from > to / 空白 provider / 空白 request_id / naive datetime
        → 422（由 Filter Contract 拒绝，HTTP 层不复制校验）
    7.  Facade Error：RepositoryError → 502；QueryError → 500；
        Analytics/Aggregation InputError → 500；
        未知 RuntimeError → 原样上抛（Starlette 兜底 500）
    8.  NULL Group：provider / model = None → JSON null
    9.  Pagination：limit / offset 原样透传（无 global_total 概念）
    10. 静态检查（AST，非全文扫描）：usage.py 无 DB / Session /
        Engine / SQL 标识符；`LLMUsageRepositoryError` 是错误映射
        所需的异常类型导入，白名单放行，不构成 DB 访问。

全部 Mock Facade：0 DB / 0 Network / 0 RUN_DB_TESTS / 0 DB writes。
真实链路由 Phase 3.10.17 ~ 3.10.21 的 Service / Runtime / Facade
测试覆盖，本文件只验证 HTTP Contract。
"""
from __future__ import annotations

import ast
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app.api import usage as usage_module
from backend.app.db.llm_usage_repository import LLMUsageRepositoryError
from backend.app.main import app
from backend.app.services.llm_usage_aggregation_service import (
    LLMUsageAggregate,
    LLMUsageAggregationError,
    LLMUsageAggregationInputError,
    ModelUsageAggregate,
    ProviderModelUsageAggregate,
    ProviderUsageAggregate,
)
from backend.app.services.llm_usage_analytics_service import (
    LLMUsageAnalyticsError,
    LLMUsageAnalyticsInputError,
    LLMUsageAnalyticsSnapshot,
)
from backend.app.services.llm_usage_query_service import (
    DEFAULT_QUERY_LIMIT,
    DEFAULT_QUERY_OFFSET,
    LLMUsageQueryError,
    LLMUsageQueryFilter,
    LLMUsageQueryInputError,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_USAGE_MODULE = "backend/app/api/usage.py"
_ENDPOINT = "/api/usage/analytics"


# ============================================================
# Snapshot / Aggregate 构造 helpers（真实 frozen DTO）
# ============================================================

def _aggregate(
    *,
    total_requests: int = 1,
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
    total_tokens: int = 15,
    prompt_known: bool = True,
    completion_known: bool = True,
    total_known: bool = True,
) -> LLMUsageAggregate:
    return LLMUsageAggregate(
        total_requests=total_requests,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        prompt_tokens_known=prompt_known,
        completion_tokens_known=completion_known,
        total_tokens_known=total_known,
    )


def _empty_snapshot() -> LLMUsageAnalyticsSnapshot:
    """零值 Snapshot（与 Analytics 层空输入契约一致）。"""
    return LLMUsageAnalyticsSnapshot(
        total=_aggregate(
            total_requests=0,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
        ),
    )


# ============================================================
# Fake Facade（0 DB / 0 Network）
# ============================================================

class FakeUsageFacade:
    """记录 query_snapshot 调用与收到的 Filter 的 Facade 替身。

    便捷方法（query_summary / query_by_*）被调用即计数并抛
    AssertionError——HTTP 层必须只走 query_snapshot()。
    """

    def __init__(
        self,
        *,
        snapshot: LLMUsageAnalyticsSnapshot | None = None,
        error: Exception | None = None,
    ) -> None:
        self._snapshot = snapshot
        self._error = error
        self.snapshot_calls: list[LLMUsageQueryFilter | None] = []
        self.summary_calls = 0
        self.by_provider_calls = 0
        self.by_model_calls = 0
        self.by_provider_model_calls = 0

    async def query_snapshot(
        self,
        query_filter: LLMUsageQueryFilter | None = None,
    ) -> LLMUsageAnalyticsSnapshot:
        self.snapshot_calls.append(query_filter)
        if self._error is not None:
            raise self._error
        if self._snapshot is None:
            raise AssertionError("FakeUsageFacade 未配置 snapshot")
        return self._snapshot

    async def query_summary(
        self, query_filter: LLMUsageQueryFilter | None = None,
    ) -> LLMUsageAggregate:
        self.summary_calls += 1
        raise AssertionError("HTTP 层不得调用 query_summary()")

    async def query_by_provider(
        self, query_filter: LLMUsageQueryFilter | None = None,
    ) -> tuple[ProviderUsageAggregate, ...]:
        self.by_provider_calls += 1
        raise AssertionError("HTTP 层不得调用 query_by_provider()")

    async def query_by_model(
        self, query_filter: LLMUsageQueryFilter | None = None,
    ) -> tuple[ModelUsageAggregate, ...]:
        self.by_model_calls += 1
        raise AssertionError("HTTP 层不得调用 query_by_model()")

    async def query_by_provider_model(
        self, query_filter: LLMUsageQueryFilter | None = None,
    ) -> tuple[ProviderModelUsageAggregate, ...]:
        self.by_provider_model_calls += 1
        raise AssertionError("HTTP 层不得调用 query_by_provider_model()")

    @property
    def last_filter(self) -> LLMUsageQueryFilter | None:
        return self.snapshot_calls[-1] if self.snapshot_calls else None


@pytest.fixture()
def client(monkeypatch):
    """构造测试客户端工厂；yield (TestClient, FakeUsageFacade)。"""

    @contextmanager
    def _make(
        *,
        snapshot: LLMUsageAnalyticsSnapshot | None = None,
        error: Exception | None = None,
        raise_server_exceptions: bool = True,
    ):
        fake = FakeUsageFacade(snapshot=snapshot, error=error)
        monkeypatch.setattr(usage_module, "_usage_facade", fake)
        with TestClient(
            app, raise_server_exceptions=raise_server_exceptions
        ) as c:
            yield c, fake

    return _make


# ============================================================
# 1. 正常返回
# ============================================================

class TestNormalRequest:
    def test_returns_200_with_full_snapshot(self, client) -> None:
        """GET /api/usage/analytics → 200 + 四个视图完整存在。"""
        snapshot = LLMUsageAnalyticsSnapshot(
            total=_aggregate(
                total_requests=2,
                prompt_tokens=20,
                completion_tokens=10,
                total_tokens=30,
            ),
            by_provider=(
                ProviderUsageAggregate(
                    provider="deepseek", aggregate=_aggregate(),
                ),
            ),
            by_model=(
                ModelUsageAggregate(
                    model="deepseek-chat", aggregate=_aggregate(),
                ),
            ),
            by_provider_model=(
                ProviderModelUsageAggregate(
                    provider="deepseek",
                    model="deepseek-chat",
                    aggregate=_aggregate(),
                ),
            ),
        )
        with client(snapshot=snapshot) as (c, _fake):
            response = c.get(_ENDPOINT)

        assert response.status_code == 200
        payload = response.json()
        assert set(payload) == {
            "total", "by_provider", "by_model", "by_provider_model",
        }
        total = payload["total"]
        assert total["total_requests"] == 2
        assert total["prompt_tokens"] == 20
        assert total["completion_tokens"] == 10
        assert total["total_tokens"] == 30
        assert len(payload["by_provider"]) == 1
        assert payload["by_provider"][0]["provider"] == "deepseek"
        assert (
            payload["by_provider"][0]["aggregate"]["total_requests"] == 1
        )
        assert len(payload["by_model"]) == 1
        assert payload["by_model"][0]["model"] == "deepseek-chat"
        assert len(payload["by_provider_model"]) == 1
        assert payload["by_provider_model"][0]["provider"] == "deepseek"
        assert payload["by_provider_model"][0]["model"] == "deepseek-chat"


# ============================================================
# 2. Filter 透传
# ============================================================

class TestFilterPassthrough:
    def test_all_params_mapped_to_filter(self, client) -> None:
        """7 个 Query 参数 → LLMUsageQueryFilter 字段完全一致。"""
        with client(snapshot=_empty_snapshot()) as (c, fake):
            response = c.get(
                _ENDPOINT,
                params={
                    "request_id": "chatcmpl-abc123",
                    "provider": "deepseek",
                    "model": "deepseek-chat",
                    "created_at_from": "2026-09-01T00:00:00+07:00",
                    "created_at_to": "2026-09-02T00:00:00+07:00",
                    "limit": 10,
                    "offset": 5,
                },
            )

        assert response.status_code == 200
        assert len(fake.snapshot_calls) == 1
        f = fake.last_filter
        assert isinstance(f, LLMUsageQueryFilter)
        assert f.request_id == "chatcmpl-abc123"
        assert f.provider == "deepseek"
        assert f.model == "deepseek-chat"
        assert f.created_at_from == datetime(
            2026, 9, 1, tzinfo=timezone(timedelta(hours=7)),
        )
        assert f.created_at_to == datetime(
            2026, 9, 2, tzinfo=timezone(timedelta(hours=7)),
        )
        assert f.limit == 10
        assert f.offset == 5

    def test_default_filter_when_no_params(self, client) -> None:
        """无参数 → Filter 字段为 None，limit/offset 引用既有常量。"""
        with client(snapshot=_empty_snapshot()) as (c, fake):
            response = c.get(_ENDPOINT)

        assert response.status_code == 200
        f = fake.last_filter
        assert isinstance(f, LLMUsageQueryFilter)
        assert f.request_id is None
        assert f.provider is None
        assert f.model is None
        assert f.created_at_from is None
        assert f.created_at_to is None
        assert f.limit == DEFAULT_QUERY_LIMIT
        assert f.offset == DEFAULT_QUERY_OFFSET


# ============================================================
# 3. 单次 Facade 调用
# ============================================================

class TestSingleFacadeCall:
    def test_only_query_snapshot_called_once(self, client) -> None:
        """只调用 query_snapshot() 一次；便捷方法零调用。"""
        with client(snapshot=_empty_snapshot()) as (c, fake):
            response = c.get(_ENDPOINT)

        assert response.status_code == 200
        assert len(fake.snapshot_calls) == 1
        assert fake.summary_calls == 0
        assert fake.by_provider_calls == 0
        assert fake.by_model_calls == 0
        assert fake.by_provider_model_calls == 0


# ============================================================
# 4. Response Mapping
# ============================================================

class TestResponseMapping:
    def test_json_matches_service_dto(self, client) -> None:
        """JSON 与 Snapshot 逐字段一致；*_known=False 原样保留。"""
        snapshot = LLMUsageAnalyticsSnapshot(
            total=_aggregate(
                total_requests=3,
                prompt_tokens=120,
                completion_tokens=60,
                total_tokens=180,
                completion_known=False,
                total_known=False,
            ),
            by_provider=(
                ProviderUsageAggregate(
                    provider="deepseek",
                    aggregate=_aggregate(
                        prompt_tokens=70,
                        prompt_known=False,
                    ),
                ),
            ),
        )
        with client(snapshot=snapshot) as (c, _fake):
            response = c.get(_ENDPOINT)

        assert response.status_code == 200
        total = response.json()["total"]
        assert total["total_requests"] == 3
        assert total["prompt_tokens"] == 120
        assert total["completion_tokens"] == 60
        assert total["total_tokens"] == 180
        assert total["prompt_tokens_known"] is True
        assert total["completion_tokens_known"] is False
        assert total["total_tokens_known"] is False
        provider_group = response.json()["by_provider"][0]["aggregate"]
        assert provider_group["prompt_tokens"] == 70
        assert provider_group["prompt_tokens_known"] is False


# ============================================================
# 5. 空数据
# ============================================================

class TestEmptySnapshot:
    def test_empty_data_returns_zero_value_snapshot(self, client) -> None:
        """空数据是 200 + 零值 + 空数组，不是错误。"""
        with client(snapshot=_empty_snapshot()) as (c, _fake):
            response = c.get(_ENDPOINT)

        assert response.status_code == 200
        payload = response.json()
        total = payload["total"]
        assert total["total_requests"] == 0
        assert total["prompt_tokens"] == 0
        assert total["completion_tokens"] == 0
        assert total["total_tokens"] == 0
        assert total["prompt_tokens_known"] is True
        assert total["completion_tokens_known"] is True
        assert total["total_tokens_known"] is True
        assert payload["by_provider"] == []
        assert payload["by_model"] == []
        assert payload["by_provider_model"] == []


# ============================================================
# 6. Invalid Filter → 422（Filter Contract 拒绝）
# ============================================================

class TestInvalidFilter:
    @pytest.mark.parametrize(
        "params",
        [
            {"limit": 0},
            {"limit": 101},
            {"offset": -1},
            {
                "created_at_from": "2026-09-02T00:00:00+00:00",
                "created_at_to": "2026-09-01T00:00:00+00:00",
            },
            {"provider": "   "},
            {"model": "\t"},
            {"request_id": ""},
            {"created_at_from": "2026-09-01T00:00:00"},  # naive datetime
        ],
        ids=[
            "limit_zero",
            "limit_over_max",
            "offset_negative",
            "from_after_to",
            "provider_blank",
            "model_tab",
            "request_id_empty",
            "naive_datetime",
        ],
    )
    def test_filter_contract_rejections_map_to_422(
        self, client, params: dict,
    ) -> None:
        """非法参数由 LLMUsageQueryFilter 拒绝 → 422；Facade 不被调用。"""
        with client(snapshot=_empty_snapshot()) as (c, fake):
            response = c.get(_ENDPOINT, params=params)

        assert response.status_code == 422
        assert "detail" in response.json()
        assert fake.snapshot_calls == []

    def test_non_integer_limit_maps_to_422(self, client) -> None:
        """HTTP 类型解析失败（limit=abc）由 FastAPI 返回 422。"""
        with client(snapshot=_empty_snapshot()) as (c, fake):
            response = c.get(_ENDPOINT, params={"limit": "abc"})

        assert response.status_code == 422
        assert fake.snapshot_calls == []


# ============================================================
# 7. Facade Error
# ============================================================

class TestFacadeError:
    def test_repository_error_maps_to_502(self, client) -> None:
        """DB 未配置 / 查询失败 → 502（不伪装成功）。"""
        with client(
            error=LLMUsageRepositoryError("DATABASE_URL 未配置"),
        ) as (c, _fake):
            response = c.get(_ENDPOINT)

        assert response.status_code == 502
        assert "detail" in response.json()

    def test_query_input_error_maps_to_422(self, client) -> None:
        """Facade 原样传播的 InputError → 422（契约一致）。"""
        with client(
            error=LLMUsageQueryInputError("参数非法"),
        ) as (c, _fake):
            response = c.get(_ENDPOINT)

        assert response.status_code == 422

    def test_query_error_maps_to_500(self, client) -> None:
        """Query Service 其余异常 → 500。"""
        with client(error=LLMUsageQueryError("查询服务内部错误")) as (
            c, _fake,
        ):
            response = c.get(_ENDPOINT)

        assert response.status_code == 500

    def test_analytics_input_error_maps_to_500(self, client) -> None:
        """Analytics 内部输入不一致 → 500（非调用方错误）。"""
        with client(
            error=LLMUsageAnalyticsInputError("records 不能为 None"),
        ) as (c, _fake):
            response = c.get(_ENDPOINT)

        assert response.status_code == 500

    def test_aggregation_input_error_maps_to_500(self, client) -> None:
        """Aggregation 内部输入不一致 → 500。"""
        with client(
            error=LLMUsageAggregationInputError("records 必须是 View"),
        ) as (c, _fake):
            response = c.get(_ENDPOINT)

        assert response.status_code == 500

    def test_analytics_error_maps_to_500(self, client) -> None:
        """Analytics 其余异常 → 500。"""
        with client(error=LLMUsageAnalyticsError("analytics 失败")) as (
            c, _fake,
        ):
            response = c.get(_ENDPOINT)

        assert response.status_code == 500

    def test_aggregation_error_maps_to_500(self, client) -> None:
        """Aggregation 其余异常 → 500。"""
        with client(error=LLMUsageAggregationError("aggregation 失败")) as (
            c, _fake,
        ):
            response = c.get(_ENDPOINT)

        assert response.status_code == 500

    def test_unknown_error_bubbles_up_to_500(self, client) -> None:
        """未知异常原样上抛 → Starlette 兜底 500（不吞、不包装）。"""
        with client(
            error=RuntimeError("unexpected boom"),
            raise_server_exceptions=False,
        ) as (c, _fake):
            response = c.get(_ENDPOINT)

        assert response.status_code == 500


# ============================================================
# 8. NULL Group
# ============================================================

class TestNullGroups:
    def test_null_provider_and_model_serialized_as_json_null(
        self, client,
    ) -> None:
        """NULL 分组原样保留为 null，不替换为 "unknown"、不过滤。"""
        snapshot = LLMUsageAnalyticsSnapshot(
            total=_aggregate(total_requests=3),
            by_provider=(
                ProviderUsageAggregate(
                    provider=None, aggregate=_aggregate(),
                ),
                ProviderUsageAggregate(
                    provider="deepseek", aggregate=_aggregate(),
                ),
            ),
            by_model=(
                ModelUsageAggregate(model=None, aggregate=_aggregate()),
            ),
            by_provider_model=(
                ProviderModelUsageAggregate(
                    provider=None, model=None, aggregate=_aggregate(),
                ),
            ),
        )
        with client(snapshot=snapshot) as (c, _fake):
            response = c.get(_ENDPOINT)

        assert response.status_code == 200
        payload = response.json()
        assert payload["by_provider"][0]["provider"] is None
        assert payload["by_provider"][1]["provider"] == "deepseek"
        assert payload["by_model"][0]["model"] is None
        assert payload["by_provider_model"][0]["provider"] is None
        assert payload["by_provider_model"][0]["model"] is None


# ============================================================
# 9. Pagination
# ============================================================

class TestPagination:
    def test_limit_offset_passed_through(self, client) -> None:
        """?limit=10&offset=20 → Filter.limit=10 / Filter.offset=20。"""
        with client(snapshot=_empty_snapshot()) as (c, fake):
            response = c.get(
                _ENDPOINT, params={"limit": 10, "offset": 20},
            )

        assert response.status_code == 200
        f = fake.last_filter
        assert isinstance(f, LLMUsageQueryFilter)
        assert f.limit == 10
        assert f.offset == 20

    def test_response_has_no_global_total_concept(self, client) -> None:
        """响应结构无 global_total / total_count / COUNT(*) 字段。"""
        with client(snapshot=_empty_snapshot()) as (c, _fake):
            response = c.get(_ENDPOINT)

        assert response.status_code == 200
        payload = response.json()
        assert set(payload) == {
            "total", "by_provider", "by_model", "by_provider_model",
        }
        assert "global_total" not in payload["total"]
        assert "total_count" not in payload["total"]


# ============================================================
# 10. 静态检查：API 模块不直接访问 DB
# ============================================================

class TestNoDirectDbAccess:
    """AST 静态检查（非全文扫描；docstring / 注释不算违规）。

    仅针对本次新增的 backend/app/api/usage.py：
    API 层只允许依赖 Facade / Filter / DTO / Mapper。
    `LLMUsageRepositoryError` 是错误映射（502）所需的异常类型导入，
    不构成 DB 访问，白名单放行。
    """

    def _usage_module_ast(self) -> ast.Module:
        path = os.path.join(REPO_ROOT, *(_USAGE_MODULE.split("/")))
        with open(path, encoding="utf-8") as handle:
            return ast.parse(handle.read())

    def test_no_db_infrastructure_imports(self) -> None:
        """无 sqlalchemy / psycopg / redis 等基础设施 import。"""
        tree = self._usage_module_ast()
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

        forbidden = {
            "sqlalchemy", "psycopg", "redis", "celery", "kafka",
            "backend.app.db.session",
        }
        assert not any(name in forbidden for name in imported), imported
        # 唯一允许的 db 层导入：异常类型（错误映射），非 Repository 本体
        repo_imports = [
            name for name in imported if "llm_usage_repository" in name
        ]
        assert repo_imports == ["backend.app.db.llm_usage_repository"]

    def test_no_forbidden_db_identifiers(self) -> None:
        """无 Session / Engine / Connection / Repository 等标识符。

        例外：LLMUsageRepositoryError（异常类型，仅用于错误映射）。
        """
        tree = self._usage_module_ast()

        identifiers: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                identifiers.add(node.id.lower())
            elif isinstance(node, ast.Attribute):
                identifiers.add(node.attr.lower())

        for fragment in (
            "session", "engine", "connection", "execute",
            "sessionmaker",
        ):
            assert not any(
                fragment in ident for ident in identifiers
            ), fragment

        repo_idents = [
            ident for ident in identifiers if "repository" in ident
        ]
        assert repo_idents == ["llmusagerepositoryerror"], repo_idents

    def test_no_sql_string_constants(self) -> None:
        """无 SQL 字符串常量（SELECT / INSERT / UPDATE / DELETE）。"""
        tree = self._usage_module_ast()

        strings: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(
                node.value, str,
            ):
                strings.append(node.value)

        for fragment in ("select ", "insert ", "update ", "delete "):
            assert not any(
                fragment in value.lower() for value in strings
            ), fragment


# ============================================================
# 11. DB Integration（RUN_DB_TESTS=1，Phase 3.10.22 Step 3）
# ============================================================

# 复用 Phase 3.10.17 既有 DB 测试机制（schema ensure / seed 走正式
# 写入路径 / cleanup TRUNCATE），不重造 PostgreSQL / Session fixture。
# 不 monkeypatch _usage_facade——走真实
# Facade → Runtime → Query Service → Repository → PostgreSQL。
from tests.test_llm_usage_query import (  # noqa: E402
    _cleanup as _cleanup_usage_table,
    _count as _count_usage_rows,
    _ensure_schema as _ensure_usage_schema,
    _open_session as _open_usage_session,
    _seed_fixture_rows as _seed_usage_rows,
    requires_db as _requires_db,
)

#: 既有 8 行 fixture 的确定性聚合期望（见 test_llm_usage_query._FIXTURE_ROWS）：
#: total = 8 requests / 38 prompt / 49 completion / 87 total tokens
_FIXTURE_TOTAL_REQUESTS = 8
_FIXTURE_TOTAL_TOKENS = (38, 49, 87)


@_requires_db
class TestUsageAnalyticsApiDatabase:
    """真实 DB 集成：GET /api/usage/analytics → PostgreSQL → JSON。

    复用既有 seed（正式写入路径）+ cleanup（TRUNCATE）机制；
    每个用例记录 API 调用前后行数，证明 API 链路零写入。
    """

    def test_http_reads_real_postgres_full_chain(self) -> None:
        """HTTP 200 + 四视图精确聚合值 + read-only。"""
        engine = _ensure_usage_schema()
        session = _open_usage_session()
        try:
            assert _seed_usage_rows(engine) == _FIXTURE_TOTAL_REQUESTS
            count_before = _count_usage_rows(session)

            with TestClient(app) as c:
                response = c.get(_ENDPOINT, params={"limit": 100})

            count_after = _count_usage_rows(session)
            assert count_before == count_after == _FIXTURE_TOTAL_REQUESTS

            assert response.status_code == 200
            payload = response.json()

            total = payload["total"]
            prompt, completion, tokens = _FIXTURE_TOTAL_TOKENS
            assert total["total_requests"] == _FIXTURE_TOTAL_REQUESTS
            assert total["prompt_tokens"] == prompt
            assert total["completion_tokens"] == completion
            assert total["total_tokens"] == tokens
            assert total["prompt_tokens_known"] is True
            assert total["completion_tokens_known"] is True
            assert total["total_tokens_known"] is True

            # by_provider：provider 升序；deepseek 4 行 / openai 3 行 /
            # anthropic 1 行
            assert [g["provider"] for g in payload["by_provider"]] == [
                "anthropic", "deepseek", "openai",
            ]
            by_provider = {
                g["provider"]: g["aggregate"] for g in payload["by_provider"]
            }
            assert by_provider["deepseek"]["total_requests"] == 4
            assert by_provider["deepseek"]["prompt_tokens"] == 25
            assert by_provider["deepseek"]["completion_tokens"] == 35
            assert by_provider["deepseek"]["total_tokens"] == 60
            assert by_provider["openai"]["total_requests"] == 3
            assert by_provider["openai"]["prompt_tokens"] == 9
            assert by_provider["openai"]["total_tokens"] == 19
            assert by_provider["anthropic"]["total_requests"] == 1
            assert by_provider["anthropic"]["total_tokens"] == 8

            # by_model：5 组，model 升序
            assert [g["model"] for g in payload["by_model"]] == [
                "claude-3", "deepseek-chat", "deepseek-reasoner",
                "gpt-4o", "gpt-4o-mini",
            ]
            by_model = {
                g["model"]: g["aggregate"] for g in payload["by_model"]
            }
            assert by_model["deepseek-chat"]["total_requests"] == 3
            assert by_model["deepseek-chat"]["total_tokens"] == 46
            assert by_model["gpt-4o"]["total_requests"] == 2
            assert by_model["claude-3"]["total_tokens"] == 8

            # by_provider_model：5 组
            assert len(payload["by_provider_model"]) == 5
            pairs = {
                (g["provider"], g["model"]): g["aggregate"]["total_requests"]
                for g in payload["by_provider_model"]
            }
            assert pairs[("deepseek", "deepseek-chat")] == 3
            assert pairs[("deepseek", "deepseek-reasoner")] == 1
            assert pairs[("openai", "gpt-4o")] == 2
            assert pairs[("openai", "gpt-4o-mini")] == 1
            assert pairs[("anthropic", "claude-3")] == 1
        finally:
            session.close()
            assert _cleanup_usage_table(engine) == 0

    def test_pagination_applies_to_real_query(self) -> None:
        """limit / offset 作用于真实 SQL；Analytics = 本页记录。"""
        engine = _ensure_usage_schema()
        session = _open_usage_session()
        try:
            assert _seed_usage_rows(engine) == _FIXTURE_TOTAL_REQUESTS
            count_before = _count_usage_rows(session)

            with TestClient(app) as c:
                page1 = c.get(
                    _ENDPOINT, params={"limit": 1, "offset": 0},
                )
                page2 = c.get(
                    _ENDPOINT, params={"limit": 2, "offset": 6},
                )
                full = c.get(_ENDPOINT, params={"limit": 100})

            count_after = _count_usage_rows(session)
            assert count_before == count_after == _FIXTURE_TOTAL_REQUESTS

            assert page1.status_code == 200
            p1 = page1.json()
            # 本页 1 条记录 → 聚合只覆盖 1 条
            assert p1["total"]["total_requests"] == 1
            assert sum(
                g["aggregate"]["total_requests"]
                for g in p1["by_provider"]
            ) == 1
            assert "global_total" not in p1["total"]
            assert "total_count" not in p1["total"]

            assert page2.status_code == 200
            p2 = page2.json()
            assert p2["total"]["total_requests"] == 2

            assert full.status_code == 200
            assert (
                full.json()["total"]["total_requests"]
                == _FIXTURE_TOTAL_REQUESTS
            )
        finally:
            session.close()
            assert _cleanup_usage_table(engine) == 0

    def test_provider_model_request_filters_hit_real_db(self) -> None:
        """provider / model / request_id 过滤作用于真实 SQL。"""
        engine = _ensure_usage_schema()
        session = _open_usage_session()
        try:
            assert _seed_usage_rows(engine) == _FIXTURE_TOTAL_REQUESTS
            count_before = _count_usage_rows(session)

            with TestClient(app) as c:
                by_provider = c.get(
                    _ENDPOINT,
                    params={"provider": "deepseek", "limit": 100},
                )
                by_model = c.get(
                    _ENDPOINT, params={"model": "gpt-4o", "limit": 100},
                )
                by_pair = c.get(
                    _ENDPOINT,
                    params={
                        "provider": "openai",
                        "model": "gpt-4o",
                        "limit": 100,
                    },
                )
                by_request = c.get(
                    _ENDPOINT, params={"request_id": "req-003"},
                )
                unknown = c.get(
                    _ENDPOINT,
                    params={"provider": "__no_such_provider__"},
                )

            count_after = _count_usage_rows(session)
            assert count_before == count_after == _FIXTURE_TOTAL_REQUESTS

            assert by_provider.status_code == 200
            p = by_provider.json()
            assert p["total"]["total_requests"] == 4
            assert [g["provider"] for g in p["by_provider"]] == ["deepseek"]

            assert by_model.status_code == 200
            assert by_model.json()["total"]["total_requests"] == 2

            assert by_pair.status_code == 200
            assert by_pair.json()["total"]["total_requests"] == 2

            assert by_request.status_code == 200
            rq = by_request.json()
            assert rq["total"]["total_requests"] == 1
            assert rq["total"]["prompt_tokens"] == 5

            # 不存在的 provider → 200 + 空结果（不是错误）
            assert unknown.status_code == 200
            u = unknown.json()
            assert u["total"]["total_requests"] == 0
            assert u["by_provider"] == []
            assert u["by_model"] == []
            assert u["by_provider_model"] == []
        finally:
            session.close()
            assert _cleanup_usage_table(engine) == 0

    def test_time_range_filter_timezone_aware(self) -> None:
        """created_at_from / to（tz-aware）→ 真实 SQL 时间过滤（含边界）。"""
        engine = _ensure_usage_schema()
        session = _open_usage_session()
        try:
            assert _seed_usage_rows(engine) == _FIXTURE_TOTAL_REQUESTS
            count_before = _count_usage_rows(session)

            with TestClient(app) as c:
                ranged = c.get(
                    _ENDPOINT,
                    params={
                        "created_at_from": "2024-01-03T00:00:00+00:00",
                        "created_at_to": "2024-01-05T00:00:00+00:00",
                        "limit": 100,
                    },
                )
                tz_shifted = c.get(
                    _ENDPOINT,
                    params={
                        "created_at_from": "2024-01-03T07:00:00+07:00",
                        "created_at_to": "2024-01-05T07:00:00+07:00",
                        "limit": 100,
                    },
                )
                naive_rejected = c.get(
                    _ENDPOINT,
                    params={"created_at_from": "2024-01-03T00:00:00"},
                )

            count_after = _count_usage_rows(session)
            assert count_before == count_after == _FIXTURE_TOTAL_REQUESTS

            # 01-03（req-003 / req-004）+ 01-04（req-005）+
            # 01-05（request_id=None，含 to 边界）= 4
            assert ranged.status_code == 200
            assert ranged.json()["total"]["total_requests"] == 4

            # 07:00+07:00 与 00:00+00:00 同一时刻 → 结果一致
            assert tz_shifted.status_code == 200
            assert tz_shifted.json()["total"]["total_requests"] == 4

            # naive datetime 被 Filter Contract 拒绝 → 422（无 DB 副作用）
            assert naive_rejected.status_code == 422
        finally:
            session.close()
            assert _cleanup_usage_table(engine) == 0
