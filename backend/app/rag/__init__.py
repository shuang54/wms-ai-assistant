"""RAG 模块（Phase 3）。

已实现（Phase 3.3 + Phase 3.4）：
    parsers/  — Document Parser Layer（.md / .txt）
        ├── MarkdownParser
        ├── TextParser
        └── get_parser() Factory
    chunking/ — Text Chunking Engine
        ├── TextChunk (data class)
        └── MarkdownAwareChunker (Heading-aware + Size Limit)

本模块**未**实现（后续 Phase）：
    - Embedding / 向量生成
    - Vector Search / RAG Retrieval
    - 数据库写入（knowledge_chunk）
    - PDF / DOCX / Excel 等其他文档格式
    - Tool Calling / Agent / LangGraph / MCP
    - WMS / ERP API 集成

详见 docs/architecture.md §10–§12。
"""
