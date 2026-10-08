"""敏感标记（Phase 5 P-4 → P-6 提取的**共享最小 deny-list**）。

为什么单独成模块：Tool Result（P-4）与 Agent Decision arguments（P-6）都要做
**同一套**最小凭证检查；把常量放在纯域层可避免"域层 import 持久化边界"。

边界（严格）：

    * 这是**最小** deny-list，**不是** DLP：只拦明显的凭证形状内容；
    * 命中后的处理由调用方决定（P-4：不落 payload；P-6：不落 arguments ⇒
      decision_unavailable）——**永不**改写 / 脱敏 / 截断原内容；
    * 误报 ⇒ fail-closed（宁可不可 Replay，也不落敏感原文）。
"""
from __future__ import annotations

from typing import Final

__all__ = ["SENSITIVE_RESULT_MARKERS", "contains_sensitive_marker"]

#: 敏感标记（小写比较；命中即拒绝落盘）。
SENSITIVE_RESULT_MARKERS: Final[tuple[str, ...]] = (
    "api_key",
    "apikey",
    "api-key",
    "authorization",
    "bearer ",
    "password",
    "passwd",
    "secret",
    "credential",
    "database_url",
    "db_url",
    "postgres://",
    "postgresql://",
    "mysql://",
    "mongodb://",
    "sk-",
)


def contains_sensitive_marker(text: str) -> bool:
    """最小凭证 deny-list 命中判定（大小写不敏感；**不**记录命中的标记）。"""
    lowered = text.lower()
    return any(marker in lowered for marker in SENSITIVE_RESULT_MARKERS)
