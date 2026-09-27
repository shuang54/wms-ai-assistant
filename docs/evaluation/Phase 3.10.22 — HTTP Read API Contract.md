# Phase 3.10.22 — LLM Usage Analytics HTTP Read API Contract（Step 1：设计稿）

> 本文档只记录**设计**，不记录尚未存在的实现。
> Step 1 = 现状勘察 + API Contract 设计；**Python implementation = 0 changes**。

---

## 1. Scope

Phase 3.10.21 已交付只读组合入口 `LLMUsageAnalyticsReadFacade`。本阶段目标是在其上增加**只读 HTTP Application API**：

```text
HTTP Request
    ↓
FastAPI API
    ↓
LLMUsageAnalyticsReadFacade
    ↓
LLMUsageQueryRuntimeBridge
    ↓
LLMUsageQueryService → LLMUsageRepository
    ↓
PostgreSQL
```

本 Step 完成的事项：

* 勘察现有 FastAPI 基础设施与 API 规范；
* 勘察 Facade / Filter / Snapshot 真实接口（以真实代码为准）；
* 设计 `GET /api/usage/analytics` 的 Query 参数映射、Response Contract、Error Mapping、依赖与安全边界、分页语义、测试策略。

本 Step 禁止（全部未做）：修改 Facade / Analytics / Aggregation / Runtime / Query Service / Repository / 数据库 / SQL；不新增 Dashboard / Frontend / Billing / Cache / Queue / Worker / Outbox / 认证 / RBAC。

---

## 2. Existing API Infrastructure（勘察结果）

### 2.1 FastAPI Application

| 项 | 真实结果 |
|---|---|
| Application 入口 | `backend/app/main.py` → `create_app()` 应用工厂 → 模块级 `app = create_app()` |
| Web 框架 | FastAPI（`requirements.txt`: `fastapi>=0.115.0,<1.0`）+ Pydantic 2.x |
| Router | 每个功能一个模块：`backend/app/api/health.py`、`chat.py`、`rag.py`、`tool_chat.py`、`orchestrator_chat.py`；各自定义 `router = APIRouter()` |
| Router 注册 | `app.include_router(xxx.router, prefix="/api", tags=["..."])` —— **统一 `/api` prefix** |
| 现有路径 | `GET /api/health`、`POST /api/chat`、`POST /api/rag/answer`、`POST /api/chat/with-tools`、orchestrator chat |
| API DTO 规范 | **Pydantic BaseModel**（如 `ChatResponse` / `RagAnswerResponse`），字段带 `Field(...)` 描述；Service 层 DTO 是 **frozen dataclass**（项目约定不引入 Pydantic）；API 层用手动映射函数（如 `_to_chat_response(result: RagResponse) -> ChatResponse`）完成 Service DTO → API DTO 转换 |
| Query 参数现状 | 现有业务端点均为 POST + body；`top_k` 等参数用 `Field(ge=..., le=...)` 校验。**项目尚无 GET + query params 的先例**（FastAPI 原生支持，无基础设施障碍） |
| 异常处理 | 端点内 `try/except` 已知异常 → `HTTPException(status_code, detail)`；共享映射模块 `backend/app/api/_rag_error_mapping.py`；**未知异常原样 re-raise**，由 Starlette `ServerErrorMiddleware` 兜底 500（不吞异常、detail 不含密钥 / SQL / traceback） |
| Dependency Injection | **未使用 `Depends`**。现有风格是**模块级单例**（`_chat_service = ChatService()`、`_rag_service = RagService()`），注释明确"测试用 monkeypatch 替换本变量" |
| Authentication | **无**认证中间件 / `Security` / API Key Header（全项目搜索确认；与 MVP 阶段一致） |
| status code 规范 | 输入错误 400/422；上游依赖故障 502；配置缺失 503；内部错误 500（见 `_rag_error_mapping.py` docstring） |
| 测试基础设施 | `fastapi.testclient.TestClient` + `from backend.app.main import app`（`tests/test_health.py`）；`pytest.ini`: `asyncio_mode = auto`；Service 测试用 Fake 注入（如 `tests/test_llm_usage_analytics_facade.py` 的 `FakeQueryRuntime`） |

### 2.2 静态构造安全性（DI 相关）

`backend/app/db/session.py`：`DATABASE_URL` 未配置时**不创建 Engine**，`get_session_factory()` 返回 `None`；Repository 在执行查询时才发现 factory 为 `None` 并抛 `LLMUsageRepositoryError("...DATABASE_URL 未配置...")`。

结论：构造链 `LLMUsageQueryService()` → `LLMUsageQueryRuntimeBridge(...)` → `LLMUsageAnalyticsReadFacade(...)` 在**应用导入期构造是安全的**（不触发 DB 连接），模块级单例可行，与现有 `_chat_service` / `_rag_service` 风格一致。

---

## 3. Facade 真实接口（勘察结果，未做任何修改）

`backend/app/services/llm_usage_analytics_facade.py`：

```python
LLMUsageAnalyticsReadFacade(query_runtime, analytics_service=None)

async query_snapshot(query_filter: LLMUsageQueryFilter | None = None)
    -> LLMUsageAnalyticsSnapshot          # 推荐：一次查询 → 完整四视图
async query_summary(query_filter=None)    -> LLMUsageAggregate
async query_by_provider(query_filter=None) -> tuple[ProviderUsageAggregate, ...]
async query_by_model(query_filter=None)    -> tuple[ModelUsageAggregate, ...]
async query_by_provider_model(query_filter=None) -> tuple[ProviderModelUsageAggregate, ...]
```

行为契约（代码与 docstring 已确立，HTTP 层不得破坏）：

* `query_filter=None` → Runtime 默认 Filter（复用现有分页契约，无第二套常量）；
* 异常原样传播（不包装、不吞掉、不返回 None 伪装成功）；
* 返回 frozen DTO / tuple，绝不重新包装为 dict / list；
* 无状态、无缓存。

---

## 4. Query Filter 真实契约（勘察结果，未做任何修改）

`backend/app/services/llm_usage_query_service.py` → `LLMUsageQueryFilter`（frozen dataclass，校验在 `__post_init__`，先于任何 DB 访问）：

| 字段 | 类型 | Optional | 默认值 | 校验规则 |
|---|---|---|---|---|
| `request_id` | `str \| None` | 是 | `None` | 非空字符串（`strip()` 后非空，空白串拒绝） |
| `provider` | `str \| None` | 是 | `None` | 同上 |
| `model` | `str \| None` | 是 | `None` | 同上 |
| `created_at_from` | `datetime \| None` | 是 | `None` | 必须 **timezone-aware** datetime（naive 拒绝）；语义 `created_at >=`（含边界） |
| `created_at_to` | `datetime \| None` | 是 | `None` | 同上；语义 `created_at <=`（含边界）；`from > to` → `LLMUsageQueryInputError`（不自动交换、不静默修正） |
| `limit` | `int` | 否 | `50` | `1 ~ 100`（`MIN_QUERY_LIMIT=1` / `MAX_QUERY_LIMIT=100`）；bool 排除 |
| `offset` | `int` | 否 | `0` | `>= 0`；bool 排除 |

错误类型：任何参数非法 → `LLMUsageQueryInputError`（`LLMUsageQueryError` 子类），在 Filter 构造时抛出。

**HTTP 层不复制第二套 Filter 逻辑**：Filter 由 HTTP 参数构造，校验完全复用 `__post_init__`。

---

## 5. Analytics Snapshot 真实契约（勘察结果，未做任何修改）

`backend/app/services/llm_usage_analytics_service.py` → `LLMUsageAnalyticsSnapshot`（frozen dataclass）：

```text
LLMUsageAnalyticsSnapshot
├── total             : LLMUsageAggregate
├── by_provider       : tuple[ProviderUsageAggregate, ...]        # provider 升序，None 分组最后
├── by_model          : tuple[ModelUsageAggregate, ...]           # model 升序，None 分组最后
└── by_provider_model : tuple[ProviderModelUsageAggregate, ...]   # (provider, model) 升序
```

`backend/app/services/llm_usage_aggregation_service.py` → `LLMUsageAggregate`（frozen dataclass）：

| 字段 | 类型 | 语义 |
|---|---|---|
| `total_requests` | `int` | 记录条数（不去重） |
| `prompt_tokens` | `int` | 已知值之和（`None` 不计入也不当 0） |
| `completion_tokens` | `int` | 同上 |
| `total_tokens` | `int` | 同上（**独立观测值**，绝不重算 `prompt + completion`） |
| `prompt_tokens_known` | `bool` | `False` = 至少一条记录该列未知（NULL） |
| `completion_tokens_known` | `bool` | 同上 |
| `total_tokens_known` | `bool` | 同上 |

分组 DTO：`ProviderUsageAggregate(provider, aggregate)`、`ModelUsageAggregate(model, aggregate)`、`ProviderModelUsageAggregate(provider, model, aggregate)`——`provider` / `model` 可为 `None`（NULL 作为独立分组保留）。

空输入 → 合法零值 Snapshot（`total_requests=0`、token 和为 0、`*_known=True`）。

---

## 6. Target Endpoint

**优先方案（本阶段唯一端点）：**

```text
GET /api/usage/analytics
        ↓
LLMUsageAnalyticsReadFacade.query_snapshot(filter)
```

一个 API 返回完整 Snapshot（total / by_provider / by_model / by_provider_model）。

**暂不创建**（除非后续真实需求明确要求拆分）：

```text
/api/usage/providers
/api/usage/models
/api/usage/summary
/api/usage/dashboard
```

理由：Facade 的四个便捷方法是单视图快捷方式，各自触发一次 Runtime 查询；`query_snapshot()` 一次查询即得全部视图，单端点避免同请求多次查询数据库，与 Facade "一次查询 → 完整四视图" 的推荐入口一致。

Router 设计（Step 2 实施）：新增 `backend/app/api/usage.py`，`router = APIRouter()`，在 `create_app()` 中 `app.include_router(usage.router, prefix="/api", tags=["usage"])`。

---

## 7. HTTP Query Parameters 与 Filter Mapping

所有参数均复用 `LLMUsageQueryFilter` 字段语义，无新增参数、无别名、无第二套校验：

| HTTP Parameter | → Filter 字段 | HTTP 类型 | 必填 | 缺省 | 说明 |
|---|---|---|---|---|---|
| `request_id` | `request_id` | string | 否 | 不过滤 | 精确匹配；空白串由 Filter 拒绝 |
| `provider` | `provider` | string | 否 | 不过滤 | 精确匹配 |
| `model` | `model` | string | 否 | 不过滤 | 精确匹配 |
| `created_at_from` | `created_at_from` | ISO 8601 datetime | 否 | 不过滤 | **必须带时区偏移**（如 `2026-01-01T00:00:00Z`） |
| `created_at_to` | `created_at_to` | ISO 8601 datetime | 否 | 不过滤 | 同上；`from > to` → 4xx |
| `limit` | `limit` | int | 否 | `50` | `[1, 100]` |
| `offset` | `offset` | int | 否 | `0` | `>= 0` |

示例：

```text
GET /api/usage/analytics?provider=deepseek&model=deepseek-chat&limit=50&offset=0
GET /api/usage/analytics?created_at_from=2026-09-01T00:00:00%2B08:00&limit=100
```

### 不暴露的 Filter 字段

无。`LLMUsageQueryFilter` 的 7 个字段全部为安全只读过滤条件（不含 user / cost / SQL / 排序注入面），全部暴露；未来若 Filter 新增字段，须在 Contract 中重新评估是否暴露。

### 时间参数的时区设计决策

Filter 明确拒绝 naive datetime（项目统一 tz-aware 时间约定）。HTTP 层用 FastAPI/Pydantic 解析 ISO 8601 字符串后直接传给 Filter：

* 客户端提供 naive 时间戳（无时区偏移）→ Filter 抛 `LLMUsageQueryInputError` → HTTP 4xx（错误信息指明必须带时区）；
* 客户端提供非法字符串 → FastAPI 参数解析失败 → 422。

即"必须 tz-aware"的规则**只存在于 Filter 一处**，HTTP 层不重复实现。

### limit / offset 常量引用

HTTP 层缺省值与取值范围**引用** `llm_usage_query_service` 的既有常量（`DEFAULT_QUERY_LIMIT` / `MIN_QUERY_LIMIT` / `MAX_QUERY_LIMIT` / `DEFAULT_QUERY_OFFSET`），不定义第二套魔法数字。

---

## 8. Response Contract（设计）

沿用现有规范：Service 层 frozen dataclass 不动，API 层新增 **Pydantic response model 镜像 Snapshot 结构**，并由手动映射函数（与 `_to_chat_response` / `_to_answer_response` 同风格）完成转换：

```python
# 结构示意（命名与实现留待 Step 2；仅表达 Contract）
class UsageAggregateResponse(BaseModel):        # 镜像 LLMUsageAggregate
    total_requests: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    prompt_tokens_known: bool
    completion_tokens_known: bool
    total_tokens_known: bool

class UsageProviderAggregateResponse(BaseModel):
    provider: str | None                        # NULL 分组保留为 null
    aggregate: UsageAggregateResponse

class UsageModelAggregateResponse(BaseModel):
    model: str | None
    aggregate: UsageAggregateResponse

class UsageProviderModelAggregateResponse(BaseModel):
    provider: str | None
    model: str | None
    aggregate: UsageAggregateResponse

class UsageAnalyticsResponse(BaseModel):        # 镜像 LLMUsageAnalyticsSnapshot
    total: UsageAggregateResponse
    by_provider: list[UsageProviderAggregateResponse]
    by_model: list[UsageModelAggregateResponse]
    by_provider_model: list[UsageProviderModelAggregateResponse]
```

响应示例（`200 OK`）：

```json
{
  "total": {
    "total_requests": 3,
    "prompt_tokens": 1200,
    "completion_tokens": 340,
    "total_tokens": 1540,
    "prompt_tokens_known": true,
    "completion_tokens_known": false,
    "total_tokens_known": false
  },
  "by_provider": [
    {"provider": "deepseek", "aggregate": {"...": "..."}},
    {"provider": null,       "aggregate": {"...": "..."}}
  ],
  "by_model": [],
  "by_provider_model": []
}
```

关键语义：

* `by_*` 为空 Filter 命中 0 条记录时是**空数组**（不是错误）；
* `provider` / `model` 为 `null` 表示该分组的记录在此列上未知——**原样保留 null**，不隐藏、不替换为 `"unknown"` 字符串（延续 "NULL ≠ 0 / 未知信息不丢失" 契约）；
* `*_known=false` 原样返回，调用方据此理解 token 求和只是"已知部分"；
* 不包含 `global_total` / `total_count` / `COUNT(*)` / cost / price / currency（不存在的 Contract 不发明）。

---

## 9. Error Mapping（设计）

沿用项目现有映射原则（`_rag_error_mapping.py` 的语义：输入错误 → 400/422；上游/基础设施故障 → 502；配置缺失 → 503；内部错误 → 500；未知异常原样上抛）：

| Domain / Application Error | 来源 | → HTTP | 理由 |
|---|---|---|---|
| `LLMUsageQueryInputError` | HTTP 参数构造 `LLMUsageQueryFilter` 时被 `__post_init__` 拒绝（空白串、naive datetime、`from > to`、limit 越界、offset 为负） | **422 Unprocessable Entity** | 调用方参数语义非法；与现有 `VectorSearchParameterError → 422` 一致。FastAPI 自身的参数类型解析失败（如 `limit=abc`）同样返回 422，语义一致 |
| `LLMUsageRepositoryError` | DB 未配置 / 查询失败（由 Query Service 原样透传） | **502 Bad Gateway** | 与现有 `VectorSearchError → 502` 一致：读取链路的上游（数据库）故障不伪装成功 |
| `LLMUsageAnalyticsInputError` | Analytics 收到非法输入 | **500 Internal Server Error** | Analytics 输入来自内部 Runtime（非调用方可控），属内部数据不一致；与现有 "内部输入错误 → 500"（如 `RerankerInputError → 500`）一致 |
| `LLMUsageAggregationInputError` | Aggregation 收到非 View 元素 | **500 Internal Server Error** | 同上（Analytics 链路内部） |
| 其他 `LLMUsageQueryError` / `LLMUsageAnalyticsError` / `LLMUsageAggregationError`（非 Input 子类） | Query / Analytics / Aggregation 失败 | **500 Internal Server Error** | 服务内部错误 |
| 未知异常 | — | **原样 re-raise** → Starlette 兜底 500 | 现有规范：不吞异常、不 `except Exception: return ...`；detail 不含 API Key / SQL / 连接串 / traceback |

实现形态（Step 2）：与现有端点一致，在 handler 内 `try/except` 已知异常并映射为 `HTTPException`；可选抽取局部映射函数（如 `_usage_error_to_http`），但**不修改** `_rag_error_mapping.py`（其领域异常与 Usage 无交集，强行复用属于无关耦合）。

---

## 10. Dependency Boundary（设计）

沿用项目模块级单例风格（与 `_chat_service` / `_rag_service` 一致；项目未使用 `Depends`，本阶段**不引入新 DI 机制**）：

```python
# backend/app/api/usage.py（Step 2 实施示意，非本 Step 产物）
_query_service = LLMUsageQueryService()                       # 默认构造 Repository（复用全局 session factory）
_query_runtime = LLMUsageQueryRuntimeBridge(_query_service)   # async 线程边界
_usage_facade = LLMUsageAnalyticsReadFacade(_query_runtime)   # Application Read Facade
```

约束：

* HTTP 层**只依赖 Facade**，不重新拼装 Repository / Service / Runtime 链路（构造一次、模块级持有）；
* 测试通过 `monkeypatch.setattr(usage_module, "_usage_facade", fake)` 替换（现有测试模式，`tests/test_chat_api.py` / `test_rag_api.py` 同款）；
* `DATABASE_URL` 未配置时模块级构造安全（§2.2：session factory 延迟获取），首次请求时才以 `LLMUsageRepositoryError → 502` 暴露。

---

## 11. Security Boundary（设计）

API 层（未来的 `backend/app/api/usage.py`）**只允许**出现：

```text
Request → LLMUsageQueryFilter → Facade → Response
```

明确禁止出现在 API 层：

```text
NO SQL / NO SQLAlchemy Session / NO Engine / NO Connection
NO Repository / NO DB write（端点本身只读）
NO LLM call / NO API key / NO password / NO database URL
```

响应与错误 detail 不含：ORM Model、prompt / messages / response 原文、tool arguments、embedding vector、Authorization、连接串、traceback。响应字段仅为 Snapshot 白名单（§5、§8）。

---

## 12. Pagination Semantics（设计）

保持 Phase 3.10.21 既有语义，**逐字不变**：

```text
HTTP limit / offset
        ↓
LLMUsageQueryFilter（原样传递，无第二套常量）
        ↓
LLMUsageQueryRuntimeBridge
        ↓
当前 page records（list[LLMUsageRecordView]）
        ↓
LLMUsageAnalyticsService.snapshot()
        ↓
Snapshot = 本页记录的 Analytics
```

明确禁止（除非真实 Contract 已存在——已核实不存在）：

```text
global_total / total_count / database_count / COUNT(*)
```

即 `limit=100` 时，`total.total_requests ≤ 100`，响应表示的是**本页 100 条记录**的统计，不是全库统计。该语义将在端点 docstring 与 Response 字段描述中显式说明，避免调用方误读。

---

## 13. Test Strategy（Step 2 实施，本 Step 仅设计）

基础设施（已核实存在）：`fastapi.testclient.TestClient` + `from backend.app.main import app`；`pytest-asyncio` auto 模式；Facade 测试已有 `FakeQueryRuntime` / Recording / Exploding Fake 模式可复用（`tests/test_llm_usage_analytics_facade.py`）；现有 API 测试通过 `monkeypatch` 替换模块级单例（`tests/test_chat_api.py` 风格）。

Step 2 计划覆盖（`tests/test_usage_api.py`，命名遵循现有 `test_*_api.py`）：

1. `GET /api/usage/analytics` 正常返回（200 + Snapshot 结构）；
2. Query 参数正确进入 Filter（Fake Facade / Runtime 断言收到的 `LLMUsageQueryFilter`）；
3. Facade 被调用且**只调用一次**（单查询契约）；
4. 返回内容为 Snapshot 映射（total / by_provider / by_model / by_provider_model）；
5. Invalid Filter（空白串 / naive datetime / `from > to` / limit 越界 / offset 负数）→ 422；
6. Facade Error（Repository / Analytics 异常）→ 502 / 500；
7. 空数据 → 200 + 零值 Snapshot + 空数组；
8. NULL provider / model 分组 → JSON `null` 原样保留；
9. Pagination（limit / offset 透传；Analytics 覆盖本页）；
10. API 不直接访问 DB（静态检查：usage API 模块无 Repository / Session / SQL / Engine 标识符——沿用 `test_llm_usage_analytics_facade.py` 的 AST 静态检查模式）。

---

## 14. Explicit Non-Goals

本阶段（Step 1 与后续 Step 2）明确不做：

```text
Dashboard / Frontend / 图表
Billing / Cost / Pricing
Cache / Redis
Queue / Worker / Outbox / Kafka / Celery
认证 / RBAC / 权限过滤（无 user / tenant 维度——Filter 与表结构均不支持）
/api/usage/providers|models|summary|dashboard 拆分端点
时间聚合（hour / day / week / month）
全库统计（global_total / COUNT(*)）
修改 LLMUsageAnalyticsSnapshot / Filter / Facade / Runtime / Service / Repository / 数据库
引入 Depends / 新 DI 框架 / 认证中间件
```

---

## 15. 勘察发现的问题（不修改，仅记录）

1. **`LLMUsageRepositoryError` docstring 措辞滞后**：`backend/app/db/llm_usage_repository.py:119` docstring 写"持久化失败（事务已回滚）"，但查询路径（`list_records` / `get_by_request_id`）同样使用该异常表示"DB 未配置或查询失败"。
   * 影响：无功能影响；仅文档措辞易误导读者以为该异常只属于写入路径。
   * 建议：未来某个 touching-db 的阶段顺带更新 docstring；本阶段禁止修改。
2. **项目尚无 GET + query params 端点先例**：现有业务端点均为 POST + body。
   * 影响：无基础设施障碍（FastAPI 原生支持）；本端点将成为第一个 GET 查询端点，`test_rag_api.py` 等 POST 模式不能直接照抄，需按 §13 设计 query 参数测试。
3. **无认证 / 无 Depends**：与 MVP 阶段定位一致，本阶段不改变；记录为已知限制而非缺陷。
4. **naive datetime 会在 Filter 层才被拒绝**：FastAPI/Pydantic 能把 naive ISO 字符串解析成 naive datetime，"必须 tz-aware" 的错误只能在 Filter 构造时以 `LLMUsageQueryInputError` 暴露。
   * 影响：无——错误仍返回 422 且信息明确；设计上复用 Filter 校验（单一事实来源）优于在 HTTP 层再写一份 tz 检查。

---

## 16. 实施清单（Step 2 已全部完成，实现与本文档一致）

```text
[ ] backend/app/api/usage.py        —— GET /api/usage/analytics（含 Pydantic response model + 映射函数 + 异常映射）
[ ] backend/app/main.py             —— include_router(usage.router, prefix="/api", tags=["usage"])（唯一允许触碰的既有文件，最小 diff）
[ ] tests/test_usage_api.py         —— §13 全部用例
[ ] docs/api.md                     —— 端点 / 参数 / 响应 / 错误码
[ ] docs/architecture.md            —— §8.22（本 Step 已先行添加，仅描述设计）
```
