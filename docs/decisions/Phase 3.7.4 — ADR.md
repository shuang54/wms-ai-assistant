# Phase 3.7.4 — ADR：Relevant Table Selection（问题 → 相关表选择）

日期：2026-09-23
状态：已实施（Phase 3.7.4）

## Context

Schema Explorer / Serializer / Semantic / Composer 已经能产出完整的
"Schema + Business Semantics" 上下文，但企业数据库可能有
500+ 表 / 5000+ 列，不能每次 Text-to-SQL 都把全部 Schema 放进
Prompt。需要在 Prompt 组装之前先解决：

```text
Question → 哪几张表相关？
```

## Decision

新增独立的相关表选择层 `RelevantTableSelector`
（`backend/app/services/relevant_table_selector.py`）：

```text
User Question
    + DatabaseSchema（候选表唯一来源）
    + ProjectSemantic（加分信号）
        ↓ RuleBasedRelevantTableSelector
TableSelectionResult（top_k 张表 + 可解释得分）
        ↓（未来由上层 Application Service 组合）
SchemaSerializer(tables=selected) + Composer
```

Selector 只回答 **Which tables？**，不生成 Prompt、不序列化——
与 Serializer / Composer 的职责严格分离，上层接口是
`RelevantTableSelector` Protocol，实现可替换。

## Current Strategy（MVP）

规则匹配，可解释、零依赖：

| 命中来源 | 加分 |
|---|---|
| table name | +3 |
| table business_name | +5 |
| table alias | +4 |
| table description | +2 |
| column name | +2 |
| column business_name | +4 |
| column alias | +3 |

匹配规则：

- 标准化：strip + lower + 空白压缩（中文原字符，不引入 jieba）；
- 完整 substring 匹配（术语出现在问题中即命中）；
- 标识符下划线↔空格变体（knowledge_document / knowledge document）；
- 纯数字术语忽略（"10001" 不是业务语义）；
- 长度 < 2 术语忽略（防单字符噪音）。

确定性：score DESC、schema.table ASC 排序；matched_terms 排序去重；
相同输入字节级相同输出。零匹配返回空 selections（正常结果，
不猜表、不返回全部表、不抛异常）——未来 Router 可据此转 RAG /
普通 Chat。

## Important Boundary

```text
LLM calls = 0
Embedding calls = 0
SQL = 0
```

纯内存计算：不查库、不 import SQLAlchemy、不读 .env、
不自动调 SchemaExplorerService（上层负责先取最新 Schema）。
候选表只来自 DatabaseSchema——Semantic 引用了 Schema 中不存在
的表会被直接忽略，绝不返回。

## Future

Protocol 之上可平行替换策略：

```text
RuleBasedRelevantTableSelector   ← 本阶段（唯一实现）
EmbeddingSelector                （问题 + 表语义做向量召回）
LLMSelector                      （把 Schema 摘要交给 LLM 选表）
HybridSelector                   （规则 + 向量 / LLM 混合）
```

上层（未来的 Text-to-SQL Application Service）只依赖 Protocol，
替换策略零改动。数千张表时再考虑 ANN / BM25 索引——当前
O(表数 × 列数) 内存扫描足够。
