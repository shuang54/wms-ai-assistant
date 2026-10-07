"""Agent Tool Result Repository（Phase 5 P-4 — Replayable Tool Result Store）。

最小数据访问层（与 ``agent_step_repository`` / ``agent_run_repository``
同风格、同基础设施）：

    create(...)        —— **幂等**写入一行 Tool Result（first-write-wins）
    create_many(...)   —— **单事务 / 单语句**批量写入

复用基础设施（不新增第二套机制）：

    * Session 工厂：``backend.app.db.session.get_session_factory()``
    * 事务：``with factory() as session, session.begin():``（一次 begin = 一批）
    * ORM：``backend.app.db.models.AgentToolResult``

幂等 / 冲突策略（**由数据库保证**，不是应用内存）：

    **UNIQUE(run_id, step_index)** —— 冻结的 Tool Execution Identity
        → ``INSERT ... ON CONFLICT (run_id, step_index) DO NOTHING RETURNING id``
        → 重复写入：无异常、无 UPDATE、返回 **None** / 空列表（first-write-wins）

    注意：
        * **不是** ``tool_call_id`` 唯一（Provider 每次响应生成新 id ⇒ 不可作身份）；
        * **不是** Conversation ``Idempotency-Key``；
        * **不是** Resume Idempotency（Resume 尚未实现；未来需要
          state_version / claim_token，属独立 Decision）。

写入前校验（触达 DB 之前拦截；任务书 §十一 / §十二 / §十四 / §十五）：

    run_id 1..64 · step_index >= 0 · tool_name 1..128 · project_id <= 128 ·
    tool_call_id <= 128 · error_code <= 64 · result_size_bytes >= 0 ·
    result_truncated / result_unavailable ∈ {True, False} ·
    result_hash 64 hex（若存在）· result_ref 必须为 None（无 artifact store）·
    result_payload 必须已是 JSON 兼容结构（**禁止** ORM model / Session /
    Connection / cursor / HTTP response / exception / 任意自定义对象）

状态不变式（与模型四态一一对应）：

    success=True  → error_code 必须为 None
    success=False → error_code 必须存在（稳定码）+ result_payload 必须为 None
    result_payload 非空 → result_unavailable 必须为 False 且 size > 0
    result_unavailable=True → result_payload 必须为 None
    result_truncated=True   → result_unavailable 必须为 True

不做（本阶段边界）：

    * **不提供** find / get / list / timeline / replay / resume
      （P-4 只建立**写入 Store**；Read / Replay API 属 Checkpoint / Resume 阶段）；
    * 不做 SELECT-then-INSERT / upsert / in-memory 去重；
    * 不引入 Unit of Work / 跨表事务（本表与 run / step 各自 best-effort 独立写入）。

失败语义：``SQLAlchemyError`` → ``AgentToolResultRepositoryError``（事务已回滚），
由调用方（BestEffort recorder）收敛为 warning —— **绝不影响** Tool / Agent 业务结果。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Final, Sequence

from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db.models import AgentToolResult
from backend.app.db.models.agent_tool_result import (
    AGENT_TOOL_RESULT_ERROR_CODE_MAX_LENGTH,
    AGENT_TOOL_RESULT_PROJECT_ID_MAX_LENGTH,
    AGENT_TOOL_RESULT_RESULT_HASH_LENGTH,
    AGENT_TOOL_RESULT_RESULT_REF_MAX_LENGTH,
    AGENT_TOOL_RESULT_RUN_ID_MAX_LENGTH,
    AGENT_TOOL_RESULT_STEP_TOOL_NAME_MAX_LENGTH,
    AGENT_TOOL_RESULT_TOOL_CALL_ID_MAX_LENGTH,
)
from backend.app.db.session import get_session_factory

__all__ = [
    "AgentToolResultRepository",
    "AgentToolResultRepositoryError",
    "AgentToolResultRow",
    "validate_step_index",
    "validate_result_size_bytes",
    "validate_flag",
    "validate_result_hash",
    "validate_result_payload",
]

#: 单批写入上限（防止一次调用构造超长 INSERT；一次 Run ≤ 8 步，留足余量）。
MAX_RESULTS_PER_BATCH: Final[int] = 64

#: payload 嵌套深度上限（防御性：拒绝病态深结构，不改变 JSONB 语义）。
MAX_PAYLOAD_DEPTH: Final[int] = 64

#: SHA-256 十六进制（小写）格式。
_HASH_PATTERN: Final[re.Pattern[str]] = re.compile(r"\A[0-9a-f]{64}\Z")

#: JSON 兼容的标量类型（int 覆盖 bool 之前先判 bool 亦可；此处顺序无关）。
_JSON_SCALARS: Final[tuple[type, ...]] = (str, int, float, bool)

_ALLOWED_PAYLOAD_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "run_id",
        "step_index",
        "tool_name",
        "project_id",
        "success",
        "error_code",
        "result_payload",
        "result_hash",
        "result_size_bytes",
        "result_truncated",
        "result_unavailable",
        "result_ref",
        "tool_call_id",
    }
)


class AgentToolResultRepositoryError(Exception):
    """Tool Result 持久化失败（事务已回滚）。

    调用方必须把它当作**可选链路失败**处理：写路径收敛为 warning，
    不改变已经产生的 Tool / Agent 业务结果，也不触发 retry / 重跑 Tool。
    """


@dataclass(frozen=True)
class AgentToolResultRow:
    """Repository 层写入行（frozen；字段 ≡ 任务书 §五 最小字段集）。

    注意：**不含** request_id / round / duration / arguments / prompt /
    context / exception message（分别属 observation 或不落盘）。
    """

    run_id: str
    step_index: int
    tool_name: str
    success: bool
    result_size_bytes: int
    result_truncated: bool
    result_unavailable: bool
    project_id: str | None = None
    error_code: str | None = None
    result_payload: Any | None = None
    result_hash: str | None = None
    result_ref: str | None = None
    tool_call_id: str | None = None


def validate_step_index(value: object) -> int:
    """校验 step_index（int >= 0；拒绝 bool / float / 负数）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"step_index 必须是 int（当前: {type(value).__name__}）")
    if value < 0:
        raise ValueError(f"step_index 必须 >= 0（当前: {value}）")
    return value


def validate_result_size_bytes(value: object) -> int:
    """校验 result_size_bytes（int >= 0；拒绝 bool / float / 负数）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"result_size_bytes 必须是 int（当前: {type(value).__name__}）"
        )
    if value < 0:
        raise ValueError(f"result_size_bytes 必须 >= 0（当前: {value}）")
    return value


def validate_flag(value: object, *, field: str) -> bool:
    """校验布尔标记（**严格** bool；拒绝 0/1 / "true" 等隐式转换）。"""
    if not isinstance(value, bool):
        raise ValueError(f"{field} 必须是 bool（当前: {type(value).__name__}）")
    return value


def validate_result_hash(value: object) -> str | None:
    """校验 result_hash：None 或 64 位小写十六进制（SHA-256）。"""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"result_hash 必须是 str 或 None（当前: {type(value).__name__}）")
    if len(value) != AGENT_TOOL_RESULT_RESULT_HASH_LENGTH or not _HASH_PATTERN.match(
        value
    ):
        raise ValueError("result_hash 必须是 64 位小写十六进制 SHA-256")
    return value


def validate_result_payload(value: object) -> Any:
    """校验 result_payload：None 或**已是** JSON 兼容结构（递归白名单）。

    允许：dict（key 必须是 str）· list · str · int · float（有限值）· bool · None。
    **拒绝**：ORM model / Session / Connection / cursor / HTTP response /
    exception object / LLMResponse / ToolDefinition / tuple / set / bytes /
    任意自定义对象 —— 绝不“强行序列化”。
    """
    if value is None:
        return None
    _assert_json_compatible(value, depth=0, path="result_payload")
    return value


def _assert_json_compatible(value: object, *, depth: int, path: str) -> None:
    if depth > MAX_PAYLOAD_DEPTH:
        raise ValueError(f"{path} 嵌套深度超出上限（{MAX_PAYLOAD_DEPTH}）")
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} 含非有限 float（NaN / Infinity 不允许）")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} 的 JSON key 必须是 str（当前: {type(key).__name__}）")
            _assert_json_compatible(item, depth=depth + 1, path=f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _assert_json_compatible(item, depth=depth + 1, path=f"{path}[{index}]")
        return
    raise ValueError(
        f"{path} 必须是 JSON 兼容结构（dict/list/str/int/float/bool/None），"
        f"当前: {type(value).__name__}"
    )


class AgentToolResultRepository:
    """``ai_ops.agent_tool_result`` 的最小仓储（P-4：**只写**，无读模型）。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        """构造仓储。

        Args:
            session_factory: Session 工厂；None 时用全局
                ``get_session_factory()``（DATABASE_URL 为空 → 操作时抛
                ``AgentToolResultRepositoryError``）。测试可注入自定义工厂。
        """
        self._session_factory = session_factory

    # ---------- 依赖解析 ----------

    def _get_session_factory(self) -> sessionmaker[Session]:
        if self._session_factory is not None:
            return self._session_factory
        factory = get_session_factory()
        if factory is None:
            raise AgentToolResultRepositoryError(
                "DATABASE_URL 未配置，无法持久化 Agent Tool Result"
            )
        return factory

    # ---------- 校验 ----------

    @staticmethod
    def _optional_text(
        value: object, *, field: str, max_length: int
    ) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} 必须是非空 str 或 None")
        if len(value) > max_length:
            raise ValueError(f"{field} 超出长度上限（{len(value)} > {max_length}）")
        return value

    @classmethod
    def _validated_values(cls, row: AgentToolResultRow) -> dict[str, Any]:
        """逐字段校验并归一为 INSERT values（**单一校验点**）。"""
        extra = set(vars(row)) - _ALLOWED_PAYLOAD_FIELDS
        if extra:  # 防御：dataclass 未来若新增字段，必须显式登记
            raise ValueError(f"AgentToolResultRow 含未登记字段: {sorted(extra)}")

        run_id = row.run_id
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id 必须是非空 str")
        if len(run_id) > AGENT_TOOL_RESULT_RUN_ID_MAX_LENGTH:
            raise ValueError(
                "run_id 超出长度上限"
                f"（{len(run_id)} > {AGENT_TOOL_RESULT_RUN_ID_MAX_LENGTH}）"
            )

        tool_name = row.tool_name
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError("tool_name 必须是非空 str")
        if len(tool_name) > AGENT_TOOL_RESULT_STEP_TOOL_NAME_MAX_LENGTH:
            raise ValueError("tool_name 超出长度上限")

        success = validate_flag(row.success, field="success")
        truncated = validate_flag(row.result_truncated, field="result_truncated")
        unavailable = validate_flag(
            row.result_unavailable, field="result_unavailable"
        )
        size = validate_result_size_bytes(row.result_size_bytes)

        error_code = cls._optional_text(
            row.error_code, field="error_code", max_length=AGENT_TOOL_RESULT_ERROR_CODE_MAX_LENGTH
        )
        payload = validate_result_payload(row.result_payload)
        result_hash = validate_result_hash(row.result_hash)

        if row.result_ref is not None:
            raise ValueError(
                f"result_ref 必须为 None（P-4 无 artifact store；§17 冻结；"
                f"列长上限 {AGENT_TOOL_RESULT_RESULT_REF_MAX_LENGTH} 仅供未来使用）"
            )

        # ---- 状态不变式（与模型四态一一对应）----
        if success:
            if error_code is not None:
                raise ValueError("success=True 时 error_code 必须为 None")
        else:
            if error_code is None:
                raise ValueError("success=False 时必须提供稳定 error_code")
            if payload is not None:
                raise ValueError("success=False 时 result_payload 必须为 None")
        if payload is not None:
            if unavailable:
                raise ValueError(
                    "result_payload 非空时 result_unavailable 必须为 False"
                )
            if truncated:
                raise ValueError(
                    "result_payload 非空时 result_truncated 必须为 False"
                )
            if size <= 0:
                raise ValueError("result_payload 非空时 result_size_bytes 必须 > 0")
        if unavailable and payload is not None:
            raise ValueError("result_unavailable=True 时 result_payload 必须为 None")
        if truncated and not unavailable:
            raise ValueError(
                "result_truncated=True 时 result_unavailable 必须为 True"
                "（截断结果不可 Replay）"
            )

        return {
            "run_id": run_id,
            "step_index": validate_step_index(row.step_index),
            "tool_name": tool_name,
            "project_id": cls._optional_text(
                row.project_id,
                field="project_id",
                max_length=AGENT_TOOL_RESULT_PROJECT_ID_MAX_LENGTH,
            ),
            "success": success,
            "error_code": error_code,
            "result_payload": payload,
            "result_hash": result_hash,
            "result_size_bytes": size,
            "result_truncated": truncated,
            "result_unavailable": unavailable,
            "result_ref": None,
            "tool_call_id": cls._optional_text(
                row.tool_call_id,
                field="tool_call_id",
                max_length=AGENT_TOOL_RESULT_TOOL_CALL_ID_MAX_LENGTH,
            ),
        }

    # ---------- 写入 ----------

    def build_idempotent_insert(self, rows: Sequence[AgentToolResultRow]) -> Any:
        """构造幂等 INSERT（本仓储唯一一处写入 SQL 构造点）。

        ``INSERT INTO ai_ops.agent_tool_result (...) VALUES (...) [, (...)]
          ON CONFLICT (run_id, step_index) DO NOTHING RETURNING id``

        * 一行与多行共用同一语句（多行 = 单次 round trip）；
        * 冲突目标**只**是 execution identity ``(run_id, step_index)`` ——
          **不**对 ``tool_call_id`` 建唯一约束。
        """
        values = [self._validated_values(row) for row in rows]
        return (
            postgresql_insert(AgentToolResult)
            .values(values)
            .on_conflict_do_nothing(index_elements=("run_id", "step_index"))
            .returning(AgentToolResult.id)
        )

    def create(self, row: AgentToolResultRow) -> int | None:
        """**幂等**写入一行 Tool Result。

        Returns:
            新记录 ``id``；``(run_id, step_index)`` 已存在 → ``None``
            （**正常幂等结果，不是错误**）。

        Raises:
            ValueError:                       参数非法（触达 DB 之前拦截）。
            AgentToolResultRepositoryError:   DB 未配置或写入失败（事务已回滚）。
        """
        created = self.create_many((row,))
        return created[0] if created else None

    def create_many(self, rows: Sequence[AgentToolResultRow]) -> list[int]:
        """**单事务 / 单语句**批量幂等写入。

        Args:
            rows: 结果行序列（空序列 → 直接返回 ``[]``，不触达 DB）。

        Returns:
            本次**实际新建**的 id 列表（冲突跳过的行不出现；顺序 = 输入顺序）。

        Raises:
            ValueError:                       参数非法（含空 run_id / 不变式冲突）。
            AgentToolResultRepositoryError:   DB 未配置或写入失败（事务已回滚）。
        """
        if not rows:
            return []
        if len(rows) > MAX_RESULTS_PER_BATCH:
            raise ValueError(
                f"单批结果数量超出上限（{len(rows)} > {MAX_RESULTS_PER_BATCH}）"
            )
        factory = self._get_session_factory()
        statement = self.build_idempotent_insert(rows)
        try:
            with factory() as session, session.begin():
                created_ids = list(session.execute(statement).scalars())
        except SQLAlchemyError as exc:
            raise AgentToolResultRepositoryError(
                f"Agent Tool Result 写入失败: {type(exc).__name__}"
            ) from exc
        return created_ids

    # 说明：本阶段**不提供**任何读方法（无 find / get / list / timeline /
    # replay / resume）—— Read / Replay 属 Checkpoint / Resume 阶段；
    # 测试直接经 SQLAlchemy 查询（不改生产读边界）。
