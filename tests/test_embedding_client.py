"""Embedding Client 单元测试（Phase 3.5.1）。

通过 httpx.MockTransport 注入 HTTP 响应，**不发起真实网络、不产生 API 费用**。

覆盖：
    - 配置：正常 / 缺 Key / 缺 Model / 缺 Base URL / 非法 Dimension / 非法 Timeout
    - 输入：空字符串 / 纯空白 / 非 str / 正常中文（拒绝时不发 HTTP）
    - API：200 / 401 / 429 / 500 / timeout / 网络异常
    - 响应：缺 data / data 空 / 缺 embedding / 类型错误 / 为空 / 非 JSON / 维度不匹配
    - 安全：异常与日志均不包含 API Key
    - 工厂 / API Key 回退链
"""
from __future__ import annotations

import json
import logging

import httpx
import pytest

import backend.app.embedding.client as embedding_client
from backend.app.config import EmbeddingSettings
from backend.app.embedding import (
    EmbeddingAPIError,
    EmbeddingClient,
    EmbeddingConfigurationError,
    EmbeddingDimensionError,
    EmbeddingError,
    EmbeddingInputError,
    EmbeddingResponseError,
    OpenAICompatibleEmbeddingClient,
    create_embedding_client,
    get_default_embedding_client,
    reset_default_embedding_client,
)


# ============================================================
# Helpers
# ============================================================

def _ok_response(vector: list[float]) -> httpx.Response:
    """构造 OpenAI Embeddings 兼容的成功响应。"""
    return httpx.Response(
        200,
        json={
            "data": [{"embedding": vector, "index": 0}],
            "model": "test-model",
            "usage": {"prompt_tokens": 5, "total_tokens": 5},
        },
    )


def _make_client(handler, *, dimension: int = 4) -> OpenAICompatibleEmbeddingClient:
    """用 httpx.MockTransport 构造 Client，避免真实网络。"""
    return OpenAICompatibleEmbeddingClient(
        api_key="test-key",
        base_url="https://api.example.com/v1",
        model="test-model",
        dimension=dimension,
        timeout=10.0,
        transport=httpx.MockTransport(handler),
    )


# ============================================================
# 异常体系结构
# ============================================================

class TestExceptionHierarchy:
    def test_all_subclass_embedding_error(self) -> None:
        for exc in (
            EmbeddingConfigurationError,
            EmbeddingInputError,
            EmbeddingAPIError,
            EmbeddingResponseError,
            EmbeddingDimensionError,
        ):
            assert issubclass(exc, EmbeddingError)


# ============================================================
# 配置校验（构造期）
# ============================================================

class TestConfiguration:
    def test_valid_configuration_constructs(self) -> None:
        client = OpenAICompatibleEmbeddingClient(
            api_key="k", base_url="https://x", model="m", dimension=1024,
        )
        assert isinstance(client, EmbeddingClient)

    def test_missing_api_key_raises(self) -> None:
        with pytest.raises(EmbeddingConfigurationError) as ei:
            OpenAICompatibleEmbeddingClient(api_key="", base_url="https://x", model="m", dimension=4)
        assert "EMBEDDING_API_KEY" in str(ei.value)

    def test_missing_model_raises(self) -> None:
        with pytest.raises(EmbeddingConfigurationError) as ei:
            OpenAICompatibleEmbeddingClient(api_key="k", base_url="https://x", model="", dimension=4)
        assert "EMBEDDING_MODEL" in str(ei.value)

    def test_missing_base_url_raises(self) -> None:
        with pytest.raises(EmbeddingConfigurationError) as ei:
            OpenAICompatibleEmbeddingClient(api_key="k", base_url="", model="m", dimension=4)
        assert "EMBEDDING_BASE_URL" in str(ei.value)

    @pytest.mark.parametrize("dimension", [0, -1])
    def test_invalid_dimension_raises(self, dimension: int) -> None:
        with pytest.raises(EmbeddingConfigurationError) as ei:
            OpenAICompatibleEmbeddingClient(
                api_key="k", base_url="https://x", model="m", dimension=dimension,
            )
        assert "EMBEDDING_DIMENSION" in str(ei.value)

    def test_non_int_dimension_raises(self) -> None:
        with pytest.raises(EmbeddingConfigurationError):
            OpenAICompatibleEmbeddingClient(
                api_key="k", base_url="https://x", model="m",  # type: ignore[arg-type]
                dimension="1536",
            )

    @pytest.mark.parametrize("timeout", [0, -1])
    def test_invalid_timeout_raises(self, timeout) -> None:
        with pytest.raises(EmbeddingConfigurationError) as ei:
            OpenAICompatibleEmbeddingClient(
                api_key="k", base_url="https://x", model="m",
                dimension=4, timeout=timeout,
            )
        assert "EMBEDDING_TIMEOUT" in str(ei.value)


# ============================================================
# 输入校验（不发送 HTTP）
# ============================================================

class TestInputValidation:
    async def test_empty_string_rejected_without_http_call(self) -> None:
        called = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            called["count"] += 1
            return _ok_response([0.1] * 4)

        client = _make_client(handler)
        with pytest.raises(EmbeddingInputError):
            await client.embed("")
        assert called["count"] == 0

    @pytest.mark.parametrize("text", ["   ", "\n\n", " \t \n "])
    async def test_whitespace_only_rejected(self, text: str) -> None:
        client = _make_client(lambda request: _ok_response([0.1] * 4))
        with pytest.raises(EmbeddingInputError):
            await client.embed(text)

    async def test_non_string_input_rejected(self) -> None:
        client = _make_client(lambda request: _ok_response([0.1] * 4))
        with pytest.raises(EmbeddingInputError):
            await client.embed(123)  # type: ignore[arg-type]

    async def test_long_text_is_not_truncated(self) -> None:
        """超长输入原样发送，不做本地截断（Chunking 已负责切分）。"""
        captured: dict = {}
        long_text = "长文本" * 1000  # 3000 chars

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return _ok_response([0.1] * 4)

        client = _make_client(handler)
        vector = await client.embed(long_text)
        assert vector == [0.1] * 4
        assert captured["body"]["input"] == long_text


# ============================================================
# 成功路径 + 请求正确性
# ============================================================

class TestSuccessPath:
    async def test_embed_returns_validated_vector(self) -> None:
        client = _make_client(lambda request: _ok_response([0.5, -0.5, 1.0, 2.0]))
        vector = await client.embed("采购入库需要先确认采购订单。")
        assert vector == [0.5, -0.5, 1.0, 2.0]
        assert all(isinstance(x, float) for x in vector)

    async def test_request_url_headers_and_body_correct(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["headers"] = dict(request.headers)
            captured["body"] = json.loads(request.content)
            return _ok_response([0.1] * 4)

        client = _make_client(handler)
        await client.embed("你好")

        assert captured["url"].endswith("/embeddings")
        assert "https://api.example.com/v1/embeddings" == captured["url"]
        assert captured["headers"]["authorization"] == "Bearer test-key"
        assert captured["headers"]["content-type"] == "application/json"
        assert captured["body"]["model"] == "test-model"
        assert captured["body"]["input"] == "你好"

    async def test_base_url_trailing_slash_normalized(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return _ok_response([0.1] * 4)

        client = OpenAICompatibleEmbeddingClient(
            api_key="k", base_url="https://api.example.com/v1/",
            model="m", dimension=4, timeout=10.0,
            transport=httpx.MockTransport(handler),
        )
        await client.embed("x")
        assert captured["url"] == "https://api.example.com/v1/embeddings"
        assert "//embeddings" not in captured["url"]


# ============================================================
# API 异常路径
# ============================================================

class TestAPIErrors:
    @pytest.mark.parametrize("status", [401, 429, 500])
    async def test_http_error_raises_api_error(self, status: int) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, text=f"http error {status}")

        client = _make_client(handler)
        with pytest.raises(EmbeddingAPIError) as ei:
            await client.embed("x")
        assert str(status) in str(ei.value)

    async def test_timeout_raises_api_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("read timeout")

        client = _make_client(handler)
        with pytest.raises(EmbeddingAPIError) as ei:
            await client.embed("x")
        assert "超时" in str(ei.value) or "timeout" in str(ei.value).lower()

    async def test_connect_error_raises_api_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        client = _make_client(handler)
        with pytest.raises(EmbeddingAPIError) as ei:
            await client.embed("x")
        assert "无法连接" in str(ei.value) or "connection refused" in str(ei.value)

    async def test_non_2xx_body_truncated_in_exception(self) -> None:
        """非 2xx 响应体过长时异常信息截断，避免日志爆炸。"""
        long_body = "x" * 5000

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text=long_body)

        client = _make_client(handler)
        with pytest.raises(EmbeddingAPIError) as ei:
            await client.embed("x")
        assert len(str(ei.value)) < 500


# ============================================================
# 响应结构异常
# ============================================================

class TestResponseErrors:
    async def test_non_json_response_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>not json</html>")

        client = _make_client(handler)
        with pytest.raises(EmbeddingResponseError) as ei:
            await client.embed("x")
        assert "JSON" in str(ei.value)

    async def test_missing_data_field_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"model": "m", "usage": {}})

        client = _make_client(handler)
        with pytest.raises(EmbeddingResponseError) as ei:
            await client.embed("x")
        assert "data" in str(ei.value)

    async def test_empty_data_list_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": []})

        client = _make_client(handler)
        with pytest.raises(EmbeddingResponseError):
            await client.embed("x")

    async def test_missing_embedding_field_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [{"index": 0}]})

        client = _make_client(handler)
        with pytest.raises(EmbeddingResponseError) as ei:
            await client.embed("x")
        assert "embedding" in str(ei.value)

    async def test_non_list_embedding_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [{"embedding": "not-a-list"}]})

        client = _make_client(handler)
        with pytest.raises(EmbeddingResponseError) as ei:
            await client.embed("x")
        assert "list" in str(ei.value)

    async def test_empty_embedding_list_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [{"embedding": []}]})

        client = _make_client(handler)
        with pytest.raises(EmbeddingResponseError):
            await client.embed("x")

    async def test_non_numeric_embedding_element_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"data": [{"embedding": [0.1, "oops", 0.3, 0.4]}]},
            )

        client = _make_client(handler)
        with pytest.raises(EmbeddingResponseError) as ei:
            await client.embed("x")
        assert "数值" in str(ei.value)


# ============================================================
# 维度校验
# ============================================================

class TestDimensionValidation:
    async def test_dimension_mismatch_raises(self) -> None:
        """模型返回 3 维但配置为 4 → EmbeddingDimensionError，不补 0 / 不截断。"""
        client = _make_client(lambda request: _ok_response([0.1, 0.2, 0.3]), dimension=4)
        with pytest.raises(EmbeddingDimensionError) as ei:
            await client.embed("x")
        msg = str(ei.value)
        assert "3" in msg and "4" in msg

    async def test_dimension_match_passes(self) -> None:
        client = _make_client(lambda request: _ok_response([0.1, 0.2, 0.3, 0.4]), dimension=4)
        vector = await client.embed("x")
        assert len(vector) == 4

    async def test_no_padding_no_truncation(self) -> None:
        """返回更长向量时也不截断到配置维度。"""
        client = _make_client(
            lambda request: _ok_response([0.1, 0.2, 0.3, 0.4, 0.5]), dimension=4,
        )
        with pytest.raises(EmbeddingDimensionError):
            await client.embed("x")

    async def test_dimension_1024_success(self) -> None:
        """Phase 3.5.1.7：BAAI/bge-m3 实际维度 1024 → 校验通过。"""
        client = _make_client(
            lambda request: _ok_response([0.1] * 1024), dimension=1024,
        )
        vector = await client.embed("采购入库需要先确认采购订单。")
        assert len(vector) == 1024
        assert all(isinstance(x, float) for x in vector)

    async def test_dimension_1536_rejected(self) -> None:
        """Phase 3.5.1.7：旧 1536 维向量在新配置（1024）下必须被拒绝。"""
        client = _make_client(
            lambda request: _ok_response([0.1] * 1536), dimension=1024,
        )
        with pytest.raises(EmbeddingDimensionError) as ei:
            await client.embed("x")
        msg = str(ei.value)
        assert "1536" in msg and "1024" in msg

    async def test_custom_dimension_512_still_supported(self) -> None:
        """维度校验仍是配置驱动：自定义 512 依旧可用（并非全局硬编码 1024）。"""
        client = _make_client(
            lambda request: _ok_response([0.1] * 512), dimension=512,
        )
        vector = await client.embed("x")
        assert len(vector) == 512


# ============================================================
# 安全：API Key 不泄漏
# ============================================================

class TestApiKeySecurity:
    async def test_exception_does_not_leak_api_key(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, text="invalid key")

        client = _make_client(handler)
        with pytest.raises(EmbeddingAPIError) as ei:
            await client.embed("x")
        assert "test-key" not in str(ei.value)

    async def test_logs_do_not_contain_api_key(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _ok_response([0.1] * 4)

        with caplog.at_level(logging.DEBUG, logger="backend.app.embedding.client"):
            client = _make_client(handler)
            await client.embed("测试")

        for record in caplog.records:
            assert "test-key" not in record.getMessage()
            assert all("test-key" not in str(v) for v in record.__dict__.get("extra", {}).values())

    async def test_error_logs_do_not_contain_api_key(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="server error")

        with caplog.at_level(logging.DEBUG, logger="backend.app.embedding.client"):
            client = _make_client(handler)
            with pytest.raises(EmbeddingAPIError):
                await client.embed("测试")

        for record in caplog.records:
            assert "test-key" not in record.getMessage()
            assert all("test-key" not in str(v) for v in record.__dict__.get("extra", {}).values())


# ============================================================
# 工厂 / 配置回退链
# ============================================================

class TestFactory:
    def test_create_with_full_settings(self) -> None:
        s = EmbeddingSettings(
            provider="test", model="m", base_url="https://x",
            api_key="k", dimension=4, timeout=5.0,
        )
        client = create_embedding_client(s)
        assert isinstance(client, OpenAICompatibleEmbeddingClient)

    def test_create_with_missing_model_raises(self) -> None:
        s = EmbeddingSettings(provider="t", model="", base_url="https://x", api_key="k", dimension=4)
        with pytest.raises(EmbeddingConfigurationError):
            create_embedding_client(s)

    def test_api_key_falls_back_to_llm_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EMBEDDING_MODEL", "m")
        monkeypatch.setenv("EMBEDDING_BASE_URL", "https://x")
        monkeypatch.setenv("EMBEDDING_API_KEY", "")
        monkeypatch.setenv("LLM_API_KEY", "shared-key")
        s = EmbeddingSettings()
        assert s.api_key == "shared-key"

    def test_api_key_prefers_embedding_specific(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EMBEDDING_API_KEY", "emb-key")
        monkeypatch.setenv("LLM_API_KEY", "llm-key")
        s = EmbeddingSettings()
        assert s.api_key == "emb-key"

    def test_default_dimension_is_1024(self) -> None:
        assert EmbeddingSettings().dimension == 1024

    def test_default_timeout_is_60(self) -> None:
        assert EmbeddingSettings().timeout == 60.0

    def test_default_client_singleton_and_reset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 强制不完整配置（Settings 为 frozen dataclass，不可直接 setattr 字段，
        # 故替换 client 模块内引用的 settings 对象），验证 get_default_embedding_client()
        # 显式抛错，不静默回退；随后补全配置，验证单例缓存与 reset 语义。
        from types import SimpleNamespace

        monkeypatch.setattr(
            embedding_client,
            "settings",
            SimpleNamespace(embedding=EmbeddingSettings(api_key="", base_url="", model="")),
        )
        reset_default_embedding_client()
        try:
            with pytest.raises(EmbeddingConfigurationError):
                get_default_embedding_client()

            monkeypatch.setattr(
                embedding_client,
                "settings",
                SimpleNamespace(
                    embedding=EmbeddingSettings(
                        api_key="k", base_url="https://x", model="m", dimension=4,
                    ),
                ),
            )
            c1 = get_default_embedding_client()
            assert get_default_embedding_client() is c1
            reset_default_embedding_client()
            assert get_default_embedding_client() is not c1
        finally:
            reset_default_embedding_client()
