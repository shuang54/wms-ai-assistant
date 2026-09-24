"""Project Knowledge Provider 单元测试（Phase 3.8.4）。

无 DB / 无网络 / 无 LLM。覆盖任务书 §二十一 Provider 部分：

    - Scope DTO：frozen / 非法输入 clear error
    - InMemoryProjectKnowledgeProvider：
        注册 / 获取 / 重复注册 / 未注册 / 类型错误 / 线程安全
    - DefaultProjectKnowledgeProvider：
        每个项目 → 自身 namespace + __global__；
        仅历史项目（vietnam-wms）可见 legacy
    - 默认 Provider 单例
"""
from __future__ import annotations

import threading

import pytest

from backend.app.config import settings
from backend.app.projects.knowledge_provider import (
    GLOBAL_NAMESPACE,
    DefaultProjectKnowledgeProvider,
    InMemoryProjectKnowledgeProvider,
    ProjectKnowledgeScope,
    ProjectKnowledgeScopeError,
    get_default_project_knowledge_provider,
)


SCOPE_A = ProjectKnowledgeScope(project_id="project-a", namespace="project-a")
SCOPE_B = ProjectKnowledgeScope(project_id="project-b", namespace="project-b")


# ============================================================
# DTO
# ============================================================

class TestProjectKnowledgeScope:
    def test_valid_scope_defaults(self) -> None:
        """默认：includes_global=True，includes_legacy=False。"""
        scope = ProjectKnowledgeScope(
            project_id="project-a", namespace="project-a"
        )
        assert scope.project_id == "project-a"
        assert scope.namespace == "project-a"
        assert scope.includes_global is True
        assert scope.includes_legacy is False

    def test_frozen(self) -> None:
        """frozen DTO：注册后不可变。"""
        scope = ProjectKnowledgeScope(
            project_id="p", namespace="p"
        )
        with pytest.raises(Exception):  # noqa: BLE001  FrozenInstanceError
            scope.namespace = "other"  # type: ignore[misc]

    @pytest.mark.parametrize(
        "project_id,namespace",
        [
            ("", "ns"),
            ("   ", "ns"),
            (None, "ns"),  # type: ignore[arg-type]
            (123, "ns"),  # type: ignore[arg-type]
            ("p", ""),
            ("p", "   "),
            ("p", None),  # type: ignore[arg-value]
            ("p", 42),  # type: ignore[arg-value]
        ],
    )
    def test_invalid_inputs(self, project_id, namespace) -> None:
        with pytest.raises(ProjectKnowledgeScopeError):
            ProjectKnowledgeScope(project_id=project_id, namespace=namespace)

    def test_flags_must_be_bool(self) -> None:
        with pytest.raises(ProjectKnowledgeScopeError):
            ProjectKnowledgeScope(
                project_id="p",
                namespace="p",
                includes_global="yes",  # type: ignore[arg-type]
            )
        with pytest.raises(ProjectKnowledgeScopeError):
            ProjectKnowledgeScope(
                project_id="p",
                namespace="p",
                includes_legacy=1,  # type: ignore[arg-type]
            )

    def test_no_sensitive_fields(self) -> None:
        """字段白名单：绝无 handler / engine / password / api_key。"""
        import dataclasses

        names = {f.name for f in dataclasses.fields(ProjectKnowledgeScope)}
        assert names == {
            "project_id",
            "namespace",
            "includes_global",
            "includes_legacy",
        }


# ============================================================
# InMemory 实现
# ============================================================

class TestInMemoryProvider:
    def test_register_and_get(self) -> None:
        provider = InMemoryProjectKnowledgeProvider()
        provider.register("project-a", SCOPE_A)
        provider.register("project-b", SCOPE_B)

        assert provider.get_scope("project-a") == SCOPE_A
        assert provider.get_scope("project-b") == SCOPE_B
        # 确定性：重复解析返回同一实例
        assert provider.get_scope("project-a") is SCOPE_A

    def test_get_unknown_project_clear_error(self) -> None:
        """未注册项目 → clear error，绝不静默回退（§十八-2）。"""
        provider = InMemoryProjectKnowledgeProvider()
        provider.register("project-a", SCOPE_A)

        with pytest.raises(ProjectKnowledgeScopeError, match="project-b"):
            provider.get_scope("project-b")

    def test_duplicate_registration_clear_error(self) -> None:
        provider = InMemoryProjectKnowledgeProvider()
        provider.register("project-a", SCOPE_A)
        with pytest.raises(ProjectKnowledgeScopeError, match="已注册"):
            provider.register("project-a", SCOPE_A)

    def test_register_type_errors(self) -> None:
        provider = InMemoryProjectKnowledgeProvider()
        # scope 类型错误
        with pytest.raises(ProjectKnowledgeScopeError, match="ProjectKnowledgeScope"):
            provider.register("project-a", {"namespace": "project-a"})  # type: ignore[arg-type]
        # project_id 类型错误
        with pytest.raises(ProjectKnowledgeScopeError):
            provider.register("", SCOPE_A)
        with pytest.raises(ProjectKnowledgeScopeError):
            provider.register(123, SCOPE_A)  # type: ignore[arg-type]
        # scope.project_id 与注册 key 不一致（防串项目）
        with pytest.raises(ProjectKnowledgeScopeError, match="不一致"):
            provider.register("project-a", SCOPE_B)

    def test_get_invalid_project_id(self) -> None:
        provider = InMemoryProjectKnowledgeProvider()
        with pytest.raises(ProjectKnowledgeScopeError):
            provider.get_scope("")
        with pytest.raises(ProjectKnowledgeScopeError):
            provider.get_scope(None)  # type: ignore[arg-type]

    def test_unregister(self) -> None:
        provider = InMemoryProjectKnowledgeProvider()
        provider.register("project-a", SCOPE_A)
        provider.unregister("project-a")
        with pytest.raises(ProjectKnowledgeScopeError):
            provider.get_scope("project-a")
        # 未注册再移除 → clear error
        with pytest.raises(ProjectKnowledgeScopeError):
            provider.unregister("project-a")

    def test_list_project_ids(self) -> None:
        provider = InMemoryProjectKnowledgeProvider()
        provider.register("project-b", SCOPE_B)
        provider.register("project-a", SCOPE_A)
        assert provider.list_project_ids() == ("project-a", "project-b")

    def test_thread_safety_register(self) -> None:
        """并发注册不同项目：全部成功，无丢失。"""
        provider = InMemoryProjectKnowledgeProvider()
        n = 50
        errors: list[Exception] = []

        def _register(i: int) -> None:
            try:
                pid = f"project-{i:03d}"
                provider.register(
                    pid, ProjectKnowledgeScope(project_id=pid, namespace=pid)
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=_register, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(provider.list_project_ids()) == n

    def test_thread_safety_duplicate_detection(self) -> None:
        """并发注册同一项目：恰好一次成功，其余 clear error。"""
        provider = InMemoryProjectKnowledgeProvider()
        successes: list[str] = []
        failures: list[str] = []
        lock = threading.Lock()
        barrier = threading.Barrier(8)

        def _register() -> None:
            barrier.wait()
            try:
                provider.register("project-a", SCOPE_A)
                with lock:
                    successes.append("ok")
            except ProjectKnowledgeScopeError:
                with lock:
                    failures.append("dup")

        threads = [threading.Thread(target=_register) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(successes) == 1
        assert len(failures) == 7
        assert provider.get_scope("project-a") == SCOPE_A


# ============================================================
# Default 实现（vietnam-wms legacy 兼容，显式表达）
# ============================================================

class TestDefaultProvider:
    def test_normal_project_own_namespace_no_legacy(self) -> None:
        """普通项目 → 自身 namespace + __global__，绝不带 legacy。"""
        provider = DefaultProjectKnowledgeProvider(
            legacy_project_id="vietnam-wms"
        )
        scope = provider.get_scope("project-a")
        assert scope.project_id == "project-a"
        assert scope.namespace == "project-a"
        assert scope.includes_global is True
        # 关键：普通项目不能因 legacy 兼容看到历史（跨项目泄漏）
        assert scope.includes_legacy is False

    def test_legacy_project_sees_legacy(self) -> None:
        """历史项目（vietnam-wms）→ 额外可见 legacy 知识。"""
        provider = DefaultProjectKnowledgeProvider(
            legacy_project_id="vietnam-wms"
        )
        scope = provider.get_scope("vietnam-wms")
        assert scope.namespace == "vietnam-wms"
        assert scope.includes_global is True
        assert scope.includes_legacy is True

    def test_default_legacy_project_from_settings(self) -> None:
        """默认 legacy 项目 = settings.project.project_id（vietnam-wms）。"""
        provider = DefaultProjectKnowledgeProvider()
        scope = provider.get_scope(settings.project.project_id)
        assert scope.includes_legacy is True
        # 其它项目不受影响
        assert provider.get_scope("project-a").includes_legacy is False

    def test_scope_never_points_to_other_project(self) -> None:
        """任何 project_id → namespace == 自身（绝不指向他人）。"""
        provider = DefaultProjectKnowledgeProvider(
            legacy_project_id="vietnam-wms"
        )
        for pid in ("project-a", "project-b", "vietnam-wms", "x"):
            assert provider.get_scope(pid).namespace == pid

    def test_invalid_project_id(self) -> None:
        provider = DefaultProjectKnowledgeProvider()
        with pytest.raises(ProjectKnowledgeScopeError):
            provider.get_scope("")
        with pytest.raises(ProjectKnowledgeScopeError):
            provider.get_scope(None)  # type: ignore[arg-type]

    def test_global_namespace_constant(self) -> None:
        """global 是显式命名空间，不是"不加过滤"。"""
        assert GLOBAL_NAMESPACE == "__global__"


# ============================================================
# 默认 Provider 单例
# ============================================================

class TestDefaultSingleton:
    def test_singleton(self) -> None:
        assert (
            get_default_project_knowledge_provider()
            is get_default_project_knowledge_provider()
        )


__all__ = [
    "TestProjectKnowledgeScope",
    "TestInMemoryProvider",
    "TestDefaultProvider",
    "TestDefaultSingleton",
]
