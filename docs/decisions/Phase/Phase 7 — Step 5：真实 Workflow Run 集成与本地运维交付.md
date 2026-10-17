# Phase 7 — Step 5：真实 Workflow Run 集成与本地运维交付

项目：`D:\coding\ai\wms-ai-assistant`

## 目标

让 Workflow 运维工作台能够在专用测试环境中读取真实持久化 Workflow Run，同时完善本地启动、登录、项目授权与验收流程。

本阶段不开发 Workflow 写操作，不连接生产 WMS 数据库。

## 开始前

```powershell
git status --short
git diff --check
git log -3 --oneline
```

完整保留 Step 2、Step 3、Step 3B、Step 4 所有未提交修改。禁止 reset、clean、stash、覆盖文件、commit 或 push。

## 1. 检查现有基础设施

阅读并确认真实接口：

- Workflow Runtime Factory / Store Adapters
- Workflow Definition Registry
- Workflow Read Service
- Workflow UI Read API
- Session 登录机制
- 项目授权 CLI 与 authorization_grant
- 专用 PostgreSQL 测试配置
- 前端静态服务与 Playwright 配置

不要创建第二套认证、授权或 Workflow 存储体系。

## 2. 真实 Run 读取验收

在专用测试数据库中，使用项目现有 Runtime 和真实 Store Adapter 创建可重复的测试 Run。

验证：

- Run Detail 能读取真实持久化记录。
- Node Attempts 能读取真实历史 Attempt。
- Timeline 只展示持久化事实。
- Graph 使用与 Run 的 `workflow_id` 和 `workflow_version` 匹配的真实注册定义。
- 定义不存在或版本不匹配时 fail-closed。
- 不生成虚假的成功、失败、审批或时间事件。

测试数据只能在专用测试环境中创建和清理，必须有明确的清理范围，并确认最终 DB residue 为零。

不得连接生产 WMS 数据库。

## 3. 登录与项目授权闭环

使用现有 Session 和项目授权机制验证：

- 未登录用户不能读取。
- 获授权项目成员能够读取。
- 未授权项目不能读取。
- 跨项目资源不会泄漏存在性。
- Session 过期后页面显示正确提示。

如果需要授权测试用户，使用现有授权 CLI；不得为方便测试而绕过认证或硬编码管理员权限。

## 4. 本地启动与运维说明

完善 README，明确：

- 后端启动命令。
- 前端静态服务启动命令。
- 本地 API 与页面的同源配置。
- 如何登录并获取有效 Session。
- 如何为测试用户授予项目成员权限。
- 如何获得 Run ID 并查询。
- 常见 401、403、404、502、503 的排查方法。

命令必须来自项目现有脚本与真实配置，不要猜测。

## 5. 真实浏览器集成测试

继续使用已安装的 Playwright 1.47.2 与 Chromium，不升级 Node，不新增浏览器。

保留现有 Mock E2E；另增加少量专用测试，在测试服务器与专用数据库可用时验证真实 API 数据读取。

如果真实测试环境未配置，明确标记为跳过或环境阻塞，不得将 Mock 测试写成真实 DB 集成结果。

## 6. 禁止事项

- 不修改 Workflow Runtime 执行逻辑。
- 不修改数据库 Schema。
- 不接生产数据库。
- 不增加启动、取消、重试、恢复、审批裁决等 UI 写操作。
- 不修改冻结基线或治理门。
- 不删除既有测试或削弱断言。
- 不提交或推送代码。

## 7. 完成报告

```text
【Phase 7 — Step 5 Report】

1. 开始时 Git 状态
2. 真实 Run 数据来源
3. Definition Registry 版本匹配结果
4. Session / Project Authorization 验收
5. 真实 API 与浏览器集成结果
6. Mock E2E 与真实 E2E 区分
7. DB residue
8. 本地启动说明
9. 前后端测试结果
10. compileall / diff check
11. 安全边界
12. 未解决问题
13. Git 状态

完成后 STOP，不进入 Phase 8。
```

