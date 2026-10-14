# Phase 7 Recovery — M2.1 Conversation Ownership Audit

## 一、目标

对当前 `phase7` 分支上的 M1 认证基础进行只读审计，规划 Conversation 资源所有权接入。

当前基线：

- HEAD：`28c54e00e46a6cb0728934382809c0a4070150c5`
- M1 已本地提交
- M1 治理门禁 PASS
- 尚未 Push、创建 PR 或部署

本阶段只做审计与实施计划，**不修改代码，不进入 M2.2**。

## 二、先阅读真实代码

检查：

- `backend/app/api/` 下的 Chat、Conversation、RAG API
- `backend/app/services/` 下对应服务
- `backend/app/db/models/` 中 Conversation 及关联模型
- `backend/app/db/` 中 Repository
- `backend/app/auth/`
- `backend/app/api/auth_dependency.py`
- `backend/app/services/resource_ownership_service.py`
- `tests/` 中对应 API、服务和 DB 测试

同时检查 API 路由注册、现有 API-Key 依赖、错误响应格式及事务边界。

不得根据端点名称猜测实现。

## 三、审计范围

优先检查：

- `/conversations*`
- `/chat`
- `/chat/with-tools`
- `/ai/chat`
- `/rag/answer`

Usage 和 Observability 暂时只记录现状与风险，不在本阶段修改。

## 四、重点确认

### 1. Conversation 所有权

确认 Conversation、Message 及相关记录之间的真实关联方式：

- 是否已有 Conversation ID
- Message 是否通过外键关联 Conversation
- 创建 Conversation 的入口有哪些
- 是否存在历史记录没有 `resource_owner` 记录
- 当前是否存在跨用户访问的可能

### 2. 认证与授权

确认：

- 哪些路由没有认证
- 哪些路由使用 API-Key
- 哪些路由适合使用 M1 的 `current_user` 会话依赖
- API-Key 服务调用与用户 Session 是否可能冲突
- 现有端点是否存在隐式匿名访问

不得将 API-Key 身份伪装为人类用户身份。

### 3. 资源所有权策略

提出最小一致策略：

- 新建 Conversation 时登记所有者
- 读取、追加消息、删除或修改 Conversation 前检查所有权
- 不存在的资源与无权访问的资源采用一致的外部响应策略，避免泄露资源是否存在
- 所有权服务或数据库异常时，不得默认放行
- 明确处理历史无所有权记录的 Conversation，不能默认归属给第一个访问者

不要在本阶段自行选择危险的历史数据迁移或归属策略。

### 4. 测试矩阵

规划测试：

- 未登录访问
- 用户 A 访问自己的 Conversation
- 用户 B 访问用户 A 的 Conversation
- 无所有权记录的历史 Conversation
- 无效、过期或已撤销 Session
- API-Key 兼容路径
- 资源所有权写入失败
- 数据库异常时 fail-closed
- 消息列表及关联内容的越权读取
- Conversation ID 篡改

## 五、严格禁止

- 不修改代码、测试、配置或数据库
- 不新增数据库表或 migration
- 不连接生产 WMS/ERP 数据库
- 不调用真实 LLM
- 不修改 M1 commit 或 Git 历史
- 不执行 `git add`、`git commit`、`git push`
- 不执行 `reset`、`checkout`、`clean`、`stash`
- 不部署
- 不开始 M2.2

## 六、最终报告

输出：

1. 当前分支与 HEAD
2. Conversation / Message 实际数据模型
3. 当前相关 API 的认证情况
4. 所有权缺口与潜在越权路径
5. 历史 Conversation 的所有权风险
6. 推荐的最小修改文件清单
7. API-Key 兼容性方案
8. 测试矩阵
9. DB 迁移需求（若无则说明无）
10. 未解决问题与实施顺序

最后明确：

```text
M2.1 Conversation Ownership Audit: COMPLETE / BLOCKED
Code Changes: NONE
Git History Changes: NONE
M2.2: NOT STARTED
Push: NOT EXECUTED
Deployment: NOT EXECUTED
STOP
```