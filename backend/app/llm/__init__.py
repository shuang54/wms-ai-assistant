"""LLM 抽象层。

封装不同大模型 Provider（OpenAI 兼容、Qwen、DeepSeek、Ollama 等）。
Phase 1 只提供 Mock 实现，真实 Provider 在 Phase 2 引入。
详见 docs/architecture.md §8。
"""
