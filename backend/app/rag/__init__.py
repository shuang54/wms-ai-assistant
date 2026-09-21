"""RAG 模块（Phase 3）。

本阶段（Phase 3.3）已实现：
    Document Parser Layer — 仅 .md / .txt
        ├── MarkdownParser  (保留原始结构)
        ├── TextParser      (UTF-8)
        └── get_parser() Factory

本阶段**未**实现（后续 Phase）：
    - Chunking / Embedding / Vector Search / RAG
    - PDF / DOCX / Excel 等其他文档格式
    - Tool Calling / Agent / LangGraph / MCP
    - WMS / ERP API 集成

详见 docs/architecture.md §10–§12。
"""
