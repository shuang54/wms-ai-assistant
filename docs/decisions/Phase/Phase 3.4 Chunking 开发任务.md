# Phase 3.4：Chunking Engine

请严格按照项目现有文档和“小步开发”原则执行。

## 一、先阅读

开始开发前必须阅读：

1. `AGENTS.md`
2. `docs/requirements.md`
3. `docs/architecture.md`

同时检查当前 Phase 3.3 的 Parser 实现：

* `backend/app/rag/parsers/`
* `tests/test_document_parsers.py`

确认 Parser 当前接口和异常体系后再开发。

---

# 二、本阶段唯一目标

实现：

```text
Parser 输出的纯文本
        ↓
     Chunking
        ↓
多个 Chunk
```

本阶段只负责：

```text
str → list[Chunk]
```

**不要在本阶段写入 PostgreSQL。**

---

# 三、明确禁止事项

本阶段禁止实现：

* ❌ Embedding API
* ❌ DeepSeek API
* ❌ 向量生成
* ❌ pgvector 查询
* ❌ RAG
* ❌ LLM
* ❌ Tool Calling
* ❌ Agent
* ❌ LangGraph
* ❌ MCP
* ❌ WMS API
* ❌ ERP API
* ❌ FastAPI 接口
* ❌ 数据库写入
* ❌ 修改 Phase 3.2 数据模型

尤其不要因为已经存在 `knowledge_chunk` 表，就直接把 Chunk 写进数据库。

本阶段先把 Chunking 算法独立做好。

---

# 四、Chunking 设计

创建：

```text
backend/app/rag/chunking/
├── __init__.py
├── models.py
└── text_chunker.py
```

可以根据现有项目结构做合理调整，但必须保持职责清晰。

---

## 1. Chunk 数据结构

定义统一的 Chunk 对象，例如：

```python
class TextChunk:
    content: str
    chunk_index: int
    metadata: dict
```

至少包含：

```text
content
chunk_index
metadata
```

其中：

```text
chunk_index
```

从 `0` 开始连续递增。

例如：

```text
0
1
2
3
...
```

---

# 五、第一版 Chunking 策略

不要一开始设计复杂的递归语义切分。

先实现一个：

## Heading-aware + Size Limit

核心原则：

### 1. Markdown 标题优先作为逻辑边界

例如：

```markdown
# 采购入库

采购入库用于处理采购订单到货。

## 操作步骤

1. 打开采购入库
2. 扫描收货通知
3. 扫描物料

## 注意事项

入库前必须确认仓库和库位。

## 异常处理

如果扫描失败，需要重新扫描。
```

尽量形成：

```text
Chunk 0
# 采购入库

采购入库用于处理采购订单到货。
```

```text
Chunk 1
# 采购入库
## 操作步骤

1. 打开采购入库
2. 扫描收货通知
3. 扫描物料
```

```text
Chunk 2
# 采购入库
## 注意事项

入库前必须确认仓库和库位。
```

```text
Chunk 3
# 采购入库
## 异常处理

如果扫描失败，需要重新扫描。
```

重点：

**子 Chunk 尽可能保留父级标题上下文。**

---

# 六、Size Limit

增加可配置参数：

```text
chunk_size
chunk_overlap
```

建议默认：

```text
chunk_size = 800
chunk_overlap = 100
```

这里的单位先使用：

```text
字符数
```

不要引入 tokenizer。

不要为了 token 计算增加新的第三方依赖。

---

# 七、超长内容处理

如果某个标题下面内容超过：

```text
chunk_size
```

需要进一步切分。

优先级：

```text
段落
↓
句子
↓
字符
```

不要简单粗暴地：

```python
text[i:i+800]
```

优先保证语义完整。

例如：

```text
第一段……

第二段……

第三段……
```

应该尽量：

```text
Chunk 1
第一段……

第二段……
```

而不是：

```text
Chunk 1
第一段……第二段……第三段的前半截
```

---

# 八、Overlap

当一个逻辑章节因为过长必须拆分时：

```text
chunk_overlap = 100
```

用于保留上一 Chunk 尾部的一部分内容。

例如：

```text
Chunk 0
AAAAAAAAAAAAAAAA
BBBBBBBBBBBBBBBB
CCCCCCCCCCCCCCCC
```

下一 Chunk 可以保留：

```text
CCCCCCCCCCCCCCCC
DDDDDDDDDDDDDDDD
EEEEEEEEEEEEEEEE
```

但是：

**不要为了 overlap 破坏标题结构。**

---

# 九、Metadata

每个 Chunk 至少保留：

```python
{
    "source_type": "markdown",
    "heading_path": [...]
}
```

例如：

```json
{
  "source_type": "markdown",
  "heading_path": [
    "采购入库",
    "操作步骤"
  ]
}
```

如果输入是普通 TXT：

```json
{
  "source_type": "text",
  "heading_path": []
}
```

不要在本阶段设计过多 metadata。

后续 RAG 再扩展。

---

# 十、空文本

输入：

```text
""
```

或者只有空白：

```text
"   \n\n "
```

应该返回：

```python
[]
```

而不是生成一个空 Chunk。

---

# 十一、接口设计

建议提供统一接口，例如：

```python
class TextChunker(ABC):

    @abstractmethod
    def chunk(self, text: str) -> list[TextChunk]:
        ...
```

然后实现：

```python
MarkdownAwareChunker
```

如果你认为当前接口不需要 ABC，也可以保持简单，但必须保证未来可以扩展不同 Chunking 策略。

---

# 十二、测试

新增：

```text
tests/test_chunking.py
```

至少覆盖以下场景：

### 基础

1. 普通文本可以切 Chunk
2. 空文本返回 []
3. 纯空白返回 []
4. chunk_index 从 0 开始
5. chunk_index 连续

### Markdown

6. 一级标题识别
7. 二级标题识别
8. 多级标题识别
9. 子 Chunk 保留父级标题
10. heading_path 正确

### Size

11. 短文本不应该被无意义切分
12. 超过 chunk_size 会切分
13. chunk_size 可配置
14. chunk_overlap 可配置

### 语义边界

15. 优先按段落切分
16. 段落过长时按句子切分
17. 单句仍然超过限制时再按字符切分

### 异常

18. 非法 chunk_size 应该报清晰异常
19. 非法 chunk_overlap 应该报清晰异常
20. `chunk_overlap >= chunk_size` 应该拒绝

### 回归

运行：

```bash
pytest
```

如果 PostgreSQL 环境可用，再运行：

```bash
$env:RUN_DB_TESTS="1"
pytest -v
```

确保 Phase 1～3.3 全部没有被破坏。

---

# 十三、测试数据

增加几个专门用于 Chunking 的 fixture，例如：

```text
tests/fixtures/chunking/
├── short.md
├── hierarchy.md
└── long.md
```

其中 `hierarchy.md` 必须包含类似：

```markdown
# 采购入库

## 操作步骤

### 扫描收货通知

扫描采购收货通知单。

### 扫描物料

扫描实际到货物料。

## 异常处理

处理扫描失败问题。
```

用于验证：

```text
heading_path
```

是否正确。

---

# 十四、代码质量要求

遵守：

```text
Simple First
Single Responsibility
No unnecessary dependencies
No premature abstraction
```

不要为了这一步引入 LangChain。

不要为了这一步引入 tokenizer。

不要为了这一步引入新的第三方库。

尽量使用 Python 标准库。

---

# 十五、完成后的验证

必须实际运行：

```bash
pytest tests/test_chunking.py -v
```

然后：

```bash
pytest
```

如果数据库测试环境可用：

```bash
$env:RUN_DB_TESTS="1"
pytest -v
```

确认：

```text
Parser
Database
LLM
Chat
Health
Chunking
```

全部没有回归。

---

# 十六、最终报告

完成后只报告：

1. 修改了哪些文件
2. Chunking 的实际算法
3. Chunk 数据结构
4. heading_path 如何处理
5. chunk_size / overlap 默认值
6. 测试数量
7. pytest 结果
8. 是否修改数据库
9. 是否调用 DeepSeek
10. 是否新增依赖
11. 当前 Phase 3.4 是否完成

**不要自行进入 Phase 3.5。**

完成 Phase 3.4 后立即停止，等待下一步指令。
