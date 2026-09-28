"""LLM Usage Repository（Phase 3.10.14；3.10.16 幂等写入；
3.10.17 只读查询）。

最小数据访问层（项目此前**没有** repository pattern，本文件是
Phase 3.10.14 引入的第一个 repository，职责刻意保持最小）：

    create(...)            —— 写入一条 request-level Usage Record（幂等）
    get_by_request_id(...) —— 只读：按 request_id 取一条（§十六）
    list_records(...)      —— 只读：过滤 + 分页 + 稳定排序（§十七 ~ §十八）

复用现有数据库基础设施（任务书 §四）：

    - Session 工厂：`backend.app.db.session.get_session_factory()`
    - 事务：`with factory() as session, session.begin():`（与
      KnowledgeIngestionService 写库路径完全一致）
    - ORM Model：`backend.app.db.models.LLMUsageRecord`

Phase 3.10.16 幂等语义（由**数据库**保证，不是应用内存）：

    request_id IS NOT NULL
        → UNIQUE PARTIAL INDEX 约束一个 request_id 最多一行
        → 写入用 `INSERT ... ON CONFLICT (request_id)
            WHERE request_id IS NOT NULL DO NOTHING RETURNING id`
        → 重复一次 Overservation：无异常、无 UPDATE、**返回 None**
          （first-write-wins，已有行保持不变）

    request_id IS NULL
        → partial index 不覆盖 NULL → 每次写入都产生新行
          （有意设计：NULL = 没有幂等身份）

不做的（§六 ~ §十二 / §二十四）：

    * 不用 SELECT-then-INSERT 做幂等（并发下仍会重复）；
    * 不用 IntegrityError + rollback 作为正常控制流；
    * 不用 DO UPDATE / upsert（禁止覆盖已有 usage 事实）；
    * 不在本层 / runtime bridge / service 里放 in-memory set / dict /
      lock 做去重（进程重启失效且无法跨进程）。

不提供（本阶段不做）：

    list_by_user() / list_by_project() / daily_usage() /
    monthly_usage() / aggregate() / sum_tokens() / sum_cost()

失败语义：SQLAlchemyError → `LLMUsageRepositoryError`（事务已回滚），
由调用方（persistence sink）转 warning，**绝不影响 LLM 业务结果**。

Phase 3.10.17 只读边界（Read Boundary）：

    * 只增加 SELECT，不增加任何 INSERT / UPDATE / DELETE；
    * 显式选择 8 个字段，**不用 `SELECT *`**——未来表结构新增字段时
      Query Boundary 不会静默泄露新字段（§十八）；
    * 过滤全部下推到 PostgreSQL（WHERE / ORDER BY / LIMIT / OFFSET），
      不在 Python 里做二次过滤（§十七）；
    * 排序固定 `created_at DESC, id DESC`——带 LIMIT/OFFSET 的查询若
      缺少稳定排序，返回顺序是不确定的（§十 / §二十九）；
    * 返回 Internal Row record `LLMUsageRecordRow`，**不返回 ORM 对象**
      （§十九）；上层 Query Service 再把它转成 `LLMUsageRecordView`；
    * 读路径不开启写事务（`with factory() as session:`，无 `begin()`），
      Session 生命周期仍是"一次操作一个 Session"（§二十四）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db.models import LLMUsageRecord
from backend.app.db.models.llm_usage_record import LLM_USAGE_REQUEST_ID_PREDICATE
from backend.app.db.session import get_session_factory

__all__ = [
    "LLMUsageRepository",
    "LLMUsageRepositoryError",
    "LLMUsageRecordRow",
    "LLM_USAGE_READ_COLUMNS",
]

#: ON CONFLICT target —— 必须与 partial unique index 的谓词一致。
_REQUEST_ID_CONFLICT_TARGET = ("request_id",)
_REQUEST_ID_INDEX_WHERE = text(LLM_USAGE_REQUEST_ID_PREDICATE)

#: 只读查询允许返回的字段（§十八：显式列，不用 SELECT *）。
LLM_USAGE_READ_COLUMNS: Final[tuple[str, ...]] = (
    "id",
    "request_id",
    "provider",
    "model",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "created_at",
)


@dataclass(frozen=True)
class LLMUsageRecordRow:
    """Repository 层的内部只读记录（**不是 ORM 对象**，§十九）。

    ORM Model 不得离开 db 层：上层只拿到这个 plain record，
    再由 Query Service 转换成安全 DTO（`LLMUsageRecordView`）。
    字段严格等于 §五 / §七 白名单。
    """

    id: int
    request_id: str | None
    provider: str | None
    model: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    created_at: datetime


#: Assistant Trace ID 长度上限（与 ORM 列 VARCHAR(128) 一致；Step 36）。
_ASSISTANT_REQUEST_ID_MAX_LENGTH: Final[int] = 128


def _validate_optional_assistant_request_id(value: object) -> str | None:
    """可选 Assistant Trace ID（``str | None``；Step 36）。

    * ``None`` = 未绑定 Assistant Trace Scope（旧链路 / 历史行为）→ 写 NULL；
    * 非空 ``str`` = 该次 Assistant 请求的 request_id；
    * 其它类型 / 空字符串 / 超长 → ``ValueError``（触达 DB 之前拦截）；
    * **不生成 / 不推断 / 不截断 / 不转换**（不把 request_id 当替代值）。
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(
            "assistant_request_id 必须是 str 或 None"
            f"（当前: {type(value).__name__}）"
        )
    if not value:
        raise ValueError("assistant_request_id 不能为空字符串（None 表示未绑定）")
    if len(value) > _ASSISTANT_REQUEST_ID_MAX_LENGTH:
        raise ValueError(
            "assistant_request_id 超出长度上限 "
            f"（{len(value)} > {_ASSISTANT_REQUEST_ID_MAX_LENGTH}）"
        )
    return value


class LLMUsageRepositoryError(Exception):
    """LLM Usage 持久化失败（事务已回滚）。

    调用方（DatabaseLLMAccountingSink）捕获后仅记录 warning：
    LLM 业务结果不因 usage 持久化失败而改变。
    """


class LLMUsageRepository:
    """`llm_usage_record` 表的最小写入仓储。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        """构造仓储。

        Args:
            session_factory: Session 工厂；None 时用全局
                `get_session_factory()`（DATABASE_URL 为空 → create 时抛
                LLMUsageRepositoryError）。测试可注入自定义工厂。
        """
        self._session_factory = session_factory

    # ---------- 依赖解析 ----------

    def _get_session_factory(self) -> sessionmaker[Session]:
        if self._session_factory is not None:
            return self._session_factory
        factory = get_session_factory()
        if factory is None:
            raise LLMUsageRepositoryError(
                "DATABASE_URL 未配置，无法持久化 LLM Usage"
            )
        return factory

    # ---------- 写入 ----------

    def build_idempotent_insert(
        self,
        *,
        request_id: str | None,
        provider: str | None,
        model: str | None,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        total_tokens: int | None,
        assistant_request_id: str | None = None,
    ) -> Any:
        """构造幂等 INSERT（本仓储唯一一处写入 SQL 构造点）。

        INSERT INTO ai_ops.llm_usage_record (...)
        ON CONFLICT (request_id) WHERE request_id IS NOT NULL
        DO NOTHING
        RETURNING id

        * `DO NOTHING`（不是 `DO UPDATE`）：first-write-wins，
          重复写入不得覆盖已有 provider / model / token；
        * `index_where` 必须与 partial unique index 谓词一致；
        * 写入字段严格等于 `_WRITE_FIELDS`（§二十三）；
        * Phase 3.12 Step 36：`assistant_request_id`（Assistant Trace 关联）
          与 `request_id`（Provider 请求 ID）是**两个不同维度**，
          互不覆盖；未绑定 Scope → None（写 NULL）。
        """
        return (
            postgresql_insert(LLMUsageRecord)
            .values(
                request_id=request_id,
                provider=provider,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                assistant_request_id=assistant_request_id,
            )
            .on_conflict_do_nothing(
                index_elements=_REQUEST_ID_CONFLICT_TARGET,
                index_where=_REQUEST_ID_INDEX_WHERE,
            )
            .returning(LLMUsageRecord.id)
        )

    def create(
        self,
        *,
        request_id: str | None,
        provider: str | None,
        model: str | None,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        total_tokens: int | None,
        assistant_request_id: str | None = None,
    ) -> int | None:
        """**幂等**写入一条 request-level Usage Record。

        值语义由调用方（Persistence Service）保证：
        request_id / provider / model / token 字段均来自
        LLMObservation 与 LLMUsage 原值（NULL 保持 NULL，不补 0）；
        assistant_request_id 来自 Assistant Trace Scope（未绑定 → NULL）。

        Args:
            request_id:           Provider 请求 ID（可 None）。
            provider:             Provider 标识（可 None）。
            model:                实际响应模型（可 None）。
            prompt_tokens:        Prompt token（可 None）。
            completion_tokens:    Completion token（可 None）。
            total_tokens:         总 token（原样，可 None）。
            assistant_request_id: Assistant Trace ID（可 None；**不参与幂等**，
                                  不生成 / 不回填 —— 由 Persistence Service
                                  传入，本层只做类型与长度校验）。

        Returns:
            新记录的 `id`；
            request_id 已存在（ON CONFLICT DO NOTHING）→ `None`
            —— 这是**正常幂等结果，不是错误**，调用方不得把它当失败。

        Raises:
            ValueError:               assistant_request_id 非法
                                      （非 str / 空 / 超长）—— 触达 DB 之前拦截。
            LLMUsageRepositoryError:  DB 未配置或写入失败（事务已回滚）。
        """
        validated_assistant_request_id = _validate_optional_assistant_request_id(
            assistant_request_id
        )
        factory = self._get_session_factory()
        statement = self.build_idempotent_insert(
            request_id=request_id,
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            assistant_request_id=validated_assistant_request_id,
        )
        try:
            with factory() as session, session.begin():
                created_id: int | None = session.execute(
                    statement
                ).scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise LLMUsageRepositoryError(
                f"LLM Usage 写入失败: {type(exc).__name__}"
            ) from exc
        return created_id

    # ---------- Phase 3.10.17：只读查询 ----------

    @staticmethod
    def _read_columns() -> tuple[Any, ...]:
        """显式 SELECT 列（不允许 `SELECT *`）。"""
        return tuple(
            getattr(LLMUsageRecord, name) for name in LLM_USAGE_READ_COLUMNS
        )

    def build_record_select(
        self,
        *,
        request_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        created_at_from: datetime | None = None,
        created_at_to: datetime | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> Any:
        """构造只读 SELECT（唯一一处读 SQL 构造点）。

        * 过滤全部下推 PostgreSQL：`WHERE ... AND ...`（§十七，
          不在 Python 里取回全表再过滤）；
        * 时间范围语义：`created_at >= from` 且 `created_at <= to`
          （含边界，§十三）；
        * 排序固定 `created_at DESC, id DESC`（§十 / §二十九稳定排序）；
        * limit / offset 由调用方（Query Service）校验后传入。
        """
        statement = select(*self._read_columns())
        if request_id is not None:
            statement = statement.where(LLMUsageRecord.request_id == request_id)
        if provider is not None:
            statement = statement.where(LLMUsageRecord.provider == provider)
        if model is not None:
            statement = statement.where(LLMUsageRecord.model == model)
        if created_at_from is not None:
            statement = statement.where(
                LLMUsageRecord.created_at >= created_at_from
            )
        if created_at_to is not None:
            statement = statement.where(
                LLMUsageRecord.created_at <= created_at_to
            )
        statement = statement.order_by(
            LLMUsageRecord.created_at.desc(),
            LLMUsageRecord.id.desc(),
        )
        if limit is not None:
            statement = statement.limit(limit)
        if offset is not None:
            statement = statement.offset(offset)
        return statement

    @staticmethod
    def _to_row(raw: Any) -> LLMUsageRecordRow:
        """Core Row → 内部 record（ORM 不外泄）。"""
        data = raw._mapping  # noqa: SLF001 —— SQLAlchemy Row 的读取约定
        return LLMUsageRecordRow(
            id=data["id"],
            request_id=data["request_id"],
            provider=data["provider"],
            model=data["model"],
            prompt_tokens=data["prompt_tokens"],
            completion_tokens=data["completion_tokens"],
            total_tokens=data["total_tokens"],
            created_at=data["created_at"],
        )

    def list_records(
        self,
        *,
        request_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        created_at_from: datetime | None = None,
        created_at_to: datetime | None = None,
        limit: int,
        offset: int,
    ) -> list[LLMUsageRecordRow]:
        """只读：按过滤条件分页查询 Usage Records。

        全部过滤参数可选；不传任何过滤 = 查全部（仍然有稳定排序 + 分页）。

        Args:
            request_id:      精确匹配 Provider 请求 ID。
            provider:        精确匹配 provider。
            model:           精确匹配 model。
            created_at_from: `created_at >=` 下界（含）。
            created_at_to:   `created_at <=` 上界（含）。
            limit:           每页条数（由 Query Service 校验 1~100）。
            offset:          偏移量（由 Query Service 校验 >= 0）。

        Returns:
            `list[LLMUsageRecordRow]`（按 created_at DESC, id DESC）。

        Raises:
            LLMUsageRepositoryError: DB 未配置或查询失败。
        """
        factory = self._get_session_factory()
        statement = self.build_record_select(
            request_id=request_id,
            provider=provider,
            model=model,
            created_at_from=created_at_from,
            created_at_to=created_at_to,
            limit=limit,
            offset=offset,
        )
        try:
            with factory() as session:
                rows = session.execute(statement).all()
        except SQLAlchemyError as exc:
            raise LLMUsageRepositoryError(
                f"LLM Usage 查询失败: {type(exc).__name__}"
            ) from exc
        return [self._to_row(row) for row in rows]

    def get_by_request_id(self, *, request_id: str) -> LLMUsageRecordRow | None:
        """只读：按 request_id 取一条 Usage Record。

        Phase 3.10.16 的 unique partial index 保证
        `request_id IS NOT NULL` 最多对应一行；因此这里不使用
        `.first()` 静默丢弃多余结果——一旦返回多行，说明数据库契约被
        破坏，必须显式报错（§十六）。

        Args:
            request_id: 非空字符串。

        Returns:
            命中 → `LLMUsageRecordRow`；未命中 → `None`。

        Raises:
            ValueError:                request_id 非法（非字符串 / 空）。
            LLMUsageRepositoryError:   DB 未配置、查询失败，
                                       或同一 request_id 命中多行。
        """
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("request_id 必须是非空字符串")

        rows = self.list_records(
            request_id=request_id,
            limit=2,        # 契约上最多 1 行；取 2 行用于发现契约破坏
            offset=0,
        )
        if len(rows) > 1:
            raise LLMUsageRepositoryError(
                "request_id 唯一契约被破坏"
                f"（命中 {len(rows)} 行）: request_id={request_id}"
            )
        return rows[0] if rows else None
