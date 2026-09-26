"""LLM 抽象层。

封装不同大模型 Provider（OpenAI 兼容、Qwen、DeepSeek、Ollama 等）。

Phase 3.10.1 起的抽象边界：

    AI Core
        ↓
    LLMProvider（provider.py，Protocol —— AI Core 依赖的抽象）
        ↓
    DeepSeekProvider（deepseek_provider.py，delegation）
        ↓
    OpenAICompatibleClient（client.py，现有 OpenAI-compatible Client）

``client.LLMClient`` 保留为 ``LLMProvider`` 的向后兼容别名。
详见 docs/architecture.md §8。
"""
