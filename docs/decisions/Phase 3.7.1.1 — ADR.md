# Phase 3.7.1.1 — ADR：Project / Data Source Abstraction

日期：2026-09-23
状态：已实施（Phase 3.7.1.1）

## 1. 为什么需要 ProjectContext

Phase 3.7.1 之后，Schema Explorer、后续 Text-to-SQL、乃至未来的
数据问答能力，都隐含一个假设："操作的是**当前这一个**数据库"。
这个假设散落在各处的默认参数里（默认 schema、默认 Engine、
默认表前缀）。一旦将来要接入第二个项目 / 第二套库，就需要
把这些隐含假设收拢成一个显式的、可传递的上下文对象。

ProjectContext 就是这个收拢点：**只描述身份，不承载能力**。

## 2. 为什么 DataSource 与 DatabaseSettings 分离

- `DatabaseSettings`（DATABASE_URL）：**怎么连接**——含密码，
  属于 .env / 部署机密，绝不进代码层 DTO。
- `DataSource(name, type)`：**连的是什么**——只有逻辑名和类型标识，
  是安全的、可序列化的、可放进日志和 Prompt 的。

两层分离后，ProjectContext 可以被任何层安全传递（甚至直接给 LLM
做上下文），而不存在泄露凭据的面。安全测试锁定：DTO 字段集合、
repr 输出均不含 DATABASE_URL / 密码 / API Key。

## 3. 为什么 AI Core 不绑定 WMS

当前项目叫 Vietnam WMS，但 AI 能力层（RAG / Tool Calling /
Schema Explorer / 未来的 Text-to-SQL）与"仓储业务"没有必然关系。
如果 Core 出现 `project_type = "wms"` 这类依赖，未来做第二个项目
（比如 ERP 助手）就要fork Core。因此：

- Core 只认识 `ProjectContext` 这个中立 DTO；
- "vietnam-wms / Vietnam WMS" 只存在于**配置默认值**
  （PROJECT_ID / PROJECT_NAME，环境变量可覆盖）；
- 没有任何 `WMSProject` 类、没有 WMS 专属分支逻辑。

## 4. 为什么当前只实现 PostgreSQL

Simple First：当前唯一的真实数据源就是 PostgreSQL（pgvector 也
绑在它上面）。抽象已经通过 `DatabaseMetadataProvider` Protocol
留好了接缝：

```text
SchemaExplorerService（编排，不变）
        ↓ Protocol
PostgreSQLMetadataProvider（当前唯一实现）
```

实现 MySQL / SQL Server / API Provider 是"新增文件"级别的扩展，
但**现在没有真实需求**，提前实现只会带来不可测试的死代码
（违反 AGENTS.md §27）。DataSource.type 是开放字符串，
未来加 "mysql" 时 Core 零改动。

## 5. 未来换项目 / 换数据源时，哪些部分需要替换

| 场景 | 需要改的 | 不需要改的 |
|---|---|---|
| 换项目（另一套 WMS） | `.env` 的 PROJECT_*；知识库数据 | 全部代码 |
| 换数据源类型（MySQL 等） | 新增一个 `DatabaseMetadataProvider` 实现 | SchemaExplorerService、DTO、全部既有测试 |
| 多数据源并存 | `DataSource.name` 已支持（如 primary/erp） | 连接层（仍是各自 Settings + Engine） |

本次重构已验证：Phase 3.7.1 的 `tests/test_schema_explorer_service.py`
**零修改**全部通过（旧构造签名 `engine=` 自动包装为
PostgreSQLMetadataProvider）。

## 6. 当前阶段为什么不做多租户

多租户需要：用户→项目权限模型、项目 CRUD、动态建库/切库、
行级隔离——每一项都是独立的工程量，且当前**没有真实用户**，
做出来无法验证，只会把简单问题复杂化（AGENTS.md §23 DON'T）。
本阶段交付的是**代码层抽象**（两个 frozen DTO + 一个 Protocol），
多租户到来时在这些接缝上生长，而不是推倒重来。
