"""密码哈希（Phase 7 Recovery M1）。

算法：**scrypt**（``hashlib.scrypt``，OpenSSL 后端；stdlib only）。

为什么不引入 bcrypt / passlib / argon2：

    * ``requirements.txt`` 未包含任何密码库（审计确认：bcrypt / passlib /
      argon2 / cryptography 均未安装）；
    * AGENTS.md 纪律：不为单点能力引入新依赖；
    * ``hashlib.scrypt`` 是内存硬化 KDF，参数固定后可复现，满足
      "只能保存安全哈希结果、禁止明文"的要求。

存储格式（单一字符串，自描述参数，便于将来参数升级时识别旧哈希）：

    ``scrypt$N$r$p$<salt_b64>$<hash_b64>``

安全约束：

    * 每密码随机盐（16 字节；``secrets.token_bytes``）；
    * 校验用 ``hmac.compare_digest``（恒定时间）；
    * **永不**在日志 / 异常 / 响应中输出密码或哈希；
    * 长度下限在 ``MIN_PASSWORD_LENGTH``（创建用户时由 Service 强制）；
      上限 ``MAX_PASSWORD_LENGTH`` 是 DoS 防护（超长输入直接拒绝，不进 KDF）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import Final

#: 格式前缀（升级算法时用新前缀；旧前缀记录保持可校验）。
PASSWORD_HASH_PREFIX: Final[str] = "scrypt"

#: scrypt 参数（N 必须是 2 的幂；r*p < 2^30）。N=2^14 · r=8 ⇒ 16 MiB 内存 /
#: 数十毫秒——刻意低于 OpenSSL 默认 scrypt 内存上限（32 MiB），避免在
#: 默认配置的 OpenSSL 3.x 上触发 "memory limit exceeded"。
SCRYPT_N: Final[int] = 2**14
SCRYPT_R: Final[int] = 8
SCRYPT_P: Final[int] = 1

#: 盐长度（字节）与派生密钥长度（字节）。
SALT_BYTES: Final[int] = 16
KEY_LENGTH: Final[int] = 32

#: 密码长度策略（创建时强制；过短拒绝 / 过长拒绝）。
MIN_PASSWORD_LENGTH: Final[int] = 8
MAX_PASSWORD_LENGTH: Final[int] = 128

#: 分隔符（格式串字段分隔；不出现在 base64 字母表中）。
_FIELD_SEPARATOR: Final[str] = "$"

#: 校验时允许的最大 scrypt N（防御性上限：即使库中哈希被写入离谱参数，
#: 也不尝试分配巨量内存——超限即判校验失败）。
_MAX_SCRYPT_N: Final[int] = 2**20


class PasswordPolicyError(ValueError):
    """密码不符合长度策略（创建用户时由 Service 抛出；不含密码内容）。"""


def validate_password_policy(password: str) -> None:
    """长度策略校验（**不**做复杂度规则——避免把规则藏进代码难以调整）。

    Raises:
        PasswordPolicyError: 非字符串 / 空白-only / 长度越界。
    """
    if not isinstance(password, str):
        raise PasswordPolicyError("密码必须是字符串")
    if not password.strip():
        raise PasswordPolicyError("密码不能为空白")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(
            f"密码长度不足（最少 {MIN_PASSWORD_LENGTH} 字符）"
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        raise PasswordPolicyError(
            f"密码长度超限（最多 {MAX_PASSWORD_LENGTH} 字符）"
        )


def hash_password(password: str) -> str:
    """生成 scrypt 哈希串（``scrypt$N$r$p$salt$hash``）。

    Raises:
        PasswordPolicyError: 密码不符合长度策略（**不**进 KDF）。
    """
    validate_password_policy(password)
    salt = secrets.token_bytes(SALT_BYTES)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=KEY_LENGTH,
    )
    return _FIELD_SEPARATOR.join(
        (
            PASSWORD_HASH_PREFIX,
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(derived).decode("ascii"),
        )
    )


def verify_password(password: str, stored_hash: str) -> bool:
    """恒定时间校验密码。

    任何格式异常 / 参数异常 / 算法不匹配一律返回 ``False``（**不**抛异常——
    调用方无法区分"哈希坏"与"密码错"，两者都表现为认证失败）。
    """
    if not isinstance(password, str) or not isinstance(stored_hash, str):
        return False
    parts = stored_hash.split(_FIELD_SEPARATOR)
    if len(parts) != 6:
        return False
    prefix, n_raw, r_raw, p_raw, salt_b64, hash_b64 = parts
    if prefix != PASSWORD_HASH_PREFIX:
        return False
    try:
        n, r, p = int(n_raw), int(r_raw), int(p_raw)
        salt = base64.b64decode(salt_b64, validate=True)
        expected = base64.b64decode(hash_b64, validate=True)
    except (ValueError, TypeError):
        return False
    if n <= 0 or r <= 0 or p <= 0 or not salt or not expected:
        return False
    if n > _MAX_SCRYPT_N:  # 防御性上限：拒绝离谱参数（防资源消耗）
        return False
    try:
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
        )
    except (ValueError, MemoryError):  # 参数越界 / 资源限制 ⇒ 视为校验失败
        return False
    return hmac.compare_digest(derived, expected)


def dummy_verify(password: str) -> None:
    """用户不存在时的**耗时均衡**（执行一次真实 KDF 后丢弃结果）。

    目的：让"用户名不存在"与"密码错误"的响应时间同量级，避免通过计时
    枚举有效用户名。**不**返回任何信息。
    """
    if isinstance(password, str) and password:
        try:
            hashlib.scrypt(
                password.encode("utf-8"),
                salt=b"\x00" * SALT_BYTES,
                n=SCRYPT_N,
                r=SCRYPT_R,
                p=SCRYPT_P,
                dklen=KEY_LENGTH,
            )
        except (ValueError, MemoryError):  # pragma: no cover - 防御性
            return


__all__ = [
    "PASSWORD_HASH_PREFIX",
    "SCRYPT_N",
    "SCRYPT_R",
    "SCRYPT_P",
    "SALT_BYTES",
    "KEY_LENGTH",
    "MIN_PASSWORD_LENGTH",
    "MAX_PASSWORD_LENGTH",
    "PasswordPolicyError",
    "validate_password_policy",
    "hash_password",
    "verify_password",
    "dummy_verify",
]
