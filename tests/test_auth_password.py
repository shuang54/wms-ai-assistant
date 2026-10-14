"""密码哈希单元测试（Phase 7 Recovery M1；离线；零 DB / 零网络 / 零 LLM）。

覆盖任务书测试清单中的"密码哈希与验证"与"敏感信息不进入日志"相关原语：
    * 哈希格式自描述（scrypt$N$r$p$salt$hash）；
    * 正确 / 错误密码校验；
    * 损坏哈希 / 异算法哈希 ⇒ False（不抛异常——认证失败统一语义）；
    * 长度策略（过短 / 过长 / 空白 / 非字符串）；
    * 同一密码两次哈希不同（随机盐）；
    * 耗时均衡函数不抛错（用户不存在路径）。
"""
from __future__ import annotations

import pytest

from backend.app.auth.password import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    PASSWORD_HASH_PREFIX,
    PasswordPolicyError,
    dummy_verify,
    hash_password,
    validate_password_policy,
    verify_password,
)

__all__ = [
    "test_hash_format_is_self_describing",
    "test_verify_accepts_correct_password",
    "test_verify_rejects_wrong_password",
    "test_verify_rejects_malformed_hash",
    "test_verify_rejects_foreign_algorithm_hash",
    "test_hash_is_salted_random_per_call",
    "test_password_policy_rejects_too_short",
    "test_password_policy_rejects_too_long",
    "test_password_policy_rejects_blank",
    "test_password_policy_rejects_non_string",
    "test_dummy_verify_runs_without_error",
    "test_hash_does_not_contain_plaintext",
]


def test_hash_format_is_self_describing() -> None:
    stored = hash_password("a-valid-password-1")
    parts = stored.split("$")
    assert parts[0] == PASSWORD_HASH_PREFIX
    assert len(parts) == 6
    # 参数段为十进制整数
    assert all(part.isdigit() for part in parts[1:4])


def test_verify_accepts_correct_password() -> None:
    stored = hash_password("a-valid-password-1")
    assert verify_password("a-valid-password-1", stored) is True


def test_verify_rejects_wrong_password() -> None:
    stored = hash_password("a-valid-password-1")
    assert verify_password("a-valid-password-2", stored) is False


def test_verify_rejects_malformed_hash() -> None:
    assert verify_password("whatever", "not-a-hash") is False
    assert verify_password("whatever", "") is False
    assert verify_password("whatever", "scrypt$1$2$3$notbase64$alsobad") is False


def test_verify_rejects_foreign_algorithm_hash() -> None:
    # 异算法前缀（例如未来的 pbkdf2 串）不得被误判为通过
    foreign = "pbkdf2$1000$c2FsdA==$aGFzaA=="
    assert verify_password("whatever", foreign) is False


def test_hash_is_salted_random_per_call() -> None:
    first = hash_password("same-password-1")
    second = hash_password("same-password-1")
    assert first != second  # 随机盐 ⇒ 同密码不同哈希
    assert verify_password("same-password-1", first)
    assert verify_password("same-password-1", second)


def test_password_policy_rejects_too_short() -> None:
    with pytest.raises(PasswordPolicyError):
        validate_password_policy("x" * (MIN_PASSWORD_LENGTH - 1))


def test_password_policy_rejects_too_long() -> None:
    with pytest.raises(PasswordPolicyError):
        validate_password_policy("x" * (MAX_PASSWORD_LENGTH + 1))


def test_password_policy_rejects_blank() -> None:
    with pytest.raises(PasswordPolicyError):
        validate_password_policy("   ")
    with pytest.raises(PasswordPolicyError):
        validate_password_policy("")


def test_password_policy_rejects_non_string() -> None:
    with pytest.raises(PasswordPolicyError):
        validate_password_policy(None)  # type: ignore[arg-type]


def test_dummy_verify_runs_without_error() -> None:
    # 用户不存在路径的耗时均衡：必须安静完成（不抛错 / 不返回信息）
    dummy_verify("some-password-value")
    dummy_verify("")
    dummy_verify("x" * 200)


def test_hash_does_not_contain_plaintext() -> None:
    password = "super-secret-value-123"
    stored = hash_password(password)
    assert password not in stored
