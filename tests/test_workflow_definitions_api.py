"""Phase 7 Step 7 — Workflow 定义目录 API 契约测试（离线；零 DB）。

覆盖：

```text
1  正常目录 200：精确字段集（顶层 / 元素 / 节点）+ 与生产目录一致
2  认证：缺少 / 错误凭据 ⇒ 401；未配置 / 空白作用域 ⇒ 503（fail closed）
3  只读面形：仅 GET；未知写方法 ⇒ 405
4  客户端无法影响目录：未知 query 参数（含 project_id）被忽略；响应与项目无关
5  不暴露定义内部：模板 / 参数映射 / 审批文案 / 工具名 / 边 均不出现在响应
6  Composition Root 注入：目录来自注册表（自定义 / 空表均正确）
7  失败映射：注册表不可用 ⇒ 500 固定文案（不暴露异常正文）
```

注入方式沿用既有范式：monkeypatch 本模块 Composition Root
（``get_workflow_definition_registry``）+ ``workflow_read_auth`` 的配置
+ ``TestClient(app)``。
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.api import workflow_definitions as catalog_api
from backend.app.api import workflow_read_auth as auth_module
from backend.app.config import WorkflowReadSettings
from backend.app.dto.workflow_read_api import (
    WORKFLOW_DEFINITION_CATALOG_RESPONSE_FIELDS,
    WORKFLOW_DEFINITION_NODE_RESPONSE_FIELDS,
    WORKFLOW_DEFINITION_RESPONSE_FIELDS,
)
from backend.app.main import app
from backend.app.workflow.catalog import (
    DEFAULT_WORKFLOW_NAMES,
    WORKFLOW_VERSION_V1,
)
from backend.app.workflow.definition import (
    NODE_TYPE_APPROVAL,
    NODE_TYPE_TOOL,
    WorkflowDefinition,
    WorkflowNode,
    WorkflowRegistry,
)

__all__ = [
    "TestCatalogEndpoint",
    "TestCatalogAuth",
    "TestCatalogSurface",
    "TestCatalogBoundaries",
]

#: 测试专用凭据（**非**生产凭据；仅存在于测试进程内 monkeypatch 配置）。
_TEST_API_KEY = "p7s7-catalog-api-key-NOT-A-PRODUCTION-CREDENTIAL"
_AUTH_PROJECT = "proj-p7s7-catalog"
_HEADERS = {"X-API-Key": _TEST_API_KEY}

_PATH = "/api/workflow-definitions"

#: 定义内部（**不得**出现在响应中的字面量）。
_INTERNAL_LITERALS = (
    "请核对库存查询结果",  # inventory_check 审批文案
    "请确认 AI 回答内容",  # answer_review 审批文案
    "请回答以下问题",  # AI 模板前缀
    "question_template",
    "arguments_map",
    "approval_prompt",
    "next_node_id",
    "retry_policy",
    "get_inventory",  # TOOL 目标名（节点类型可见，Tool 名不可见）
)


def _custom_registry() -> WorkflowRegistry:
    registry = WorkflowRegistry()
    registry.register(
        WorkflowDefinition(
            name="tmp_approval_only",
            version="v1",
            start_node_id="gate",
            nodes=(
                WorkflowNode(
                    node_id="gate",
                    node_type=NODE_TYPE_APPROVAL,
                    approval_prompt="请审批",
                ),
            ),
        )
    )
    return registry


def _client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    registry: WorkflowRegistry | None = None,
    settings_override: WorkflowReadSettings | None = None,
) -> TestClient:
    monkeypatch.setattr(
        auth_module,
        "get_workflow_read_settings",
        lambda: settings_override
        or WorkflowReadSettings(
            api_key=_TEST_API_KEY, api_project_id=_AUTH_PROJECT
        ),
    )
    if registry is not None:
        monkeypatch.setattr(
            catalog_api, "get_workflow_definition_registry", lambda: registry
        )
    return TestClient(app)


# ============================================================
# 1：正常目录
# ============================================================


class TestCatalogEndpoint:
    def test_production_catalog_200_exact_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(monkeypatch)
        response = client.get(_PATH, headers=_HEADERS)
        assert response.status_code == 200
        payload = response.json()
        assert tuple(payload.keys()) == WORKFLOW_DEFINITION_CATALOG_RESPONSE_FIELDS
        assert [item["name"] for item in payload["items"]] == list(
            DEFAULT_WORKFLOW_NAMES
        )
        for item in payload["items"]:
            assert tuple(item.keys()) == WORKFLOW_DEFINITION_RESPONSE_FIELDS
            assert item["version"] == WORKFLOW_VERSION_V1
            assert item["start_node_id"]
            assert item["input_fields"], item
            for node in item["nodes"]:
                assert tuple(node.keys()) == WORKFLOW_DEFINITION_NODE_RESPONSE_FIELDS
                assert node["node_id"]
                assert node["node_type"] in {"AI", "TOOL", "APPROVAL", "CONDITION"}

    def test_catalog_matches_command_registry_names(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """目录与命令 API 使用同一注册表 ⇒ 出现的 name 必然可 start 解析。"""
        from backend.app.workflow_runtime.command_service import (
            get_workflow_registry,
        )

        client = _client(monkeypatch)
        payload = client.get(_PATH, headers=_HEADERS).json()
        registry = get_workflow_registry()
        for item in payload["items"]:
            assert item["name"] in registry
            definition = registry.resolve_current(item["name"])
            assert definition.version == item["version"]
            assert definition.start_node_id == item["start_node_id"]
            assert [n.node_id for n in definition.nodes] == [
                n["node_id"] for n in item["nodes"]
            ]


# ============================================================
# 2：认证
# ============================================================


class TestCatalogAuth:
    def test_missing_key_rejected_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client(monkeypatch)
        response = client.get(_PATH)
        assert response.status_code == 401
        assert "缺少或无效的 API Key" in response.json()["detail"]

    def test_wrong_key_rejected_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client(monkeypatch)
        response = client.get(_PATH, headers={"X-API-Key": "wrong-key"})
        assert response.status_code == 401

    def test_unconfigured_fails_closed_503(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(
            monkeypatch, settings_override=WorkflowReadSettings()
        )
        response = client.get(_PATH, headers=_HEADERS)
        assert response.status_code == 503
        assert "读取认证未配置" in response.json()["detail"]

    def test_blank_project_fails_closed_503(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(
            monkeypatch,
            settings_override=WorkflowReadSettings(
                api_key=_TEST_API_KEY, api_project_id="   "
            ),
        )
        assert client.get(_PATH, headers=_HEADERS).status_code == 503

    def test_unauthenticated_never_reaches_registry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """认证已配置但缺凭据 ⇒ 401，且**注册表读取从未发生**。"""

        def _must_not_run() -> Any:
            raise AssertionError("registry must not be read before auth")

        client = _client(monkeypatch)  # 认证已配置（否则先 503）
        monkeypatch.setattr(
            catalog_api, "get_workflow_definition_registry", _must_not_run
        )
        assert client.get(_PATH).status_code == 401


# ============================================================
# 3 / 4 / 5：面形、输入无效性、不泄露内部
# ============================================================


class TestCatalogSurface:
    def test_write_methods_rejected_405(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(monkeypatch)
        for method in ("post", "put", "patch", "delete"):
            response = getattr(client, method)(_PATH, headers=_HEADERS)
            assert response.status_code == 405, method

    def test_only_get_in_openapi_with_security(self) -> None:
        schema = app.openapi()
        operation = schema["paths"][_PATH]
        assert set(operation) == {"get"}
        requirement = operation["get"].get("security")
        assert requirement, "未声明 security"

    def test_unknown_query_params_ignored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """客户端无法影响目录：未知参数（含 project_id / name 过滤）被忽略。"""
        client = _client(monkeypatch)
        baseline = client.get(_PATH, headers=_HEADERS).json()
        spoofed = client.get(
            _PATH,
            headers=_HEADERS,
            params={"project_id": "proj-OTHER", "name": "tmp_approval_only"},
        ).json()
        assert spoofed == baseline

    def test_internal_definition_details_never_exposed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(monkeypatch)
        response = client.get(_PATH, headers=_HEADERS)
        assert response.status_code == 200
        text = response.text
        for literal in _INTERNAL_LITERALS:
            assert literal not in text, literal
        assert _TEST_API_KEY not in text


# ============================================================
# 6 / 7：Composition Root 与失败映射
# ============================================================


class TestCatalogBoundaries:
    def test_custom_registry_is_served(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(monkeypatch, registry=_custom_registry())
        payload = client.get(_PATH, headers=_HEADERS).json()
        assert [item["name"] for item in payload["items"]] == ["tmp_approval_only"]
        assert payload["items"][0]["nodes"] == [
            {"node_id": "gate", "node_type": "APPROVAL"}
        ]
        assert payload["items"][0]["input_fields"] == []

    def test_empty_registry_returns_empty_items(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(monkeypatch, registry=WorkflowRegistry())
        response = client.get(_PATH, headers=_HEADERS)
        assert response.status_code == 200
        assert response.json() == {"items": []}

    def test_registry_failure_is_500_fixed_detail(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom() -> Any:
            raise RuntimeError("internal-detail-DO-NOT-EXPOSE")

        client = _client(monkeypatch)
        monkeypatch.setattr(
            catalog_api, "get_workflow_definition_registry", _boom
        )
        response = client.get(_PATH, headers=_HEADERS)
        assert response.status_code == 500
        assert response.json()["detail"] == "Workflow 定义目录不可用"
        assert "internal-detail" not in response.text

    def test_tool_node_type_visible_but_tool_name_hidden(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """节点**类型**（TOOL）可见；TOOL 目标名（get_inventory）不可见。"""
        client = _client(monkeypatch)
        payload = client.get(_PATH, headers=_HEADERS).json()
        types = {
            node["node_type"]
            for item in payload["items"]
            for node in item["nodes"]
        }
        assert NODE_TYPE_TOOL in types
        assert "get_inventory" not in str(payload)
