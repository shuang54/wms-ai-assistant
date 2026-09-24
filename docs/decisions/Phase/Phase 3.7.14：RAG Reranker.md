你现在只执行 **Phase 3.7.14：RAG Reranker 正式接入**。

## 一、目标

将项目中已经完成离线评测的 `bge-reranker-v2-m3` 正式接入 RAG 在线链路。

目标链路：

```text
用户问题
  ↓
RagService
  ↓
EmbeddingClient
  ↓
VectorSearchService
  ↓
候选知识片段 TopK
  ↓
Reranker
  ↓
重排序后的 TopK
  ↓
ContextBuilder
  ↓
LLMClient / DeepSeek
  ↓
最终回答
```

当前已有：

* `BGERerankerClient`
* `bge-reranker-v2-m3` 离线评测结果
* RAG
* Vector Search
* BGE-M3 Embedding
* DeepSeek LLM
* `/api/ai/chat`
* AI Router
* AI Orchestrator

本阶段的核心是：

> **复用现有 Reranker 实现，最小改动接入 RagService。**

---

# 二、严格限制

本阶段只做 RAG Reranker 接入。

**禁止：**

1. 不实现 Agent
2. 不引入 LangGraph
3. 不实现多轮规划
4. 不修改 AI Router 路由逻辑
5. 不修改 AI Orchestrator 核心执行逻辑
6. 不修改 Text-to-SQL
7. 不修改 SQL Validator
8. 不修改 SQL Executor
9. 不修改 Tool Framework
10. 不修改现有 `/api/chat`
11. 不增加新的 LLM Client
12. 不更换 Embedding 模型
13. 不更换 Reranker 模型
14. 不做前端开发
15. 不做数据库结构迁移
16. 不把 Reranker 逻辑复制到多个地方
17. 不删除现有测试
18. 不大规模重构现有 RAG

如果发现必须修改上述范围之外的代码：

**先停止，向我报告原因，不要自行继续。**

---

# 三、第一步：先阅读现有实现

先不要写代码。

重点检查：

```text
backend/app/services/rag_service.py
backend/app/services/vector_search_service.py
backend/app/services/
backend/app/clients/
backend/app/config.py
tests/
docs/decisions/
```

重点找到：

1. 当前 `BGERerankerClient` 的实现位置
2. 它当前的调用接口
3. 输入格式
4. 输出格式
5. 模型加载方式
6. 是否 CPU/GPU
7. 是否已经有配置项
8. 当前 `RagService.answer()` 的完整流程
9. Vector Search 返回的数据结构
10. ContextBuilder 接收的数据结构
11. 当前 RAG 测试如何 Mock Vector Search / LLM

不要重新实现 `BGERerankerClient`。

---

# 四、设计要求

## 4.1 增加 Reranker 抽象

如果当前项目没有合适的 Protocol，则新增一个最小抽象。

建议：

```python
class Reranker(Protocol):
    def rerank(
        self,
        query: str,
        documents: Sequence[str],
        *,
        top_k: int | None = None,
    ) -> Sequence[...]:
        ...
```

具体返回 DTO 根据现有代码实际情况设计。

要求：

* Protocol 与具体模型实现解耦
* RagService 不直接依赖 `BGERerankerClient`
* 方便未来替换其他 Reranker
* 不改变现有 `BGERerankerClient` 的公共行为

如果已有合适 Protocol，则复用，不要重复创建。

---

# 五、RAG 接入位置

Reranker 必须位于：

```text
Vector Search
      ↓
Reranker
      ↓
Context Builder
```

而不是：

```text
Embedding
 ↓
Reranker
 ↓
Vector Search
```

也不是：

```text
Vector Search
 ↓
Context Builder
 ↓
Reranker
```

推荐：

```text
Vector Search TopK = candidate_k
        ↓
     Reranker
        ↓
   final_top_k
        ↓
 ContextBuilder
```

例如：

```text
Vector Search:
top_k = 10

Reranker:
rerank 10 个候选

最终：
top_k = 3
```

具体默认值根据现有 RAG 配置和代码决定，不要凭空改变原有行为。

---

# 六、增加配置开关

增加最小配置：

```text
RERANKER_ENABLED=false
```

默认必须：

```text
false
```

原因：

* 保证现有 RAG 默认行为不发生变化
* 方便灰度验证
* 测试可以分别验证开启/关闭两种路径

同时增加必要的：

```text
RERANKER_CANDIDATE_TOP_K
RERANKER_TOP_K
```

如果现有配置已经存在对应参数，则复用。

配置必须：

* 有默认值
* 有合理范围
* 不允许异常值导致无限资源消耗

---

# 七、RagService 行为

当：

```text
RERANKER_ENABLED=false
```

保持原有：

```text
Vector Search
→ ContextBuilder
→ LLM
```

当：

```text
RERANKER_ENABLED=true
```

变成：

```text
Vector Search
→ Reranker
→ ContextBuilder
→ LLM
```

要求：

* Reranker 失败时必须有明确错误处理
* 不允许静默吞掉异常
* 不允许返回未排序的错误结果
* Reranker 不执行数据库操作
* Reranker 不调用 LLM
* Reranker 不修改知识库数据

---

# 八、默认模型

继续使用现有：

```text
bge-reranker-v2-m3
```

不要下载或切换其他模型。

如果当前实现已经通过配置指定模型，则复用。

---

# 九、测试

至少增加以下测试。

## 9.1 Reranker 单元测试

验证：

* 正常排序
* top_k
* 空 documents
* 单 document
* query 为空
* 输入类型错误
* Reranker 异常

---

## 9.2 RagService 集成测试

验证关闭：

```text
RERANKER_ENABLED=false
```

时：

```text
VectorSearch = 1
Reranker = 0
ContextBuilder = 1
LLM = 1
```

验证开启：

```text
RERANKER_ENABLED=true
```

时：

```text
VectorSearch = 1
Reranker = 1
ContextBuilder = 1
LLM = 1
```

---

## 9.3 顺序测试

必须明确验证：

```text
Vector Search
→ Reranker
→ ContextBuilder
→ LLM
```

不能出现：

```text
ContextBuilder
→ Reranker
```

---

## 9.4 Reranker 排序效果测试

构造至少 3 个候选：

```text
document A
document B
document C
```

让 Fake Reranker 改变顺序。

验证最终传给 ContextBuilder 的顺序已经发生变化。

---

## 9.5 API E2E

使用现有：

```text
POST /api/ai/chat
```

验证 RAG 路由仍然正常。

如果环境允许：

```text
RUN_REAL_RAG_E2E=1
RUN_DB_TESTS=1
```

执行真实 RAG E2E。

验证：

* API 200
* route = rag
* 返回真实回答
* Reranker 被调用
* Vector Search 正常
* DeepSeek 正常
* 数据库无污染

---

# 十、性能

由于当前：

```text
bge-reranker-v2-m3
```

之前已经做过性能 Benchmark，本阶段不要重新设计模型推理框架。

只记录：

```text
Vector Search耗时
Reranker耗时
总RAG耗时
```

如果现有代码已经有耗时字段，则复用。

不要为了性能引入：

* Redis
* Celery
* 多进程
* GPU 服务
* 微服务
* 异步推理框架

---

# 十一、兼容性

必须保证：

```text
RERANKER_ENABLED=false
```

时现有全部测试保持通过。

运行：

```bash
pytest -q
```

然后如果环境支持：

```bash
RUN_DB_TESTS=1 pytest -q
```

最后运行：

```bash
python -m compileall backend
```

并检查 LSP / lint。

---

# 十二、文档

新增：

```text
docs/decisions/Phase/Phase 3.7.14：RAG Reranker正式接入.md
```

记录：

1. 背景
2. 当前 RAG 链路
3. 为什么 Reranker 放在 Vector Search 后
4. Protocol 设计
5. 配置项
6. 开启/关闭行为
7. 测试结果
8. 性能结果
9. 已知限制
10. 后续方向

不要写 Agent / LangGraph 的实现计划。

---

# 十三、最终验收标准

必须满足：

```text
pytest -q
```

全部通过。

如果 DB 测试可用：

```text
RUN_DB_TESTS=1 pytest -q
```

全部通过。

同时：

```text
python -m compileall backend
```

通过。

并确认：

```text
数据库没有被测试污染
```

---

# 十四、完成后必须停止

这是最重要的要求：

**Phase 3.7.14 完成后立即停止。**

不要自行开始：

* Phase 3.7.15
* Agent
* LangGraph
* Memory
* Multi-Agent
* MCP
* 权限系统
* Streaming
* 前端

完成后只向我报告：

```text
1. 修改了哪些文件
2. 新增了哪些文件
3. Reranker 如何接入
4. 新增了哪些配置
5. 测试结果
6. DB 测试结果
7. 真实 RAG E2E 是否执行
8. Reranker 是否真的参与在线 RAG
9. 性能结果
10. 遇到的问题及解决方式
11. 当前已知限制
```

**报告完成后停止，不得继续下一阶段。**
