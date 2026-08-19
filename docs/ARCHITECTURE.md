# 架构

## 目标

统一对话增强、持久记忆、自适应学习为单一插件，对外呈现一体化智能体能力。

## 模块

- `main.py:UnifiedChatPlugin(Star)` - 唯一入口，声明所有 `filter`，委托至 `core.PluginLifecycle`。
- `core/pipeline.py` - 统一消息流水线，非阻塞 `asyncio.create_task`。
- `core/hooks.py` - `on_llm_request` 统一注入（Agentic 时注入 `KnowledgeBaseQueryTool`）。
- `services/{chat,memory,learning,rag}.py` - 三域与 RAG 封装。
- `storage/` - `SQLModel` + `aiosqlite`，`data/plugin_data/unified_chat/` 持久化。
- `native/` - `try: import _native except: fallback` 双路径。
- `rust/` - `pyo3` 扩展，激进 `release` 配置。

## 数据流

`event_message → pipeline (filter/dedup/affection/memory/learning) → db → on_llm_request (rag/memory/social) → LLM`。

## 约束

- 单一 `Star` 实例，无全局单例，状态经 `Context` 注入。
- `__init__` 不抛异常，初始化延至 `initialize()`。
- 单条消息异常仅 `logger.error`，不中断插件。
