# Phase 4.1 Step 23 — Selection Evidence Data Readiness

- 阶段：Phase 4.1（Conversation / Chat 会话层）
- 类型：**Audit only**（只审计"是否存在可用于 Selection Strategy 决策的真实/脱敏多轮 WMS 对话数据"）
- 前置：Step 22（Selection Strategy Decision Gate）= `Selection Strategy Decision = BLOCKED`
- 原则：

```text
Production Code = 0 · DB Schema = 0 · DB Writes = 0 · Network = 0 · LLM = 0
不复制任何真实业务数据到 tests/ / docs/ / git
```

---

## 1. Data Source

审计范围（只读）：

```text
tests/ · tests/fixtures/ · docs/ · scripts/ · knowledge/ · （无 data/ 目录）
```

实测发现的**全部**对话/评估类数据来源：

| 来源 | 性质 | 与 Selection Evidence 的关系 |
| --- | --- | --- |
| `tests/fixtures/conversation_context/wms_multiturn_conversations.yaml` | synthetic but WMS-realistic（Step 21 构造） | 唯一多轮对话数据集 |
| `tests/fixtures/rag/evaluation_cases.json` | synthetic（Phase 3.5.7 检索评估用例） | 非对话数据（单轮 query） |
| `tests/fixtures/text_to_sql/baselines/*.json` | synthetic/离线基线（Phase 3.9.x） | 非对话数据（SQL 质量基线） |
| `knowledge/` | 仅 `.gitkeep`（空） | 无内容 |
| `scripts/` | 15 个 Text-to-SQL 分析脚本 | 无对话数据导出 |

**未发现**：

```text
* 本地导出的历史聊天数据；
* 真实脱敏对话样本；
* 已有 evaluation snapshot（真实来源）；
* data/ · datasets/ · samples/ · fixtures/（仓库根级）目录（均不存在）。
```

说明（避免误判）：

```text
* tests/test_real_wms_kb_ingestion.py 是 DB-gated 集成测试
  （RUN_DB_TESTS=1 + DATABASE_URL），使用**外部**知识库 + pgvector，
  向仓库引入的是**知识库文档**且默认 skip —— 与"多轮对话数据"无关；
* .env 为本地文件（非提交物），.env.example 为模板。
```

**分类结论（三值之一）**：

```text
Data Source = SYNTHETIC_ONLY
```

即：repo 内不存在真实业务来源的对话数据（REAL_DEIDENTIFIED 不存在；
也未发现 REAL_NOT_DEIDENTIFIED 形态的真实数据落入仓库）。

Synthetic dataset 规模（实测，仅计数）：

```text
cases = 12
turns = 68（user 40 + assistant 28）
schema = id / project_id / turns[{role, content}]
```

---

## 2. G1

状态：

```text
G1 = BLOCKED
G1 blocked: real de-identified dataset unavailable
```

G1 READY 所需条件（全部满足才可迁移）：

```text
source_type = REAL_DEIDENTIFIED
sample_count > 0
conversation_count > 0
turn_count > 0
contains_sensitive_fields = false
usable_for_evaluation = true
```

阻塞原因：

```text
* 唯一数据集为 SYNTHETIC_ONLY（synthetic dataset ≠ production evidence）；
* 真实业务来源在仓库中不存在（也就无从脱敏）；
* 真实数据获取 + 脱敏流程尚未建立（见 §6 Privacy）。
```

明确禁止：**不得把 synthetic dataset 冒充 G1 evidence**。

---

## 3. G2

状态：

```text
G2 = INSUFFICIENT
```

原因：G2 只能基于**真实数据**计算；当前 G1 BLOCKED → 无可计算对象。

G2 READY 后可获得（仅字符口径）的统计维度：

```text
turn count              → P50 / P90 / P95 / P99 / Max
character count         → P50 / P90 / P95 / P99 / Max
conversation length     → P50 / P90 / P95 / P99 / Max
```

口径约束（冻结）：

```text
* 禁止任何 token 推算（chars / 4、chars * 0.25 等一律禁止）；
* 统计结果只是 evidence，不是 policy：
  即使观察到 P95 = 18 turns，也不能直接推出 max_turns = P95；
  即使观察到 P95 chars = 12000，也不能直接推出 max_chars = P95；
* 本阶段不计算任何分布（无真实数据可算）。
```

---

## 4. G3

状态：

```text
G3 = BLOCKED
```

annotation readiness（**只定义 readiness，不真的给 synthetic 数据重新标注**）：

| Annotation | 判定标准 | 当前可行性 |
| --- | --- | --- |
| `early_constraint` | 关键约束出现在较早历史 turn | 需真实数据 + 标注规范 |
| `middle_decision` | 关键决策出现在中间 turn | 需真实数据 + 标注规范 |
| `recent_context` | 关键上下文主要来自最近 turn | 需真实数据 + 标注规范 |
| `old_topic` | 当前问题重新依赖较早主题 | 需真实数据 + 标注规范 |
| `standalone` | 当前问题基本不依赖历史 | 需真实数据 + 标注规范 |

标注形态（evaluation annotation，**不是** production DTO）：

```yaml
annotations:
  - type: early_constraint
    turn_index: 0
```

阻塞原因：

```text
* 无真实数据（G1 BLOCKED）；
* annotation 规范（判定边界 / 标注人力 / 一致性校验）尚未建立。
```

---

## 5. G4

状态：

```text
G4 = BLOCKED
```

G4 是本阶段最重要的设计审计。未来需要做：

```text
Full History
      ↓
Answer A

Selected History
      ↓
Answer B
```

然后判断：

```text
* 是否改变 business outcome（业务结果）；
* 是否丢失关键约束（lost constraint）；
* 是否改变关键实体（entity）；
* 是否改变用户意图（intent）。
```

G4 READY 至少需要：

```text
真实多轮 history
    +
当前 user turn
    +
可比较的 expected / reference outcome
```

阻塞原因：

```text
* 无真实多轮 history（G1 BLOCKED）；
* 无可靠的 reference outcome（synthetic 数据的 assistant 回复是构造值，
  不能作为质量参照）；
* 离线评测流程（Full vs Selected 的人工/离线比较）尚未建立。
```

本阶段**不调用 LLM**、不搭建评测平台。

---

## 6. Privacy

```text
de-identification mechanism = unavailable
```

审计结论：

```text
* repo 内无真实业务数据，因此当前**不存在**需要脱敏的对象；
* 同时**不存在**脱敏工具链（scripts 中无 deidentify / anonymize /
  pseudonymize / redact 脚本）；
* 未来若引入真实数据：必须先在仓库之外完成脱敏
  （姓名 / 手机号 / 邮箱 / 地址 / 客户名 / 供应商名 / 订单号 / 合同号 /
  SKU / 库存数量 / 生产数据），并且只允许脱敏产物进入 evaluation 数据集；
* 未脱敏的真实数据（REAL_NOT_DEIDENTIFIED）只允许"记录发现"，
  **禁止复制数据**到 tests/ / docs/ / git。
```

---

## 7. Leakage

最小 secret pattern 检查（只检查明确凭据形态，不对业务数据做全文扫描），
覆盖对象：

```text
tests/fixtures/conversation_context/wms_multiturn_conversations.yaml
tests/fixtures/rag/evaluation_cases.json
docs/evaluation/Phase 4.1 Step 21 — WMS Conversation Selection Evidence.md
docs/evaluation/Phase 4.1 Step 22 — Selection Strategy Decision Gate.md
docs/evaluation/Phase 4.1 Step 23 — Selection Evidence Data Readiness.md（本文档）
```

检查模式与结果（模式本身以字形描述，避免文档自触检查）：

```text
API key 前缀形态（sk 连字符…）          → 未发现
PostgreSQL 连接串（内嵌凭据形态）       → 未发现
password 字面量赋值形态                → 未发现
Authorization 请求头赋值形态           → 未发现
api_key 字面量赋值形态                 → 未发现
DATABASE_URL 环境变量赋值形态          → 未发现
```

其他泄露面：

```text
* 仓库内无 .jsonl / .csv 对话导出文件；
* 无 data/ · datasets/ · samples/ · 根级 fixtures/ 目录；
* 唯一 conversation fixture 为 Step 21 synthetic YAML（自声明非生产数据）。
```

结论：**未发现 secrets 或 production data exposure**。

---

## 8. Decision

```text
Selection Strategy remains BLOCKED
```

理由（Step 22 Gate 输入更新后）：

| Gate | 状态 | 变化 |
| --- | --- | --- |
| G1 Real WMS dataset | BLOCKED | 本次审计确认（real de-identified dataset unavailable） |
| G2 Length distribution | INSUFFICIENT | 本次审计确认（无真实数据可算） |
| G3 Constraint annotation | BLOCKED | 本次审计确认（无真实数据 + 无标注规范） |
| G4 Loss impact evidence | BLOCKED | 本次审计确认（无 reference outcome） |
| G5~G9 | BLOCKED | 未变（依赖 G1~G4 / 基础设施决策） |
| G10 Failure semantics | READY | 未变 |

Transition Rule（延续 Step 22）：

```text
G1 = BLOCKED  →  Selection Strategy = BLOCKED（继续保持）

即使 G2 / G3 / G4 全部 READY，也不能冻结策略；
只有 G1 / G2 / G3 / G4 全部 READY 才允许进入下一次 Strategy Decision Gate。

Step 23 READY 仅表示**本审计步骤完成**，
不代表 Selection Strategy READY。
```

本阶段明确不做：

```text
* 不实现 ContextSelector / Recent N / Hybrid / Relevance；
* 不引入 Tokenizer / LLM Relevance；
* 不创建真实对话数据；
* 不修改 synthetic WMS dataset（保持 Step 21 原状）。
```

下一步需要用户/业务侧提供：

```text
G1 blocked: real de-identified dataset unavailable
```

（取得真实脱敏多轮 WMS 会话样本后，方可重新评估 G1~G4。）

---

## 附：证据锚点

```text
tests/test_conversation_selection_evidence_data_audit.py
    → 数据分类（SYNTHETIC_ONLY）/ G1~G4 条件式判定 / 泄露最小检查 /
      离线与生产边界 / 文档完整性
tests/fixtures/conversation_context/wms_multiturn_conversations.yaml
    → 唯一对话数据集（Step 21 synthetic；12 cases / 68 turns）
docs/evaluation/Phase 4.1 Step 22 — Selection Strategy Decision Gate.md
    → Decision Gate 原始状态（G1~G10）
```
