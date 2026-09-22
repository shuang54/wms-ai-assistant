# RAG Retrieval Analysis（Phase 3.5.9）

> 本文档记录 Phase 3.5.8 真实知识库建立后的 Retrieval Baseline 失败分析。
> 所有结论均附带取证数据（Top-5 结果、similarity、chunk 内容），
> 取证脚本为一次性运行（未保留在仓库），底层复用 `VectorSearchService` 真实
> BGE-M3 + pgvector 查询。

## Baseline

```text
Top-K:     5
Cases:     12
Hit:       10
Failed:    2
Hit Rate:  83.33%
```

失败 case：`case_003`、`case_011`。

---

## case_003

### Query

```text
单据归档在哪里处理？
```

### Expected Keywords

```text
["单据归档"]
```

### Top-5 Results（取证）

| rank | chunk_index | similarity | 内容摘要 |
|------|-------------|------------|----------|
| 1 | 2 | 0.5909 | 退货入库 2.3 注意事项（退货数量与单据数量一致…） |
| 2 | 3 | 0.5805 | 销售出库 3.3 注意事项（拣货先进先出…） |
| 3 | 1 | 0.5601 | 采购入库 1.3 注意事项（到货数量与采购订单…） |
| 4 | 4 | 0.5481 | 盘点 5.1 业务说明 |
| 5 | 8 | 0.5404 | 仓库与库位 9.3 库位说明 |

全部 5 条结果均**不包含"归档"知识**，similarity 仅 0.54–0.59（低），
属于典型的"知识不存在时检索返回弱相关内容"。

### Root Cause

**failure_type = DOCUMENT_GAP（类型 A：知识文档缺失）**

证据：

1. 对 `docs/knowledge/wms-basic-operations.md` 全文检索"归档"：**0 命中**。
2. 全文仅有 6 处"单据"字样，均为"退货单据 / 调拨单据 / 单据条码"等语境，
   没有任何章节描述"单据完成后如何归档"。
3. Top-5 结果内容与"归档"业务均无关 —— 向量检索行为正确，
   问题在于知识库确实没有该知识。

### Decision

**不修改检索算法**（检索已正确表达"没有相关知识"）。

按任务书 §八（DOCUMENT_GAP 允许补充通用 WMS 知识），
在知识文档中新增 `## 十、单据归档` 章节（业务说明 / 归档方式 / 注意事项），
内容为通用 WMS 单据归档知识（电子归档 / 纸质归档 / 单据条码对应），
不含任何企业敏感信息与内部规则。

---

## case_011

### Query

```text
采购单如何创建？
```

### Expected Keywords

```text
["采购入库", "采购单"]
```

### Top-5 Results（取证）

| rank | chunk_index | similarity | 内容摘要 |
|------|-------------|------------|----------|
| 1 | 6 | 0.5802 | 工单领料 7.2 操作流程 |
| 2 | 0 | 0.5786 | **采购入库 1.1/1.2（含完整采购入库流程）** |
| 3 | 5 | 0.5472 | 上架 6.2 操作流程 |
| 4 | 1 | 0.5292 | 采购入库 1.3 注意事项 |
| 5 | 3 | 0.4841 | 销售出库 3.3 注意事项 |

### Root Cause

**failure_type = DOCUMENT_GAP + 术语表达缺口（A / C 混合，主导为 A）**

证据：

1. 检索**已经召回**了正确的知识（rank 2 的 chunk_index=0 即采购入库章节，
   `采购入库` 关键词命中）—— 检索能力没有问题。
2. 唯一未命中的关键词是字面 `采购单`：
   知识文档通篇使用 `采购订单`，从未出现"采购单"三个连续字。
   （"采购单"不是"采购订单"的连续子串：采购**订**单 ≠ 采购单。）
3. 文档也确实缺少"采购单从哪里创建（ERP 创建、下推 WMS）"的说明 ——
   1.1 节只说"源头是 ERP 中的采购订单"，未说明创建主体与"采购单"这一常用简称。

说明：这不属于评测器 false negative（类型 D）——
评测器按"全部关键词命中才算成功"的既定标准执行正确；
"采购单"确实不出现在任何 Top-K 结果中，是知识文档的术语覆盖缺口。

### Decision

**不引入 Query Rewrite / 不改评测器 / 不改 evaluation case**。

在知识文档 `一、采购入库 1.1 业务说明` 中补充最小术语说明：

```text
采购单是采购订单的常用简称，两者指同一种单据。
采购单（采购订单）由 ERP（企业 ERP）创建，用于向供应商下达采购需求。
采购单创建后下推到 WMS（仓库管理系统），仓库才会知道货物将要到货。
```

这是通用行业术语知识（采购单 = 采购订单的简称），不是为评测"造词"。

---

## Top-K 离线比较（观察项，不改默认值）

| Top-K | total | matched | hit_rate | 备注 |
|-------|-------|---------|----------|------|
| 3 | 12 | 10 | 83.33% | 优化前：与 K=5 相同 |
| 5 | 12 | 10 | 83.33% | 优化前：当前默认 |
| 10 | 12 | 9* | 75.00% | 优化前：*case_001 遭遇 Embedding API 超时（error 计入 failed），数据被瞬态故障污染，非真实检索信号 |

结论（优化前）：K=3 与 K=5 召回无差异；两个失败 case 与 Top-K 无关（根因均为文档缺口，
失败关键词在任何 K 下都不存在）。**维持默认 Top-K = 5 不变**，
不因 K=10 的污染数据调整任何参数。

优化后干净复测（文档补充后，同一脚本重跑）：

| Top-K | total | matched | hit_rate |
|-------|-------|---------|----------|
| 3 | 12 | 12 | 100.00% |
| 5 | 12 | 12 | 100.00% |
| 10 | 12 | 12 | 100.00% |

维持默认 Top-K = 5。

---

## Optimization Decision

```text
修改内容（仅 1 个文件）：
  docs/knowledge/wms-basic-operations.md
    1. §1.1 增加“采购单（采购订单）”术语说明 → 修 case_011
    2. 新增 §十、单据归档 → 修 case_003

不修改：
  - VectorSearchService（算法 / 参数 / Top-K 均不变）
  - RagEvaluationService（匹配逻辑不变）
  - evaluation_cases.json（不改评测迎合结果）
  - Chunker / Embedding / Prompt / API

Ingestion 更新语义（已核实 KnowledgeIngestionService 源码）：
  - same content_hash  → already_exists（幂等）
  - diff content_hash  → 新建 document（无按文件名替换逻辑）
  因此内容修改后必须先清理被取代的旧 document 再重新导入，
  否则会产生"旧 document + 新 document"并存。
  本阶段知识库仅 1 个 document，采用一次性清理 + 重新导入，
  不改动 ingestion 架构（任务书 §九：先分析，不大改）。
```

预期：Hit Rate 83.33% → 100%（两处均为文档缺口，补齐后关键词可命中）。
以实际评估结果为准，不人为保证。

## Optimized Result

（文档补充 → 清理旧 document_id=1 → 重新导入为 document_id=2 / 11 chunks →
重新执行 `RUN_REAL_RAG_EVAL=1 pytest -q tests/test_rag_evaluation_real.py`）

```text
Top-K:     5
Cases:     12
Hit:       12
Failed:    0
Hit Rate:  100.00%
Improvement: +16.67pp（83.33% → 100%）
```
