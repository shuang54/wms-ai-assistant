"""Phase 3.12 Step 105 — **临时** CI failure probe（**仅存在于临时验证分支，禁止合并**）。

用途：让 Observability Matrix Gate 产生确定性 DRIFT，以验证
Required Status Check 在 failure 时是否阻断合并。

约束（§九）：

```text
deterministic     —— 固定断言，无随机 / 无时间依赖
offline           —— 不连接 DB
zero LLM          —— 无 DeepSeek / 任何模型调用
zero production DB writes —— 不写任何业务或观测表
```
"""

from __future__ import annotations

#: 探针期望值（与实际值**故意不一致** ⇒ 确定性失败）。
_PROBE_EXPECTED = "PASS"
_PROBE_ACTUAL = "DRIFT"


def test_step_105_failure_probe_is_deterministic() -> None:
    """确定性失败探针：**故意**与实际值不一致，用于验证 Required Check 阻断语义。"""
    assert _PROBE_ACTUAL == _PROBE_EXPECTED, (
        f"Step 105 failure probe: expected {_PROBE_EXPECTED!r}, "
        f"observed {_PROBE_ACTUAL!r}"
    )
