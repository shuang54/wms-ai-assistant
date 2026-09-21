# Phase 3.5.1：Embedding Client

请严格按照“小步开发”原则执行。

## 一、先阅读项目文档

开发前必须阅读：

1. `AGENTS.md`
2. `docs/requirements.md`
3. `docs/architecture.md`

同时检查：

* `backend/app/llm/`
* `backend/app/config.py`
* `backend/app/rag/chunking/`
* `backend/app/db/models/knowledge_chunk.py`

理解当前 LLM Client、配置管理和 TextChunk 数据结构后再开发。

---

# 二、本阶段唯一目标

实现独立的 Embedding Client：

```text
文本
 ↓
EmbeddingClient
 ↓
Embedding API
 ↓
list[float]
```

本阶段只负责：

> 调用 Embedding API，并返回向量。

---

# 三、严格禁止

本阶段不要实现：

* ❌ PostgreSQL 写入
* ❌ `knowledge_chunk.embedding` 写入
* ❌ pgvector 查询
* ❌ Vector Search
* ❌ RAG
* ❌ Document Service
* ❌ Chunk 持久化
* ❌ FastAPI Endpoint
* ❌ Tool Calling
* ❌ Agent
* ❌ LangGraph
* ❌ MCP
* ❌ WMS / ERP API
* ❌ 批量知识库导入
* ❌ 自动扫描 knowledge/ 目录
* ❌ 修改 Phase 3.2 ORM
* ❌ 修改 Phase 3.3 Parser
* ❌ 修改 Phase 3.4 Chunking

尤其注意：

**本阶段不得执行任何数据库 INSERT / UPDATE。**

---

# 四、Embedding Provider

当前项目已经使用：

```text
LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-chat
LLM_BASE_URL=https://api.deepseek.com
```

但不要假设：

```text
DeepSeek 的 Chat Model
=
Embedding Model
```

需要先确认当前 DeepSeek API 是否提供可用的 Embedding API，以及当前 API 文档中的：

* Endpoint
* Model 名称
* Request 格式
* Response 格式
* 向量维度

如果项目当前没有确定的 Embedding 模型：

**不要猜模型名称。**

请把 Embedding 模型设计成配置项。

例如：

```env
EMBEDDING_PROVIDER=deepseek
EMBEDDING_MODEL=...
EMBEDDING_BASE_URL=...
EMBEDDING_API_KEY=...
EMBEDDING_DIMENSION=1536
EMBEDDING_TIMEOUT=60
```

其中 `EMBEDDING_DIMENSION` 必须作为配置存在。

如果当前选定模型实际维度不是 1536：

**本阶段只报告这个冲突，不要修改 Phase 3.2 的数据库字段。**

---

# 五、推荐目录

在现有项目结构基础上增加：

```text
backend/app/embedding/
├── __init__.py
├── client.py
└── exceptions.py
```

如果项目已有类似基础抽象，可以合理复用，但不要破坏现有：

```text
backend/app/llm/
```

---

# 六、Embedding Client 抽象

设计统一接口：

```python
class EmbeddingClient(ABC):

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        ...
```

同时可以提供：

```python
class BatchEmbeddingClient(EmbeddingClient):

    @abstractmethod
    def embed_many(self, texts: list[str]) -> list[list[float]]:
        ...
```

但不要过度设计。

如果当前只需要单文本接口，可以先实现：

```python
embed(text)
```

批量接口可以后续 Phase 3.5.2 再做。

---

# 七、DeepSeek Embedding Client

如果确认 DeepSeek 当前提供 Embedding API，则实现：

```text
DeepSeekEmbeddingClient
```

要求：

```text
EmbeddingClient
      ↑
DeepSeekEmbeddingClient
```

不要把 DeepSeek 请求逻辑直接写在业务代码中。

---

# 八、请求逻辑

Client 至少应该处理：

```text
text
 ↓
POST Embedding API
 ↓
解析 response
 ↓
vector
```

要求：

1. API Key 从环境变量读取
2. 不允许代码硬编码 API Key
3. Base URL 从配置读取
4. Model 从配置读取
5. Timeout 从配置读取
6. HTTP 错误统一转换为项目自己的异常
7. API 返回格式异常时抛出明确异常
8. 向量为空时抛出明确异常
9. 不打印 API Key
10. 不把完整 API Key 写入日志

---

# 九、异常体系

建立清晰的异常，例如：

```python
EmbeddingError
EmbeddingConfigurationError
EmbeddingAPIError
EmbeddingResponseError
EmbeddingDimensionError
```

继承关系建议：

```text
EmbeddingError
├── EmbeddingConfigurationError
├── EmbeddingAPIError
├── EmbeddingResponseError
└── EmbeddingDimensionError
```

调用方以后可以统一：

```python
except EmbeddingError:
    ...
```

---

# 十、输入校验

至少处理：

### 空字符串

```python
embed("")
```

应该明确拒绝。

例如：

```text
EmbeddingConfigurationError
```

或者更合适的输入异常。

不要把空字符串发送给真实 API。

### 空白字符串

```python
embed("   ")
```

同样拒绝。

### 正常中文

例如：

```text
采购入库需要先确认采购订单。
```

应该可以正常调用。

### 超长文本

本阶段不要自己偷偷截断。

如果 API 有输入长度限制：

应该明确抛出 API / 输入相关异常。

不要：

```python
text = text[:800]
```

因为 Chunking 已经负责文本切分。

---

# 十一、向量维度验证

这是本阶段非常重要的一点。

Embedding Client 拿到：

```python
vector: list[float]
```

后验证：

```text
len(vector) == EMBEDDING_DIMENSION
```

如果不一致：

抛：

```text
EmbeddingDimensionError
```

例如配置：

```env
EMBEDDING_DIMENSION=1536
```

实际模型返回：

```text
1024
```

应该直接失败。

**不要自动补 0。**

**不要自动截断。**

**不要修改数据库维度。**

---

# 十二、不要使用真实 API 做普通单元测试

测试必须 Mock HTTP 请求。

不要因为执行：

```bash
pytest
```

就产生 DeepSeek API 费用。

测试应该覆盖：

```text
正常 response
API 401
API 429
API 500
timeout
网络异常
response JSON 错误
缺少 embedding
embedding 为空
维度不正确
空文本
```

---

# 十三、Real Smoke Test

可以单独增加：

```text
tests/test_embedding_real.py
```

但必须默认 Skip。

例如：

```text
RUN_REAL_EMBEDDING_TESTS=1
```

没有环境变量：

```text
skip
```

不能默认调用真实 API。

真实测试只测试：

```text
一句中文
 ↓
Embedding API
 ↓
向量
 ↓
检查维度
```

不要写数据库。

---

# 十四、配置

扩展现有配置体系。

建议：

```env
# Embedding
EMBEDDING_PROVIDER=deepseek
EMBEDDING_MODEL=
EMBEDDING_BASE_URL=
EMBEDDING_API_KEY=
EMBEDDING_DIMENSION=1536
EMBEDDING_TIMEOUT=60
```

注意：

如果 Embedding API 实际使用和 Chat API 相同的 Key，可以支持：

```env
EMBEDDING_API_KEY=
```

为空时 fallback 到：

```env
LLM_API_KEY
```

但：

**不要把 API Key 打印出来。**

`.env.example` 可以包含：

```env
EMBEDDING_API_KEY=
```

但绝对不能写真实 Key。

---

# 十五、关于 1536 维

当前 Phase 3.2 的数据库字段是：

```text
vector(1536)
```

因此本阶段必须明确回答：

```text
当前选定 Embedding Model
        ↓
实际输出维度是多少？
        ↓
是否等于 1536？
```

如果不等于：

```text
不要修改数据库
不要进入 3.5.2
```

而是在最终报告中明确：

```text
Embedding model dimension = XXX
Current DB dimension = 1536
Status = mismatch
```

然后停止。

如果恰好：

```text
Embedding dimension = 1536
```

则报告：

```text
Dimension compatible with Phase 3.2 schema.
```

---

# 十六、测试文件

新增：

```text
tests/test_embedding_client.py
```

建议至少覆盖：

### 配置

1. 正常配置
2. 缺少 API Key
3. 缺少 Model
4. 缺少 Base URL
5. 非法 Dimension

### 输入

6. 空字符串
7. 空白字符串
8. 正常中文

### API

9. HTTP 200 正常 response
10. HTTP 401
11. HTTP 429
12. HTTP 500
13. timeout
14. 网络异常

### Response

15. 缺少 data
16. 缺少 embedding
17. embedding 类型错误
18. embedding 为空
19. dimension mismatch

### 安全

20. 异常信息不能包含完整 API Key

### Real Smoke

21. 默认 skip
22. `RUN_REAL_EMBEDDING_TESTS=1` 才允许真实调用

测试数量不要求严格等于某个数字，但必须覆盖以上核心场景。

---

# 十七、依赖

优先复用 Phase 2 已经使用的 HTTP Client。

检查当前 `requirements.txt`。

如果已经存在：

```text
httpx
```

优先复用。

不要重复增加 HTTP 库。

除非确实必要，否则：

**本阶段不增加新的第三方依赖。**

---

# 十八、运行验证

先运行：

```bash
pytest tests/test_embedding_client.py -v
```

然后：

```bash
pytest
```

如果数据库环境正常：

```bash
$env:RUN_DB_TESTS="1"
pytest -v
```

确认：

```text
Phase 1
Phase 2
Phase 3.1
Phase 3.2
Phase 3.3
Phase 3.4
Phase 3.5.1
```

全部没有回归。

---

# 十九、真实 API 测试

如果已经确认 Embedding API 和模型：

可以单独运行：

```text
RUN_REAL_EMBEDDING_TESTS=1
```

只允许：

```text
中文测试文本
 ↓
Embedding API
 ↓
检查向量
```

禁止：

```text
写数据库
```

也不要进行大量测试。

**最多验证 1～2 个文本即可。**

---

# 二十、最终报告

完成后停止，不要进入 Phase 3.5.2。

报告：

1. 修改文件
2. Embedding Client 接口
3. Provider
4. Model
5. API Endpoint
6. 向量维度
7. 是否与 `vector(1536)` 兼容
8. 异常体系
9. Mock 测试数量
10. Full pytest 结果
11. Real Embedding Smoke Test 是否执行
12. 是否产生真实 API 调用
13. 是否修改数据库
14. 是否新增依赖
15. 当前 Phase 3.5.1 是否完成

完成后立即停止，等待下一步指令。
