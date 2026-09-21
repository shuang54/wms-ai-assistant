"""Embedding 异常体系（Phase 3.5.1）。

层级：

    EmbeddingError
    ├── EmbeddingConfigurationError   配置缺失 / 非法（Key / Model / Base URL / Dimension / Timeout）
    ├── EmbeddingInputError           输入非法（空文本 / 纯空白 / 非 str）
    ├── EmbeddingAPIError             网络 / 超时 / HTTP 非 2xx
    ├── EmbeddingResponseError        响应解析失败 / 结构异常
    └── EmbeddingDimensionError       向量维度与 EMBEDDING_DIMENSION 不一致

调用方可以统一：

    except EmbeddingError:
        ...

注意：所有异常消息**不得**包含 API Key（完整或部分）。
"""
from __future__ import annotations

__all__ = [
    "EmbeddingError",
    "EmbeddingConfigurationError",
    "EmbeddingInputError",
    "EmbeddingAPIError",
    "EmbeddingResponseError",
    "EmbeddingDimensionError",
]


class EmbeddingError(Exception):
    """Embedding 相关错误的基类。"""


class EmbeddingConfigurationError(EmbeddingError):
    """Embedding 配置错误（API Key / Model / Base URL / Dimension / Timeout 缺失或非法）。"""


class EmbeddingInputError(EmbeddingError):
    """输入文本非法（空字符串 / 纯空白 / 非 str）。

    在发送给真实 API 之前拒绝，避免无效请求与费用。
    """


class EmbeddingAPIError(EmbeddingError):
    """Embedding 请求失败（连接失败、超时、HTTP 非 2xx 等）。"""


class EmbeddingResponseError(EmbeddingError):
    """Embedding 响应解析失败（JSON 非法、缺 data / embedding、结构异常）。"""


class EmbeddingDimensionError(EmbeddingError):
    """返回向量维度与配置的 EMBEDDING_DIMENSION 不一致。

    严禁自动补 0 / 截断 / 修改数据库维度——必须直接失败，
    由使用者修正配置或按迁移流程修改 Phase 3.2 schema。
    """
