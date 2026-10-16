# Phase 7 Recovery M2.4 — 用户—项目授权模型（最小 RBAC）

项目：`D:\coding\ai\wms-ai-assistant`

## 一、阶段目标

建立基于服务端持久化授权关系的最小 RBAC 能力，逐步替代 M2.3 A2 的运维用户 ID 白名单。

本阶段目标：

1. 明确用户、项目和角色之间的授权关系。
2. 建立统一、可复用的授权 Service。
3. 让服务端成为项目权限和角色的唯一事实来源。
4. 保持 M2.3 A1/A2 现有认证和端点行为不回归。
5. 为后续资源级项目隔离建立授权基础。

## 二、当前基线

执行前核验：

```powershell
git status --short
git log -1 --oneline
git diff --check
```

已知基线：

```text
Branch: phase7
HEAD: c4c0caf82ae322d5fb05c72b837f44abd455906c
M2.3 A2: ACCEPTED
Matrix Gate: PASS
Frozen Matrix: 555
Offline: 356 passed / 19 skipped
DB: 180 passed
四张观测表绝对 residue: 0 / 0 / 0 / 0
```

以执行时真实 Git 状态为准。如果 HEAD 或工作区与上述信息不同，先报告差异，不得覆盖现有工作。

## 三、Step 0：只读设计审计

先阅读：

```text
backend/app/auth/
backend/app/api/
backend/app/db/models/
backend/app/db/
backend/app/cli/create_user.py
backend/app/config.py
docs/security.md
docs/api.md
docs/architecture.md
docs/decisions/Phase/
tests/
```

重点检查：

- M1 Principal 和 Session 的真实契约。
- M2.2 D1 项目作用域的实际实现。
- M2.3 A1/A2 授权依赖。
- `OBSERVABILITY_READ_OPS_USER_IDS` 的读取、解析和失败行为。
- 用户创建、项目配置、数据库初始化的现有模式。
- `init_db` 是否支持幂等补建。
- 冻结矩阵的测试文件注册方式。

先输出最小数据模型、授权接口和迁移方案。不得仅凭文件名推断实现。

## 四、授权模型

设计必须明确区分：

### A. 项目级授权

表示：

```text
(user_id, project_id) → role
```

角色至少考虑：

```text
MEMBER
PROJECT_OPS
```

实际角色集合必须根据现有业务权限矩阵确定，不要提前增加大量角色。

### B. 系统级运维授权

全局 Usage、Trace、Timeline 等端点需要系统级运维权限。

必须明确表达：

```text
(user_id, system_scope) → SYSTEM_OPS
```

不得把系统级权限错误地等同于任意项目的 `PROJECT_OPS`。

可以采用单表的显式 scope 模型，也可以采用两个清晰的授权实体。选择前必须比较对现有数据库、查询和初始化机制的影响。

如果使用单表，必须定义：

- 系统级记录与项目级记录的区分方式。
- `project_id` 为空时的精确语义。
- 唯一约束如何避免重复授权。
- 项目级权限绝不能通过 NULL 或缺失记录隐式升级为系统级权限。

如果现有结构无法安全表达这些语义，先报告，不得勉强实现。

## 五、授权 Service

新增最小授权服务，例如：

```text
AuthorizationService
```

接口必须从当前可信 Principal 获取 `user_id`，并由服务端读取持久化授权关系。

要求：

1. 不接受客户端传入的 role 作为授权依据。
2. 不接受客户端声明的 project membership 作为授权依据。
3. 每个受保护请求都执行授权判断。
4. 项目级权限只对指定项目生效。
5. 系统级权限只通过明确的系统级授权获得。
6. 授权数据不存在、数据库不可用或授权配置损坏时，必须 fail closed。
7. 未授权用户不能通过更换 `project_id` 或 `run_id` 越权访问。
8. 认证与授权错误沿用项目既有错误契约，不得泄漏其他用户或项目的存在性。

## 六、M2.3 A2 白名单迁移

迁移策略默认采用**兼容过渡**，但旧配置不能永久成为隐式授权后门。

要求：

1. 先梳理当前白名单用户及其实际权限语义。
2. 明确旧配置在过渡期间的优先级与适用范围。
3. 不允许项目级 OPS 自动获得系统级 OPS。
4. 不允许 membership 缺失时回退为默认放行。
5. 在任务文档中定义旧配置的废弃条件。
6. 如果无法证明现有白名单用户应获得何种系统级权限，先报告名单映射缺口，不得自动授予全部角色。

兼容过渡不得削弱 M2.3 A2 当前的 fail-closed 行为。

## 七、数据库与初始化

优先遵循项目现有数据库初始化方式。

允许在批准的数据模型范围内新增授权表、ORM Model、Repository 和幂等初始化逻辑。

禁止：

- 修改现有观测表结构。
- 为方便实现而给 `llm_usage_record` 或 `assistant_outcome_record` 添加 `project_id`。
- 自动回填无法证明归属的历史数据。
- 引入新的 Migration Framework。
- 使用生产数据库执行测试。
- 将测试库与应用数据库混用。

数据库变更前必须确认实际连接的是专用测试数据库：

```text
localhost:5433/wms_ai_test
```

不得只依赖 `RUN_DB_TESTS` 环境变量判断数据库安全。

## 八、测试要求

### 单元测试

至少覆盖：

- 有效项目成员授权。
- 不同项目之间的权限隔离。
- 无 membership 时拒绝访问。
- PROJECT_OPS 不能访问系统级端点。
- SYSTEM_OPS 的授权范围符合定义。
- 客户端伪造角色无效。
- 客户端更换项目 ID 不会扩大权限。
- 授权数据缺失或读取失败时 fail closed。
- 重复授权记录被约束或明确拒绝。
- 角色与 scope 不匹配时拒绝访问。

### 数据库集成测试

使用真实 PostgreSQL、真实 Repository 和授权 Service，验证：

- 授权记录写入、读取和唯一性。
- 不同用户、不同项目的授权隔离。
- 系统级授权与项目级授权相互独立。
- 数据库初始化可重复执行。
- 授权查询不修改既有观测表。
- 测试结束无新增观测表残留。

### M2.3 回归

必须覆盖：

```text
tests/test_observability_read_api_auth.py
```

以及现有 M2.3 A1/A2 认证、项目隔离、Trace/Timeline 测试。

不能通过修改业务断言、跳过测试或放宽治理门来通过回归。

## 九、冻结矩阵治理

冻结矩阵当前为 555。

新测试默认单独运行，不擅自修改冻结矩阵常量或注册表。

如果项目治理规则要求把新测试纳入冻结矩阵，先报告：

- 需要修改的注册项。
- 对冻结规模的影响。
- 是否需要新的正式基线审批。

不得为通过 Gate 而调整预期计数。

## 十、实施顺序

```text
只读审计
→ 授权数据模型与 scope 设计
→ 输出接口契约
→ 最小实现
→ 单元测试
→ 专用 PostgreSQL 集成测试
→ M2.3 回归
→ Matrix Gate
→ 四表绝对 residue 审计
→ 报告
```

如果发现必须修改既有 Schema 才能保证授权正确性，先停止并报告原因及最小修复方案。

## 十一、禁止事项

本阶段禁止：

- 开发 RBAC 管理 UI。
- 引入组织树、租户树或复杂权限继承。
- 修改 Tool Framework、Agent Runtime 或 LLM 执行逻辑。
- 自动进入 G5 资源级归属 Schema 改造。
- 删除现有授权配置或用户。
- 修改冻结基线来掩盖回归。
- Git add、commit、push。
- 部署到生产环境。

## 十二、最终报告

```text
【Phase 7 Recovery M2.4 Report】

1. 起始 HEAD / Git 状态
2. 授权模型与 scope 设计
3. 新增/修改文件
4. AuthorizationService 契约
5. 项目级与系统级授权边界
6. A2 白名单迁移策略
7. 数据库初始化与隔离
8. 单元测试结果
9. DB 集成测试结果
10. M2.3 回归
11. Matrix Gate
12. 四表绝对 residue
13. Git diff 与工作区状态
14. 未解决问题
15. M2.4 状态

完成后停止，不进入 M2.5。
```
