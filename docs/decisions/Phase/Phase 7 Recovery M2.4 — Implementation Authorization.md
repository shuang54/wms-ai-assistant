# Phase 7 Recovery M2.4 — Implementation Authorization

## 一、决策结果

项目方已明确批准：

- D1：单表 `ai_ops.authorization_grant` + 显式 `scope_type`。
- D2：将项目成员授权接入现有两个项目级观测端点。
- D3：系统级端点在过渡期采用旧白名单与显式 `SYSTEM_OPS` 授权并集，但旧白名单不可用时仍返回 503。

现在进入 M2.4 最小实现阶段。

## 二、实施前保护

确认：

```text
HEAD = c4c0caf82ae322d5fb05c72b837f44abd455906c
Branch = phase7
```

重新检查 Git 状态。不得覆盖已有文件或清理现有工作。

## 三、最小实现范围

1. 新增授权 Grant ORM Model、Repository 和 `AuthorizationService`。
2. 使用数据库级 CHECK 与 partial unique indexes 保证 Scope/Role 一致性及授权唯一性。
3. 在授权依赖中区分项目级授权与系统级授权。
4. 将 `MEMBER` / `PROJECT_OPS` 授权接入两个现有项目级观测端点。
5. 系统级端点使用显式 `SYSTEM_OPS` 授权与旧白名单并集。
6. 保持旧白名单缺失、空白或格式错误时的 503 语义。
7. 通过现有 CLI 扩展为用户授予明确的项目角色或系统级角色；不得自动为现有用户授予角色。
8. 更新安全文档、API 文档和环境变量说明。

## 四、授权数据规则

仅允许以下组合：

```text
PROJECT + MEMBER + non-empty project_id
PROJECT + PROJECT_OPS + non-empty project_id
SYSTEM + SYSTEM_OPS + NULL project_id
```

其他组合必须被数据库拒绝。

不得将 `PROJECT_OPS` 推导为 `SYSTEM_OPS`，也不得将 `SYSTEM_OPS` 推导为任意项目成员。

## 五、迁移与数据库边界

- 只新增授权表，不修改现有观测表 Schema。
- 不给 `llm_usage_record` 或 `assistant_outcome_record` 增加 `project_id`。
- 使用项目现有幂等初始化机制。
- 所有 DB 集成测试必须硬断言连接为 `localhost:5433/wms_ai_test`。
- 不自动为旧白名单用户写入角色；未知映射必须保留为待配置项。
- 不访问生产数据库。

## 六、测试与回归

新增授权 Service 单元测试及专用 PostgreSQL 集成测试，覆盖：

- 项目成员、项目运维、系统运维的正向授权。
- 项目越权、系统权限越权和伪造客户端角色。
- Scope/Role 不匹配、重复授权。
- 授权存储故障与缺少授权时的 fail-closed 行为。
- 旧白名单与授权表并集及旧配置 503 回归。
- M2.3 A1/A2 认证与项目过滤回归。
- 初始化幂等性。

新增测试默认独立运行。不得擅自修改冻结矩阵的 555 项基线或注册表。

完成后运行相关离线测试、专用 DB 测试、现有 M2.3 回归和 Matrix Gate，并执行四张观测表的独立绝对 residue 审计。

## 七、禁止事项

- 不开发管理 UI。
- 不实现组织树或租户树。
- 不修改 Tool Framework、Agent Runtime 或 LLM 执行逻辑。
- 不改造资源级 Schema（G5）。
- 不删除旧白名单配置。
- 不修改冻结基线以掩盖回归。
- 不部署生产环境。
- 不执行 Git add、commit、push。

## 八、完成后报告

提供文件变更、授权规则、白名单兼容行为、测试统计、DB 结果、Matrix Gate、四表绝对 residue、Git 状态和未解决问题。

完成 M2.4 后立即 STOP，不进入 M2.5。