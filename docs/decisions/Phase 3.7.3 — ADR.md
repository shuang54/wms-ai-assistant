# Phase 3.7.3 — ADR：Business Semantic Layer（业务语义层）

日期：2026-09-23
状态：已实施（Phase 3.7.3）

## Context

Phase 3.7.1/3.7.2 之后，链路已能告诉 LLM 数据库**事实**：

```text
public.inventory: material_code / qty / warehouse_id ...
```

但数据库结构 ≠ 业务语义。`material_code` / `qty` 的业务含义
（物料编码 / 库存数量）不应由 LLM 猜测——猜错一次，Text-to-SQL
就错一次，且错误不可审计。

## Decision

增加独立的 Project Business Semantic Layer：

```text
ProjectSemantic（人工配置，semantic/<project_id>.yaml）
    ├── TableSemantic        表：业务名 / 描述 / 别名
    ├── ColumnSemantic      字段：业务名 / 描述 / 别名
    └── BusinessRelationship 业务关系（描述模型，不做推理）
```

配套组件：

| 组件 | 职责 |
|---|---|
| `projects/semantic_loader.py` | project_id → YAML → ProjectSemantic（结构校验，错误绝不静默） |
| `services/semantic_schema_validator.py` | Semantic ↔ DatabaseSchema 对齐校验（全部失配一次返回，不自动修复） |
| `services/business_semantic_serializer.py` | ProjectSemantic → 稳定 Prompt 文本 |
| `services/database_context_composer.py` | 项目头 + Schema 事实 + 业务语义 → 单段 AI Database Context（共享预算） |

## Important Principle

```text
Schema   = database facts（数据库事实）
Semantic = human-defined business meaning（人工定义的业务含义）
```

`SchemaSerializer` 与 `BusinessSemanticSerializer` 严格分工、互不合并：
前者输出事实，后者输出人工定义；未来 Text-to-SQL 的上下文是两者的
**组合**（Composer 拼接），而不是任何一方的职责扩张。

## Project Isolation

语义属于 Project，不属于 AI Core：

- 语义存放在 `backend/app/projects/semantic/vietnam-wms.yaml`——
  换项目 = 换配置文件 + 换 project_id，Core（RAG / Tools / 未来的
  Text-to-SQL）零修改；
- 代码中没有任何 `if table == "inventory"`、没有 `SEMANTICS = {...}`
  硬编码字典；
- 本阶段只配置当前知识库两张表的**最小验证语义**（2 表 / 4 列 /
  1 关系），大规模 WMS 业务语义留待真实业务表接入后补充。

## Validation

Semantic 必须与实际 DatabaseSchema 对齐（`SemanticSchemaValidator`）：

- 引用解析规则与 SchemaSerializer 一致（`schema.table` 精确 /
  裸表名当前 schema 内匹配）；
- 表 / 列 / 关系（source + target 表与列）全部校验；
- **多个失配一次全部返回**（`validate` 返回清单），便于一次性修正；
- **不自动修复**：配置引用 `material_code` 而库里是 `mat_code`
  → 报 `unknown_column`，等数据库演进时尽早暴露漂移。

## No Automatic Inference

当前版本禁止：

```text
Database Schema → AI 猜业务语义
Database Schema → LLM 生成业务语义
```

语义只能来自人工 YAML。Loader 侧同步禁止：不读 .env、不做
环境变量展开（配置值一律字面量，防止 secrets 注入）、
project_id 白名单防路径穿越、未知字段一律拒绝
（`password: xxx` 之类的字段直接报错）。

## Future

未来可以支持（均不在本阶段）：

```text
Question
    ↓ Relevant Table Selection（选表，可用 aliases 做匹配信号）
Schema + Semantic Context（Composer 已就绪）
    ↓
Text-to-SQL（前置：SQL Validator / 只读账号）
```

aliases 字段当前只是稳定输出，不做任何自动匹配——留作未来
选表层的输入信号。
