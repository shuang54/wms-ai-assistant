# ADR-003

## Decision

AI 不允许直接访问 PostgreSQL。

## Reason

1. 安全
2. 权限控制
3. 审计
4. 防止错误 SQL
5. 业务规则统一

## Architecture

AI
 ↓
Tool
 ↓
Service
 ↓
WMS API
 ↓
Database