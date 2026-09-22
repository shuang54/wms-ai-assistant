# Phase 3.5.1.5：Embedding Model Smoke Test

本阶段只做 Embedding 模型选型和真实 API 验证。

## 一、重要原则

当前项目：

```text
knowledge_chunk.embedding
    ↓
vector(1536)
```

因此必须真实验证 Embedding 模型输出维度。

**不要修改数据库。**

**不要修改 `knowledge_chunk.py`。**

**不要进入 Phase 3.5.2。**

---

## 二、优先验证模型

优先尝试当前 Embedding Client 已支持的 OpenAI-compatible API。

如果使用 SiliconFlow，优先验证：

```text
BAAI/bge-m3
```

如果该模型当前不可用，再验证：

```text
Qwen/Qwen3-Embedding-8B
```

不要凭记忆假设模型维度。

最终维度必须以真实 API Response 为准。

SiliconFlow 官方资料显示其平台提供 Embedding 模型，并支持 OpenAI 兼容 API。不要自行猜测模型 ID 或向量维度。

---

## 三、配置

不要把 API Key 写入代码。

使用环境变量：

```env
EMBEDDING_PROVIDER=siliconflow
EMBEDDING_MODEL=实际可用模型名称
EMBEDDING_BASE_URL=https://api.siliconflow.cn/v1
EMBEDDING_API_KEY=你的Key
EMBEDDING_DIMENSION=1536
EMBEDDING_TIMEOUT=60
```

如果项目已经存在 `.env`：

直接修改本地 `.env`。

不要修改 `.env.example` 写入真实 Key。

---

## 四、真实测试文本

只发送 1～2 条文本。

第一条：

```text
采购入库时，需要先确认采购订单和收货通知，然后扫描物料条码并完成库位上架。
```

第二条：

```text
WMS 当前越南仓原材料库存数量是多少？
```

第二条仅用于验证中文企业/WMS文本能够正常生成向量。

---

## 五、验证内容

调用：

```text
EmbeddingClient
    ↓
Embedding API
    ↓
vector
```

打印/报告以下信息：

```text
Provider
Model
Base URL（不要打印 API Key）
HTTP Status
Vector Dimension
```

例如：

```text
Provider = siliconflow
Model = BAAI/bge-m3
Status = 200
Dimension = XXXX
```

不要输出完整向量。

最多只允许显示：

```text
vector[:3]
```

用于确认返回确实是数值向量。

---

## 六、最关键的兼容性判断

比较：

```text
实际 Embedding Dimension
        VS
当前 PostgreSQL vector(1536)
```

### 情况 A

如果：

```text
实际 = 1536
```

报告：

```text
Embedding Model Compatible = YES
```

可以为下一阶段做准备。

### 情况 B

如果：

```text
实际 != 1536
```

报告：

```text
Embedding Model Compatible = NO
```

**不要修改数据库。**

**不要迁移表。**

**不要进入 Phase 3.5.2。**

我们下一步再决定：

```text
换模型
```

还是：

```text
调整 vector dimension
```

---

## 七、不要做的事情

本阶段禁止：

* ❌ 写 `knowledge_chunk`
* ❌ INSERT
* ❌ UPDATE
* ❌ ALTER TABLE
* ❌ 修改 pgvector dimension
* ❌ 批量 Embedding
* ❌ 导入 knowledge/
* ❌ RAG
* ❌ Vector Search
* ❌ 修改 Chunking
* ❌ 修改 Parser
* ❌ 调用 DeepSeek Chat
* ❌ 运行大量 API 请求

只做 1～2 次 Embedding API 调用。

---

## 八、最终报告

完成后只报告：

1. 使用的 Provider
2. 使用的 Model
3. API Endpoint
4. HTTP Status
5. 实际 Vector Dimension
6. 是否等于 1536
7. 中文 WMS 文本是否成功
8. 是否修改数据库
9. 是否产生数据库写入
10. 是否调用 DeepSeek
11. 是否产生其他 API 调用
12. 当前推荐模型
13. 是否可以进入 Phase 3.5.2

完成后立即停止。

**不要自行进入 Phase 3.5.2。**
