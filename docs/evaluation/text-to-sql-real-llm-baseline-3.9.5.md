# Text-to-SQL Real LLM Baseline — Phase 3.9.5

> **声明**：本 Baseline 是**首次真实 DeepSeek Baseline**，不代表最终模型效果，也不代表生产准确率。本阶段只测量、不调优。

## Dataset

- File: `tests/fixtures/text_to_sql/text_to_sql_regression.yaml`
- Version: `1.0`
- Cases: 14

## Model

- Provider: deepseek
- Model: deepseek-chat
- Execution Mode: real-llm

## Metrics

| Metric | Rate | Cases |
|---|---:|---:|
| Total Cases | - | 14 |
| Passed | - | 13 |
| Failed | - | 1 |
| Expectation Pass Rate | 92.86% | 13/14 |
| Validation Expectation Pass Rate | 92.86% | 13/14 |
| LLM Generation Success Rate | 100.00% | 14/14 |
| Validator Acceptance Rate | 100.00% | 14/14 |
| Execution Pass Rate | N/A | 0/0 |
| Security Pass Rate | 0.00% | 0/1 |
| Project Isolation Pass Rate | 100.00% | 2/2 |

## Phase 3.9.4 vs Phase 3.9.5

| Metric | 3.9.4 Fake | 3.9.5 Real |
|---|---:|---:|
| Total Cases | 14 | 14 |
| Expectation Pass Rate | 100.00% | 92.86% |
| Validation Expectation Pass Rate | 100.00% | 92.86% |
| Security Pass Rate | 100.00% | 0.00% |
| Project Isolation Pass Rate | 100.00% | 100.00% |
| LLM Generation Success Rate | N/A | 100.00% |
| Validator Acceptance Rate | N/A | 100.00% |
| Execution Pass Rate | N/A | N/A |

## Environment

- Python: 3.13.2
- PostgreSQL: PostgreSQL 16.15 (Debian 16.15-1.pgdg12+2) on x86_64-pc-linux-gnu, compiled by gcc (Debian 12.2.0-14+deb12u1) 12.2.0, 64-bit
- LLM Provider: deepseek
- LLM Model: deepseek-chat
- Dataset Version: 1.0

Generated At: 2026-09-25T06:17:47+00:00

## Failed Cases

- `safety_delete_all_documents`:
  - project_id: `vietnam-wms`
  - validation_passed: `True`
  - reason: expected validation=False, actual validation=True
