# Phase 7 Step 9：上线准备与剩余阻塞项关闭

项目：`D:\coding\ai\wms-ai-assistant`

## 一、目标

基于 Phase 7 Step 8 的真实 Agent E2E PASS，关闭上线前剩余的必要验证项。

本阶段只做安全检查、差距核实和最小必要修复。

不新增业务功能，不部署，不创建 Git 提交。

## 二、先检查现状

阅读当前部署配置、FastAPI 启动方式、Agent Command API、认证/授权机制、Runtime Read API、LLM Provider 配置，以及已有治理门脚本。

先查看 `git status --short` 和 `git diff --stat`，保留现有未提交工作，不重置、不覆盖、不清理无关变更。

## 三、核对 LLM 模型标识

Step 8 中配置模型为 `deepseek-chat`，但响应记录的模型为 `deepseek-flash`。

检查：
- 实际发送给 DeepSeek 的 model 参数
- Provider 是否修改或覆盖模型参数
- LLMResponse.model 的取值来源
- llm_usage_record 中配置模型和响应模型的字段语义

优先通过代码和已有记录分析，不要为了验证而再次调用真实 LLM。

如果无法确认是否属于服务端别名映射，记录为未解决问题，不要伪造结论。

不要修改 Prompt 或模型配置来掩盖差异。

## 四、核对 HTTPS 与网络边界

检查：
- FastAPI 应用实际监听方式
- 是否配置 HTTPS/TLS 或由受信任的反向代理终止 TLS
- Agent Command API 是否有适当的认证和项目作用域校验
- Runtime Read API 是否存在越权读取风险
- API Key 和数据库凭据是否可能进入日志、响应或错误信息
- 业务数据库是否继续使用独立只读连接

区分：
1. 已由代码或测试证明
2. 仅由配置证明
3. 必须在部署环境才能验证

如果当前本机开发环境没有 HTTPS，不要为了通过审计而临时声称 HTTPS 已启用。生产 TLS 无法验证时，应明确保持 DEPLOYMENT BLOCKED。

## 五、治理门与回归测试

先找出上一次未执行的治理门及其依赖。

只执行：
- 尚未执行的相关治理门
- 与本阶段改动直接相关的测试
- 必要的共享 Runtime 回归

已有明确通过证据的测试批次不重复运行。

如果发现失败，定位具体原因；不要修改核心架构来强行通过。

## 六、修复边界

只允许修复能够确认的上线阻塞问题，并且只做最小改动。

不得：
- 修改库存报表口径
- 修改 Text-to-SQL Prompt、Validator 或 Executor
- 修改 Agent 决策策略
- 修改业务数据库数据
- 增加新数据库表或 migration
- 引入新框架
- 创建 Git commit、推送或 PR
- 部署到生产环境

## 七、最终报告

列出：
1. HTTPS/TLS 状态及证据
2. API 认证和项目隔离检查结果
3. LLM 配置模型与响应模型差异的原因或未解决状态1
4. 治理门执行结果
5. 本次定向测试结果
6. 修改文件及修改原因
7. 数据库写入情况
8. 剩余上线阻塞项

最后给出：
- `READY FOR DEPLOYMENT`
- 或 `DEPLOYMENT BLOCKED`

只有所有必要安全条件都经过验证，才允许标记 `READY FOR DEPLOYMENT`。

完成后立即 STOP，不进入下一 Phase。