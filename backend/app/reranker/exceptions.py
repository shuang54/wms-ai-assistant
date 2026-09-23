"""Reranker 异常体系（Phase 3.5.12）。

    RerankerError (Exception)
        ├── RerankerConfigurationError  配置非法（enabled 但缺模型名等）
        ├── RerankerInputError          输入非法（空 query / 空 documents / 非 str）
        └── RerankerModelError          模型加载 / 推理失败
                                          （依赖缺失、下载失败、CUDA OOM 等）
"""
from __future__ import annotations

__all__ = [
    "RerankerError",
    "RerankerConfigurationError",
    "RerankerInputError",
    "RerankerModelError",
]


class RerankerError(Exception):
    """Reranker 通用异常基类。"""


class RerankerConfigurationError(RerankerError):
    """配置非法：模型名为空 / device 显式指定但无法解析等。"""


class RerankerInputError(RerankerError):
    """输入非法：空 query / 空 documents / 非 str 元素。"""

    def __init__(self, message: str, *, index: int | None = None) -> None:
        super().__init__(message)
        self.index = index


class RerankerModelError(RerankerError):
    """模型层失败：依赖缺失（torch / transformers）、模型下载 / 加载失败、推理异常。

    携带 cause 类型名，便于离线实验报告归因（不携带完整 traceback /
    本地缓存路径到日志）。
    """

    def __init__(self, message: str, *, cause_type: str | None = None) -> None:
        super().__init__(message)
        self.cause_type = cause_type
