# 架构

## 目标

统一对话增强、持久记忆、自适应学习为单一插件，对外呈现一体化智能体能力。

## 模块

- `main.py:UnifiedChatPlugin(Star)` - 唯一入口，声明所有 `filter`，委托至 `core.PluginLifecycle`。
- `core/pipeline.py` - 统一消息流水线，非阻塞 `asyncio.create_task`（filter/dedup/record → 后台 memory/learning）。
- `core/hooks.py` - `on_llm_request` 统一注入（Agentic RAG 工具、社交上下文、记忆检索）。
- `core/cron.py` - 每日 03:00：低重要度记忆清理、消息/学习日志留存清理、FTS 索引调和、内存状态清扫。
- `services/{chat,memory,learning,rag,migration}.py` - 三域 + RAG 封装 + KB 索引重建。
- `storage/` - `SQLModel` + `aiosqlite`，`data/plugin_data/astrbot_plugin_unified_chat/` 持久化；repo/kv 薄封装。
- `native/` - `try: import _native except: fallback` 双路径（chunk/score/hash_dedup）。
- `rust/` - `pyo3` 扩展，激进 `release` 配置。

## 数据流

`event_message → pipeline (filter/dedup/affection/memory/learning) → db → on_llm_request (rag/memory/social) → LLM`。

记忆：消息评分 → SQLite `Memory`；高重要度上传至插件自建知识库（复用 FAISS+FTS5+RRF+Rerank）；检索优先走 KB、降级 SQLite LIKE。
学习：全量消息 filter(轻规则) → refine(LLM via chat_provider_id) → reinforce(Memory+LearningLog)；无 provider 时仅落 MessageRecord。

## 约束

- 单一 `Star` 实例，无全局单例，状态经 `Context` 注入。
- `__init__` 不抛异常，初始化延至 `initialize()`。
- 单条消息异常仅 `logger.error`，不中断插件。
- 时间约定：SQLite DATETIME 存 aware-UTC 墙钟字符串，读回为 naive；
  所有比较统一使用 `datetime.now(UTC)`，`.timestamp()` 前必须补回 UTC 时区，
  避免主机时区偏移。
