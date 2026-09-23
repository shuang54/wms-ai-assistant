# Phase 3.7.2 — ADR：Schema → Prompt Serializer

日期：2026-09-23
状态：已实施（Phase 3.7.2）

## Context

Phase 3.7.1（Schema Explorer）已经能从 PostgreSQL 得到结构化的
`DatabaseSchema` DTO。但后续 Text-to-SQL 需要把它放进 LLM Prompt，
这要求一种**稳定、Token-aware、LLM-readable** 的文本表示：
裸 dump DTO（repr）不稳定且含噪音，手工临时拼接则不可测试、不可复用。

## Decision

新增独立的 `SchemaSerializer`
（`backend/app/services/schema_serializer_service.py`）：

```text
DatabaseSchema (+ 可选 ProjectContext)
        ↓ SchemaSerializer.serialize()
Prompt Context（str）
```

**负责**：Schema DTO → Prompt 文本；表选择（显式）；字符预算截断。

**不负责**（刻意排除，均为后续阶段）：

- Schema → Business Meaning（绝不猜字段语义：无注释 → `Description: —`）
- Schema → Relevant Tables（不做 LLM 选表）
- Schema → SQL（不做 Text-to-SQL）

Serializer 是 Presentation / Prompt Context 层，不是 Business Semantic Layer。

## 关键设计

### 稳定性

相同输入 → 字节级相同输出。排序规则固定：
table 按 `(schema_name, name)`，column 按 `ordinal_position`，
FK 按 `source_column`。不依赖 dict 顺序 / 时间 / 随机数。

### Budget

`max_chars`（默认 12000）是**字符数近似**，不是精确 Token 数。
当前 MVP 刻意不引入 Tokenizer 依赖——真实企业库（500+ 表）接入后，
如需精确预算再升级（预估收益 vs 依赖成本）。

### 截断

按**整行**截断，绝不产生半截字段行（如 `varchar(2`）：

- 能完整放下的表完整输出；
- 溢出的那张表保留 Table Header + 尽可能多的完整行；
- 其后所有表不再输出；
- 末尾追加 `[Schema truncated: max_chars=N]`（marker 不计入预算）。

### Selection

当前只支持**显式**表选择：`schema.table` 精确匹配 或 裸表名在当前
schema 中匹配；不存在的表抛 `SchemaSerializationError`（不静默忽略）。
未来若需要"问题 → 相关表选择"（LLM / 检索式选表），作为
Serializer 之前的一层独立实现，不在本阶段。

### 安全

- Serializer 是纯函数：不读 .env、不依赖 SQLAlchemy / Session /
  不查数据库（测试静态断言无 `os.environ` / `getenv` / sqlalchemy import）；
- 只输出 ProjectContext 的 `project_name` / `description` 与
  DataSource 的 `name` / `type`——均为非敏感 metadata；
- 绝不输出 password / API Key / 连接串 / DTO repr；
- serialize 不修改输入 DTO（frozen，deepcopy 快照测试锁定）。

## 与既有阶段的关系

```text
Phase 3.7.1    SchemaExplorerService → PostgreSQLMetadataProvider → DatabaseSchema
Phase 3.7.1.1  ProjectContext / DataSource / Provider Protocol
Phase 3.7.2    DatabaseSchema → SchemaSerializer → Prompt Context   ← 本阶段
（未来）       SQL Validator → 只读 Text-to-SQL
```
