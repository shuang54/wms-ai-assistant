# Phase 3.7.1 — ADR：Database Schema Explorer

日期：2026-09-23
状态：已实施（Phase 3.7.1）

## 1. 为什么需要 Schema Explorer

后续阶段要让 AI 回答"库里有哪些表、字段什么含义、表之间什么关系"这类
数据问题，并为 Text-to-SQL 做准备。第一步必须先有一个**可靠的、结构化的**
数据库元数据读取能力，否则：

- AI 只能靠猜（幻觉风险）；
- 每次临时手写 `information_schema` 查询不可复用、不可测试；
- 后续 Text-to-SQL 的 Prompt 无法注入稳定的 Schema 上下文。

Schema Explorer 的产物是稳定 DTO（`DatabaseSchema → SchemaTable →
SchemaColumn / SchemaForeignKey`），全部为 frozen dataclass，不暴露
ORM / Row 对象，适合直接序列化进 Prompt。

实现：`backend/app/services/schema_explorer_service.py`，
只读查询 `pg_catalog`（表 / 列 / 主键 / 外键 / COMMENT），
复用现有 `db.session.get_engine()`，不新建连接体系。

默认 schema 策略：`schema=None` → `public`（PostgreSQL 默认 schema，
非业务命名；当前库唯一用户 schema 即 public，已实测确认）。
系统 schema（`pg_catalog` / `information_schema` / `pg_*`）被显式拒绝，
系统表绝不暴露给 AI。

## 2. 为什么暂时不做 Text-to-SQL

1. **依赖未就绪**：Text-to-SQL 需要先把 Schema 转成 AI 可消费的上下文，
   本阶段刚拿到结构化 Schema，还没做"Schema → Prompt"这一步。
2. **风险分级**：SQL 生成意味着 AI 输出可能被解释执行，必须有
   SQL 校验 / 白名单 / 只读账号 / 权限控制兜底，这些是独立的工程量。
3. **Simple First**：先让"读结构"这一步 100% 可靠（正确、只读、防注入），
   再往上叠生成能力，出问题时可分层归因。

因此本阶段明确禁止：`execute_sql` / SQLGenerator / NL2SQL / SQL Validator。

## 3. Schema Explorer 的职责边界

**只做**：

- `inspect(schema=None | "public" | 自定义)` → `DatabaseSchema`
- 读取：表名、表 COMMENT、列名、类型（`format_type` 精确格式）、
  nullable、default、ordinal_position、主键标记、外键关系、列 COMMENT
- 无 COMMENT 的列：`description = None`（**不让 AI 猜字段含义**）

**不做**：

- 不调用 LLM / Embedding / RAG（AI 理解 Schema 是下一阶段）
- 不执行任何 DDL / DML（SQL 常量为 SELECT-only，有静态测试锁定）
- 不做 HTTP API、不修改 Tool / ToolRegistry / Chat / RAG

## 4. 安全设计

- schema 参数：白名单正则校验（合法 PostgreSQL 标识符）+ 系统 schema 拒绝，
  **先于任何 DB 访问**；注入串（`public"; DROP TABLE ...`）在参数校验层
  被拒绝，有集成测试验证业务表完好无损。
- SQL：四条模块常量，全部 SELECT，schema 只走 bind parameter，
  无任何字符串拼接（有测试静态锁定）。
- 错误：SQLAlchemyError 包装为 `SchemaExplorerError`，只含异常类名，
  不透传可能携带 DATABASE_URL / 密码的原始消息（有测试断言）。
- 日志：只记 schema / table_count / column_count / foreign_key_count /
  elapsed_ms。

## 5. 后续 Text-to-SQL 如何使用它

```text
SchemaExplorerService.inspect()
        ↓
DatabaseSchema DTO
        ↓  （下一阶段）Schema → Prompt 序列化（裁剪 / 压缩 / 加注释）
LLM 生成候选 SQL
        ↓
SQL Validator（只读白名单、AST 校验）
        ↓
受控执行（只读账号 / 行数上限 / 审计）
```

关键点：Text-to-SQL 只消费本 Service 的 DTO，不自己查 catalog，
也不直接持有 Engine——生成与执行必须经过独立校验层。

## 6. 为什么 AI 不应该直接拥有数据库连接

- AGENTS.md §4.1 / §15.2：AI 不允许直接操作企业数据库；
  一切数据访问必须经过受控层（Tool / Service / 权限校验）。
- 直接给 AI 连接 = 绕过最小权限原则，无法审计、无法限流、
  无法阻止写操作与误删。
- Schema Explorer 本身是"受控只读"的实现示范：
  AI 后续拿到的只是它的**DTO 快照**，而不是连接。
