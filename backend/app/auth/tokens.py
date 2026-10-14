"""不透明会话令牌 + 会话绑定 CSRF 签名（Phase 7 Recovery M1）。

令牌 = ``secrets.token_urlsafe(32)``（256 bit 熵；URL / Cookie 安全字符集）。

纪律：

    * 令牌**本体**只在登录响应的 ``Set-Cookie`` 头出现一次；
    * 数据库只存 ``sha256(token)``（``hash_session_token``）——库表泄露
      不直接暴露可用令牌；
    * 令牌**不**进 URL / 查询参数 / 日志 / 异常文本；
    * 不做"可逆"编码（无 base64 包装、无 JWT——撤销即删行，天然即时生效）。

CSRF（M1 Security Review 修复——**签名双提交，绑定会话**）：

    朴素 Double-Submit Cookie（header == cookie）比较的是两个**客户端可控**
    的值；攻击者只要能注入 Cookie（如兄弟子域 / 共享域），即可让两者相等
    ⇒ OWASP 明确要求与会话绑定。本实现改为**服务端签名**：

        csrf_token = HMAC-SHA256(key = token_hash, msg = session_id).hexdigest()

    * ``key`` = 服务端**独占**的会话令牌哈希（浏览器不可得；不进 Cookie /
      日志 / 响应）；
    * ``msg`` = 服务端签发的 ``session_id``（标识符，非凭据）；
    * 校验 = 用**当前会话**（由 HttpOnly Session Cookie 定位）的
      ``token_hash`` 重算签名，再与请求头恒定时间比较；
    * ⇒ 伪造值必须等于 HMAC(受害者 token_hash, 受害者 session_id)——
      不知道 token_hash 即不可构造；换会话 / 吊销 / 过期后旧值全部失效；
    * 无新增依赖（hmac/hashlib stdlib）、无新增表 / 列 / 环境变量。
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Final

#: 令牌熵（字节；32 字节 = 256 bit）。
TOKEN_BYTES: Final[int] = 32

#: 哈希输出编码（hex；固定 64 字符，与列宽对齐）。
TOKEN_HASH_ENCODING: Final[str] = "utf-8"

#: CSRF 签名输出长度（SHA-256 hex = 64 字符）。
CSRF_TOKEN_LENGTH: Final[int] = 64


def generate_session_token() -> str:
    """生成一个新的不透明会话令牌（每次调用都不同）。"""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_session_token(token: str) -> str:
    """计算令牌的 SHA-256 hex（入库 / 查表都用哈希，不用本体）。"""
    return hashlib.sha256(token.encode(TOKEN_HASH_ENCODING)).hexdigest()


def sign_csrf_token(*, session_id: str, key: str) -> str:
    """为**特定会话**签发 CSRF 签名值（``key`` 必须是服务端独占材料）。

    Args:
        session_id: 服务端签发的会话 ID（标识符；非凭据）。
        key:        签名密钥——实现上传入该会话的 ``token_hash``
                    （仅存于服务端 DB，**绝不**暴露给浏览器）。

    Returns:
        HMAC-SHA256 hex（64 字符）。
    """
    return hmac.new(
        key.encode(TOKEN_HASH_ENCODING),
        session_id.encode(TOKEN_HASH_ENCODING),
        hashlib.sha256,
    ).hexdigest()


def verify_csrf_token(*, session_id: str, key: str, candidate: str) -> bool:
    """恒定时间校验 CSRF 签名值（任何格式异常 ⇒ ``False``）。"""
    if not isinstance(candidate, str) or len(candidate) != CSRF_TOKEN_LENGTH:
        return False
    expected = sign_csrf_token(session_id=session_id, key=key)
    return hmac.compare_digest(expected, candidate)


__all__ = [
    "TOKEN_BYTES",
    "TOKEN_HASH_ENCODING",
    "CSRF_TOKEN_LENGTH",
    "generate_session_token",
    "hash_session_token",
    "sign_csrf_token",
    "verify_csrf_token",
]
