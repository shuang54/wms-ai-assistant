"""Project Capabilities（Phase 3.8.2）。

职责（任务书 §四 / §五）：

    ProjectCapabilities 只回答一个问题：

        "当前 Project 允许使用哪些业务能力？"

        Project
         ├── DataSource          （Phase 3.8.1：数据源切换）
         ├── Semantic            （Phase 3.7.3：per-project YAML）
         └── Capabilities        （本阶段：能力开关）
              ├── tool_names            允许的 Tool 名称白名单
              ├── knowledge_enabled     是否允许 RAG
              └── text_to_sql_enabled   是否允许 Text-to-SQL

设计约束（任务书 §五 安全边界）：

- Capability **只描述**，不执行：字段中绝不出现
  ``handler / engine / session / llm / password / api_key /
  connection_string``（有单元测试锁定字段白名单）；
- frozen dataclass：注册后不可变，可安全在进程内传递；
- 项目能力只能由**服务器端 ProjectRegistry** 决定；
  HTTP 请求无法注入 / 覆盖任何能力字段；
- 默认值（``DEFAULT_PROJECT_CAPABILITIES``）等价于 Phase 3.8.2 之前的
  系统行为（任务书 §十三）：get_inventory + Knowledge + Text-to-SQL
  全部开启，保证旧项目 / 旧测试零改动。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, fields
from typing import Final

__all__ = [
    "ProjectCapabilities",
    "ProjectCapabilityError",
    "DEFAULT_TOOL_NAMES",
    "DEFAULT_PROJECT_CAPABILITIES",
]


class ProjectCapabilityError(Exception):
    """ProjectCapabilities 输入校验异常（Phase 3.8.2）。"""


#: Tool 名称白名单（与 ToolRegistry 注册名对齐：snake_case 标识符）
_TOOL_NAME_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z_][a-z0-9_]*$")


#: 当前系统的正式 Tool 集合（任务书 §十三："根据实际 Registry 自动处理，
#: 不要遗漏"）。目前全局 Tool Registry 只有 get_inventory 一个真实 Tool；
#: 未来新增 Tool 时在工厂的 builder 映射中同步注册即可。
DEFAULT_TOOL_NAMES: Final[tuple[str, ...]] = ("get_inventory",)


@dataclass(frozen=True)
class ProjectCapabilities:
    """项目能力配置（frozen，非敏感，只描述不执行）。

    Attributes:
        tool_names:          该项目允许使用的 Tool 名称白名单。
                             空元组 = 该项目不允许任何 Tool。
        knowledge_enabled:   是否允许 RAG（知识库问答）；
                             False 时 Orchestrator 在调用 RagService
                             **之前**拒绝（RagService 0 次调用）。
        text_to_sql_enabled: 是否允许 Text-to-SQL；
                             False 时 Orchestrator 在解析 Schema /
                             生成 / 执行 SQL **之前**拒绝（0 次数据库访问）。
    """

    tool_names: tuple[str, ...] = DEFAULT_TOOL_NAMES
    knowledge_enabled: bool = True
    text_to_sql_enabled: bool = True

    def __post_init__(self) -> None:
        # ---- tool_names ----
        if isinstance(self.tool_names, str) or not isinstance(
            self.tool_names, tuple
        ):
            raise ProjectCapabilityError(
                "tool_names 必须是 tuple[str, ...]"
                f"（当前: {type(self.tool_names).__name__}；"
                f"单个字符串请写成 (name,)）"
            )
        seen: set[str] = set()
        for name in self.tool_names:
            if not isinstance(name, str) or not name.strip():
                raise ProjectCapabilityError(
                    f"tool_names 每项必须是非空 str（当前: {name!r}）"
                )
            if not _TOOL_NAME_RE.match(name):
                raise ProjectCapabilityError(
                    f"tool_names 项 {name!r} 不是合法的 Tool 名称"
                    "（只允许小写字母/数字/下划线）"
                )
            if name in seen:
                raise ProjectCapabilityError(
                    f"tool_names 存在重复项 {name!r}"
                )
            seen.add(name)

        # ---- 布尔开关（严格 bool，拒绝 truthy 非 bool） ----
        for flag_name in ("knowledge_enabled", "text_to_sql_enabled"):
            value = getattr(self, flag_name)
            if not isinstance(value, bool):
                raise ProjectCapabilityError(
                    f"{flag_name} 必须是 bool"
                    f"（当前: {type(value).__name__}）"
                )

    # ---------- 查询辅助 ----------

    def allows_tool(self, tool_name: str) -> bool:
        """某 Tool 是否被该项目允许。"""
        return tool_name in self.tool_names

    # ---------- 自检（防未来意外引入敏感字段） ----------

    def assert_no_sensitive_fields(self) -> None:
        """字段白名单自检（测试调用；拒绝 handler/engine/密码等字段）。"""
        allowed = {"tool_names", "knowledge_enabled", "text_to_sql_enabled"}
        actual = {f.name for f in fields(self)}
        if actual != allowed:
            raise ProjectCapabilityError(
                f"ProjectCapabilities 字段白名单被破坏: {sorted(actual)}"
                f"（允许: {sorted(allowed)}）"
            )


#: 默认能力：等价于 Phase 3.8.2 之前的系统行为（§十三）
DEFAULT_PROJECT_CAPABILITIES: Final[ProjectCapabilities] = ProjectCapabilities(
    tool_names=DEFAULT_TOOL_NAMES,
    knowledge_enabled=True,
    text_to_sql_enabled=True,
)
