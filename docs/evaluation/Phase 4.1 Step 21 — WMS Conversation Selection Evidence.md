# Phase 4.1 Step 21 — WMS Conversation Selection Evidence

- 阶段：Phase 4.1（Conversation / Chat 会话层）
- 类型：**Audit only**（建立 Dataset + 离线 selection 模拟 + preservation evidence；不实现 Selector）
- 前置：Step 17（Selection Contract）+ Step 18（Policy/Budget Ownership）+ Step 19（Budget Unit）+ Step 20（Strategy Decision = Deferred）
- 状态：

```text
Production Code = 0
ContextSelector = Deferred
Truncation = Deferred
Tokenizer = Deferred
Budget Unit = Deferred
Selection Strategy = Deferred
DB = 0 · Network = 0 · LLM = 0
```

---

## 1. Dataset Purpose

数据集：`tests/fixtures/conversation_context/wms_multiturn_conversations.yaml`

性质（严格）：

```text
* synthetic WMS-realistic（合成、贴近 WMS 语境的对话样例）
* not production data（不含真实客户 / 供应商 / 订单号 / 真实库存数量 / 生产数据）
* test / evaluation fixture（**不是** database seed；不进入 backend/）
```

Schema（极简，冻结）：

```yaml
- id: inventory_followup_001
  project_id: vietnam-wms
  turns:
    - role: user
      content: "查询越南一厂当前库存"
    - role: assistant
      content: "越南一厂当前库存约为 1000 件（示例值）。"
    - role: user
      content: "那 B01 呢？"
```

```text
只允许：id / project_id / turns / role / content
禁止：任何派生字段（计数类 / 向量类 / 相关度类 / SQL / 模型标识 等）
role 只允许：user / assistant（不研究 system / tool）
```

约定：每个 case 的**最后一个 turn 是当前问题（user）**；candidate history = `turns[:-1]`
（当前用户 Turn 不进入候选历史 —— 与 Step 14 / 17 的边界一致）。

Loader 校验（缺一即错）：

```text
missing id / missing project_id / empty turns / invalid role / empty content /
duplicate conversation id / 最后一个 turn 非 user
```

---

## 2. Dataset Categories

共 **12 个案例，6 组**（每组 2 个）：

| 组 | 类别 | 案例 | 观察点 |
| --- | --- | --- | --- |
| A | Continuous Follow-up | inventory_followup_001 / inbound_followup_002 | 库存 → 仓库 → 具体物料/单据的连续追问 |
| B | Early Constraint | constraint_factory_003 / constraint_domestic_004 | 后续问题依赖早期约束（越南一厂 / 国产原材料） |
| C | Middle Decision | middle_scope_005 / middle_period_006 | 先宽 → 再限定 → 再继续追问（中间决策） |
| D | Long Turn | long_turn_007 / long_turn_008 | 长业务问题 + 多个短问题（turn 数 ≠ 体量） |
| E | Return to Old Topic | topic_return_009 / topic_return_010 | 主题 A → B → C → 回到 A |
| F | No History Dependency | standalone_011 / standalone_012 | 当前问题本身已经完整 |

---

## 3. Selection Simulation

离线模拟（**test-local，不是 production ContextSelector**；无 DB / 无 LLM / 无 tokenizer）：

```text
Recent N                recent_n(history, n)              （保留最后 N 个 history turn）
Recent + First          recent_n_plus_first(history, n)   （first + last N；不重复 first）
Hybrid                  hybrid_selection(history, n)       （结构 = Recent+First；
                                                            overflow 需要未来 Token Budget）
Relevance/Token = Deferred   relevance_token_selection(...) → NotImplementedError
                             （缺 Tokenizer / Model Context Window / Relevance Model / Budget Unit）
```

模拟参数：`N = 2`（单点窗口；用于暴露"小窗口"下的丢失模式）。

禁止（本阶段）：

```text
token 估算（字符比例换算）/ 真实 tokenizer / relevance 模型 / 真实 LLM
```

---

## 4. Preservation Evidence

指标为**事实型**（不是模型质量评估）：`preserved` / `lost` / `risk` / `n/a`。

### Group A：Continuous Follow-up

| case | strategy | early | middle | recent | old_topic | long_turn_risk |
| --- | --- | --- | --- | --- | --- | --- |
| inventory_followup_001 | Recent N | n/a | n/a | preserved | n/a | n/a |
| inventory_followup_001 | Recent + First | n/a | n/a | preserved | n/a | n/a |
| inventory_followup_001 | Hybrid | n/a | n/a | preserved | n/a | n/a |
| inbound_followup_002 | Recent N | n/a | n/a | preserved | n/a | n/a |
| inbound_followup_002 | Recent + First | n/a | n/a | preserved | n/a | n/a |
| inbound_followup_002 | Hybrid | n/a | n/a | preserved | n/a | n/a |

### Group B：Early Constraint

| case | strategy | early | middle | recent | old_topic | long_turn_risk |
| --- | --- | --- | --- | --- | --- | --- |
| constraint_factory_003 | Recent N | lost | n/a | preserved | n/a | n/a |
| constraint_factory_003 | Recent + First | preserved | n/a | preserved | n/a | n/a |
| constraint_factory_003 | Hybrid | preserved | n/a | preserved | n/a | n/a |
| constraint_domestic_004 | Recent N | lost | n/a | preserved | n/a | n/a |
| constraint_domestic_004 | Recent + First | preserved | n/a | preserved | n/a | n/a |
| constraint_domestic_004 | Hybrid | preserved | n/a | preserved | n/a | n/a |

### Group C：Middle Decision

| case | strategy | early | middle | recent | old_topic | long_turn_risk |
| --- | --- | --- | --- | --- | --- | --- |
| middle_scope_005 | Recent N | n/a | lost | lost | n/a | n/a |
| middle_scope_005 | Recent + First | n/a | lost | lost | n/a | n/a |
| middle_scope_005 | Hybrid | n/a | lost | lost | n/a | n/a |
| middle_period_006 | Recent N | n/a | lost | lost | n/a | n/a |
| middle_period_006 | Recent + First | n/a | lost | lost | n/a | n/a |
| middle_period_006 | Hybrid | n/a | lost | lost | n/a | n/a |

（说明：N=2 时中间决策与"最后相关 turn"同时落在窗口外 —— 两者均为 lost。）

### Group D：Long Turn

| case | strategy | early | middle | recent | old_topic | long_turn_risk |
| --- | --- | --- | --- | --- | --- | --- |
| long_turn_007 | Recent N | lost | n/a | preserved | n/a | risk |
| long_turn_007 | Recent + First | preserved | n/a | preserved | n/a | risk |
| long_turn_007 | Hybrid | preserved | n/a | preserved | n/a | risk |
| long_turn_008 | Recent N | lost | n/a | preserved | n/a | risk |
| long_turn_008 | Recent + First | preserved | n/a | preserved | n/a | risk |
| long_turn_008 | Hybrid | preserved | n/a | preserved | n/a | risk |

字符证据（**非** token 估算）：Group D 案例的平均每 turn 字符显著大于其它组（测试断言 > 3 倍）；
且 `Recent N=2` 在 Group D 上裁掉的字符超过历史总量的四分之三（测试断言 > 3 倍差异）。

### Group E：Return to Old Topic

| case | strategy | early | middle | recent | old_topic | long_turn_risk |
| --- | --- | --- | --- | --- | --- | --- |
| topic_return_009 | Recent N | n/a | n/a | preserved | lost | n/a |
| topic_return_009 | Recent + First | n/a | n/a | preserved | preserved | n/a |
| topic_return_009 | Hybrid | n/a | n/a | preserved | preserved | n/a |
| topic_return_010 | Recent N | n/a | n/a | preserved | lost | n/a |
| topic_return_010 | Recent + First | n/a | n/a | preserved | preserved | n/a |
| topic_return_010 | Hybrid | n/a | n/a | preserved | preserved | n/a |

### Group F：No History Dependency

| case | strategy | early | middle | recent | old_topic | long_turn_risk |
| --- | --- | --- | --- | --- | --- | --- |
| standalone_011 | Recent N | n/a | n/a | n/a | n/a | n/a |
| standalone_011 | Recent + First | n/a | n/a | n/a | n/a | n/a |
| standalone_011 | Hybrid | n/a | n/a | n/a | n/a | n/a |
| standalone_012 | Recent N | n/a | n/a | n/a | n/a | n/a |
| standalone_012 | Recent + First | n/a | n/a | n/a | n/a | n/a |
| standalone_012 | Hybrid | n/a | n/a | n/a | n/a | n/a |

不变量（全部策略、全部案例，测试锁定）：

```text
subsequence / ordering / no duplication / determinism / no mutation / current-user 排除
```

字符增长证据沿用 Step 16（不重新测量）：1 → 26 · 10 → 294 · 50 → 1474 ·
100 → 2949 · 500 → 14749 · 1000 → 29499（chars）。

---

## 5. Key Findings

### 5.1 哪些场景 Recent N 容易失败？

```text
* Early Constraint（Group B）：2/2 lost —— 早期约束 turn 被窗口挤出；
* Return to Old Topic（Group E）：2/2 lost —— 旧主题不在最近窗口内；
* Middle Decision（Group C）：2/2 lost —— 中间决策同样在窗口外；
* Long Turn（Group D）：2/2 risk —— 被裁掉的正是体量最大的长 turn（字符损失巨大）。
```

### 5.2 哪些场景 Recent + First 容易失败？

```text
* Middle Decision（Group C）：2/2 lost —— 中间 turn 既非"首"也非"近"，两种规则都保护不到；
* Long Turn（Group D）：仍然 risk —— first 本身可能是长 turn，体量问题未被解决；
* （Group B / E 在 N=2 下为 preserved：说明 first 规则有效覆盖"早期单点约束"。）
```

### 5.3 哪些场景必须依赖未来 Relevance？

```text
* Group C（中间决策）与 Group E（回到旧主题）：需要"语义相关"而非"位置近"的选择依据；
* Group D（长 turn）：需要从长文本中提取关键约束（语义压缩或相关性过滤）；
* 结论：这类需求指向 Strategy C（Relevance）—— 但其前置（Tokenizer / Relevance Model）
  当前全部缺失，属 Requires future capability。
```

### 5.4 哪些问题与 Budget Unit 无关？

```text
* "选哪些 turn"的语义问题（本节的丢失模式）—— 与 N 的度量口径无关；
* 5 项不变量（subsequence / ordering / no duplication / determinism / no mutation）；
* current-user 排除边界；
* Recent N 与 Recent+First 的规则差异本身。
```

### 5.5 哪些问题必须等待 Tokenizer？

```text
* 量化"保留内容是否超出模型窗口"（当前只有字符长度，不能等同 token）；
* Hybrid 的 overflow 裁剪规则（必须先有 Budget Unit）；
* Strategy C 的 Token Budget 分支；
* 未来任何"容量保证"（capacity guarantee）的验证。
```

---

## 6. Final Decision

```text
Selection Strategy = Deferred
```

Why（为什么仍不足以冻结策略）：

```text
1. Dataset 为合成样例（12 案例）——不是真实生产会话采样；
2. 缺少"丢失关键 turn 之后回答质量如何变化"的标注 → 无法判断丢失的实际影响；
3. N=2 只是单点窗口，未评估 N 的区间（2、4、8）与不同分组的交互；
4. 无质量标注 → 不能证明"选择 Recent N + First"优于其它候选（也不能反向证明）。
```

方向性证据（记录，不作为决策）：

```text
* Group B / E 显示：位置型规则（尤其 Recent N）对"早期单点约束"存在结构性丢失；
* Group C 显示：Recent + First 对小窗口下的"中间决策"同样存在结构性丢失；
* Group D 显示：turn 数不能代表体量，"长 turn"是独立的保护缺口；
* Group F 显示：并非所有场景都需要历史（历史保留必要性是场景相关的）。
```

Missing evidence / Next experiment（建议，不在本 Step 执行）：

```text
1. 扩展到真实（脱敏）多轮 WMS 会话采样，覆盖 6 组分类；
2. 对"关键 turn 被裁掉"的对话做人工影响标注（回答是否因此不可用）；
3. 评估 N ∈ {2, 4, 8} 的保留率区间（仍以字符为口径，不引入 tokenizer），
   并覆盖不同窗口大小与 6 组分类的交互；
4. 若未来 Tokenizer / Relevance 前置满足，再评估 Strategy C / Hybrid 的 overflow 规则。
```

---

## 附：证据锚点

```text
tests/fixtures/conversation_context/wms_multiturn_conversations.yaml   （12 案例 / 6 组）
tests/test_conversation_context_selection_evidence.py                  （loader + 模拟 + 矩阵 + 不变量）
tests/test_conversation_context_selection_evidence_architecture.py     （AST / fixture / production 边界）
```

```text
Production Code = 0 · DB = 0 · Network = 0 · LLM = 0 · Tokenizer = 0
```
