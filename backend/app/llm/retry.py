"""LLM Retry 分类（Phase 3.10.2）—— 只分类，不执行重试。

责任边界（详见 docs/architecture.md §8.3）：

    Transport reliability（HTTP timeout / 网络错误 / 状态码分类）
        ↓ 由 LLM Client / Provider 层负责（本模块提供分类依据）
    Business semantic retry（Validator 拒绝后重新生成等）
        ↓ 由 TextToSQLService / 未来业务服务负责

本模块提供的是**纯函数分类**：

    * 无网络
    * 无 sleep
    * 无副作用（不改输入、无状态）
    * deterministic（同一输入恒得同一结果）

**不做**自动重试 / 指数退避 / jitter / retry queue / circuit breaker
（未来单独阶段处理）。

分类依据是 Phase 3.10.2 的 LLM 层异常体系
（``client.LLMError`` 家族 + ``LLMRequestError.status_code``），
不感知 httpx / OpenAI SDK 异常类型。
"""
from __future__ import annotations

from backend.app.llm.client import LLMConfigError, LLMError, LLMResponseError

__all__ = [
    "is_retryable_llm_error",
]


def is_retryable_llm_error(error: BaseException) -> bool:
    """判断一个 LLM 调用异常是否属于"可重试"类别。

    规则（Phase 3.10.2 §六）：

    可重试：
        * 网络级失败：连接暂时失败、连接 / 读取超时
          （``LLMRequestError`` 且 ``status_code is None``）；
        * HTTP 429（rate limit）；
        * HTTP 5xx（服务端暂时性错误）。

    不可重试：
        * HTTP 4xx（400 / 401 / 403 / 404 ...）——请求本身有问题，
          重试同样请求必然复现；
        * ``LLMConfigError``——配置缺失 / 非法（Key、Base URL、Model）；
        * ``LLMResponseError``（含 ``LLMToolCallFormatError``）——
          响应结构异常，属协议 / 代码层面问题；
        * 非 ``LLMError`` 家族异常——代码 bug / 意外异常，
          交由上层原有错误处理，不做隐式重试。

    Args:
        error: 捕获到的异常。对携带 ``status_code`` 属性的非 LLM
               异常对象保持 duck-typing 容错（``getattr``），
               但非 ``LLMError`` 家族一律返回 ``False``。

    Returns:
        bool: 是否允许未来对该调用发起重试（本阶段只分类，不执行）。
    """
    # 非 LLM 层异常（代码 bug / 意外异常）→ 不重试
    if not isinstance(error, LLMError):
        return False
    # 配置错误：重试无意义（Key / URL / Model 不会因重试而出现）
    if isinstance(error, LLMConfigError):
        return False
    # 响应结构异常（协议级 / 代码 bug 性质）→ 不重试
    if isinstance(error, LLMResponseError):
        return False
    # ---- LLMRequestError ----
    # status_code=None → 网络级失败（连接失败 / 超时 / 其它 httpx 错误）
    #                     → 暂时性，可重试
    # 429 / 5xx         → 暂时性，可重试
    # 其它 4xx          → 请求本身有问题，不重试
    status_code = getattr(error, "status_code", None)
    if not isinstance(status_code, int):
        return True
    return status_code == 429 or status_code >= 500
