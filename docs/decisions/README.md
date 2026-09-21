# WMS AI Assistant — Architecture Decision Records (ADR)

本目录用于记录项目中重要的技术决策。
每条决策独立成文，文件名格式：

```text
NNNN-short-kebab-title.md
```

例如：

```text
0001-llm-provider-abstraction.md
0002-rag-retrieval-strategy.md
0003-tool-permission-model.md
```

---

## 模板

复制以下内容到新文件，按需修改后提交。

```markdown
# ADR-NNNN: <简短标题>

- 状态：Proposed / Accepted / Deprecated / Superseded by ADR-XXXX
- 日期：YYYY-MM-DD
- 决策者：<姓名/角色>

## 背景 (Context)

当前面临什么问题？有什么约束？

## 决策 (Decision)

我们决定怎么做？

## 后果 (Consequences)

正面：
- ...

负面 / 风险：
- ...

后续影响：
- ...
```

---

## 当前 ADR 列表

暂无。Phase 1 阶段无需新增 ADR，所有关键决策已在 `docs/architecture.md` §39 给出。

后续阶段（如引入具体 LLM Provider、向量库、鉴权方案）按需新增。
