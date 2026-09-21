现在开始 **Phase 3.3：知识库文档解析**。

Phase 3.2 已完成并经过真实 PostgreSQL + pgvector 集成测试。

当前已经存在：

* PostgreSQL 16
* pgvector
* SQLAlchemy 2.x
* `knowledge_document`
* `knowledge_chunk`
* Document → Chunk ORM Relationship
* `embedding vector(1536)` 字段
* 完整数据库测试

现在开始下一小阶段。

# 一、本阶段唯一目标

实现：

```text
本地文档
   ↓
Document Parser
   ↓
纯文本内容
```

本阶段只支持：

```text
.md
.txt
```

暂时不要实现：

* PDF
* DOCX
* Excel
* Word
* OCR
* Embedding
* Chunking
* Vector Search
* RAG
* Tool Calling
* Agent
* LangGraph
* MCP
* WMS / ERP API

---

# 二、先阅读规范

必须先阅读：

* `AGENTS.md`
* `docs/requirements.md`
* `docs/architecture.md`

然后检查：

* `backend/app/db/models/knowledge_document.py`
* `backend/app/db/models/knowledge_chunk.py`
* `backend/app/db/session.py`
* `backend/app/config.py`
* `tests/test_knowledge_models.py`

不要破坏已有实现。

---

# 三、建立 Parser Layer

建议创建：

```text
backend/app/rag/
├── __init__.py
└── parsers/
    ├── __init__.py
    ├── base.py
    ├── markdown_parser.py
    ├── text_parser.py
    └── factory.py
```

具体目录可以根据现有项目结构调整，但职责必须清晰。

---

# 四、定义统一 Parser 接口

设计一个统一接口，例如：

```python
class DocumentParser:
    def parse(self, file_path: str) -> str:
        ...
```

要求：

* 输入文件路径
* 输出纯文本
* 不负责数据库
* 不负责 Chunk
* 不负责 Embedding
* 不负责 LLM

Parser 只做：

```text
文件 → 文本
```

---

# 五、TXT Parser

实现 TXT 文件解析。

要求：

* UTF-8 优先
* 能正确处理中文
* 文件为空时返回空字符串
* 文件不存在时抛出明确异常
* 不要静默吞掉异常

同时设计合理的异常类型，例如：

```text
DocumentParseError
UnsupportedDocumentTypeError
DocumentNotFoundError
```

如果项目已有异常体系，优先复用。

---

# 六、Markdown Parser

第一阶段 Markdown 不需要做复杂 AST 解析。

可以先读取 Markdown 原始文本。

例如：

```markdown
# WMS采购入库

## 1. 创建入库通知

进入采购入库模块...

## 2. PDA上架

扫描物料条码...
```

Parser 返回：

```text
# WMS采购入库

## 1. 创建入库通知

进入采购入库模块...

## 2. PDA上架

扫描物料条码...
```

保留 Markdown 标题结构。

不要在 Parser 阶段做：

* Markdown → HTML
* Markdown → Chunk
* Markdown → Embedding

这些属于后续阶段。

---

# 七、Parser Factory

实现根据文件类型选择 Parser：

```text
.md  → MarkdownParser
.txt → TextParser
```

例如：

```python
parser = get_parser("manual.md")
content = parser.parse(...)
```

不支持：

```text
.pdf
.docx
.xlsx
```

时必须返回明确的 UnsupportedDocumentTypeError。

---

# 八、不要写数据库

特别注意：

本阶段不要让 Parser 直接操作：

```text
knowledge_document
knowledge_chunk
```

保持：

```text
Parser
  ↓
纯文本
```

后面由 ingestion service 负责：

```text
Parser
  ↓
Text
  ↓
Chunker
  ↓
Embedding
  ↓
Database
```

这样后续容易测试和扩展。

---

# 九、测试

新增：

```text
tests/test_document_parsers.py
```

至少测试：

### TXT

1. 中文 TXT 正常读取
2. 英文 TXT 正常读取
3. 空 TXT
4. 不存在文件
5. UTF-8

### Markdown

1. Markdown 正常读取
2. 中文 Markdown
3. 标题结构保留
4. 空 Markdown

### Factory

测试：

```text
.md → MarkdownParser
.txt → TextParser
.pdf → UnsupportedDocumentTypeError
.docx → UnsupportedDocumentTypeError
```

测试不需要 PostgreSQL。

也不要调用 DeepSeek。

---

# 十、增加测试 Fixtures

如果合适，可以创建：

```text
tests/fixtures/documents/
├── sample.txt
├── sample.md
└── empty.txt
```

内容使用简单的 WMS 示例。

例如：

```text
WMS采购入库操作说明

采购订单审核完成后，
仓库人员创建采购入库通知。
```

Markdown：

```markdown
# WMS采购入库

## 创建入库通知

采购订单审核完成后创建入库通知。

## PDA上架

仓库人员使用PDA扫描物料条码。
```

---

# 十一、不要新增不必要依赖

本阶段 `.md` 和 `.txt` 都可以使用 Python 标准库。

优先：

```python
pathlib
```

和：

```python
open()
```

不要为了读取 Markdown 引入大型依赖。

---

# 十二、测试与回归

执行：

```bash
pytest -v
```

以及：

```bash
RUN_DB_TESTS=1 pytest -v
```

要求：

```text
Phase 1
+
Phase 2
+
Phase 3.1
+
Phase 3.2
+
Phase 3.3
```

全部通过。

不要运行真实 DeepSeek LLM。

不要消耗 API 额度。

---

# 十三、代码质量要求

Parser 必须满足：

```text
单一职责
可测试
可扩展
不依赖 FastAPI
不依赖数据库
不依赖 LLM
```

未来增加：

```text
PDFParser
DocxParser
ExcelParser
```

时，不应该修改已有 TXT / Markdown Parser 的核心逻辑。

---

# 十四、完成后停止

不要自行进入 Phase 3.4。

输出：

## Phase 3.3 Result

| 项目              | 状态       |
| --------------- | -------- |
| TXT Parser      | ✅/❌      |
| Markdown Parser | ✅/❌      |
| Parser Factory  | ✅/❌      |
| 异常处理            | ✅/❌      |
| Parser Tests    | X passed |
| Full Tests      | X passed |
| PostgreSQL      | 未修改      |
| Embedding       | 未实现      |
| DeepSeek        | 未调用      |

## 修改文件

列出新增和修改文件。

## 当前状态

明确回答：

> Phase 3.3 是否完成？

如果完成：

> 可以进入 Phase 3.4 Chunking。

否则：

> 停留在 Phase 3.3，并说明问题。

**不要自行进入 Phase 3.4。**
