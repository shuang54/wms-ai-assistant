"""WMS AI Assistant — 开发用 CLI 入口（Phase 3.5.8）。

本目录只承载开发期离线命令（知识库导入等）。

设计原则（AGENTS.md §4 / §13）：

- CLI 只负责参数解析 + 调用现有 Service，**不**直接操作 ORM / Parser / EmbeddingClient；
- 业务逻辑收敛在 Service 层；
- 不在 CLI 中塞业务规则。
"""