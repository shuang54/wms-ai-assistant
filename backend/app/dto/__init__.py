"""DTO 层（Phase 3.12 Step 63 起）。

当前内容很少：只放**跨层共享的稳定数据契约**（不含业务逻辑、
不含 IO、不依赖 Service / DB）。

    AssistantOutcome        —— Assistant-level 业务结果枚举（4 态固定）
    determine_assistant_outcome() —— 纯函数判定（唯一判定入口）

边界：
    * 本层不 import Service / API / DB / LLM；
    * DTO 只描述数据形状与常量，不执行业务。
"""
from __future__ import annotations

__all__ = ["assistant_outcome"]
