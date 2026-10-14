### Phase 7：真实库存现存量报表接入

项目：`D:\coding\ai\wms-ai-assistant`

以我提供的两份 PostgreSQL 报表 SQL 为业务依据，将现有 `get_inventory` Tool 改为查询真实 WMS 条码库存数据。

#### 范围

1. 先阅读当前 `backend/app/tools/get_inventory.py`、`backend/app/db/business_session.py`、相关 Settings、Tool Registry 和定向测试。
2. 检查测试库中两份 SQL 涉及的真实表、列、关联关系及字段类型。
3. 第一份报表作为库存现存量查询基础，第二份报表用于货架维度查询。
4. 保留原报表的关键过滤条件：
   - `barcode.archived = '0'`
   - `bc.archived = '0'`
   - `status.code = 'BARCODE_STATUS_IN'`
   - `HAVING SUM(barcode.qty) <> 0`
5. 将报表平台的 `${if(...)}` 转换为参数化查询，不允许把用户输入直接拼接进 SQL。
6. 物料编码、组织、仓库、库位、货架、批次等过滤条件必须遵循已确认的字段映射；不支持的筛选参数应明确拒绝。
7. 对动态 schema/table/column 使用固定配置和白名单；保留只读事务、查询超时和返回数量限制。
8. 核对 `latest_record` 的 `MAX(ID)` 与 `MAX(update_time)` 语义，未经验证不得擅自修改。
9. 使用测试库已有物料，对照原始报表查询结果和 Tool 结果。验证物料、仓库、库位、批次、单位等字段的分组和数量一致性。
10. 增加必要的定向测试：正常查询、空结果、非法参数、过滤条件、只读约束和结果一致性。

#### 限制

- 不修改现有应用库 `DATABASE_URL`。
- 不连接生产业务库。
- 不创建虚假业务表或测试成功数据。
- 不写入业务数据库。
- 不打印数据库凭据。
- 不改 Prompt、Validator、Executor 或无关架构。
- 不进行无关 Git 提交，不自动推送或创建 PR。
- 先运行本次相关定向测试，不重复无必要的全量测试。

#### 完成报告

说明修改文件、真实表字段、库存计算口径、报表对照结果、测试结果、DB 写入情况和仍存在的业务歧义。

如果原报表本身存在字段或关联问题，先报告证据，不要擅自改变业务口径。