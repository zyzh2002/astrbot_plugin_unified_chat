# 配置说明

WebUI 插件页由 `_conf_schema.json` 驱动。

## 核心开关

- `enable_conversation_enhance` - 对话增强
- `enable_persistent_memory` - 持久记忆
- `enable_adaptive_learning` - 自适应学习
- `rag_agentic` - RAG 自主调用（默认 true，唯一模式）
- `rag_kbs` - 关联知识库（`_special: select_knowledgebase` 多选）
- `embedding_provider_id` / `rerank_provider_id` / `chat_provider_id` - `_special: select_provider`
- `memory_kb_name` - 记忆向量库名称（默认 `unified_chat_memories`，由插件自动创建/复用）

## 存储

- `data_dir` - 覆盖 `StarTools.get_data_dir()`，默认自动管理。
- 记忆清理：`memory_cleanup_days=30`, `importance_threshold=0.3`，每日 03:00 执行。
- 未配置 `embedding_provider_id` 时记忆降级为纯 SQLite 模式（关键词检索，无向量索引）。

## 迁移

更换嵌入模型时插件在启动时检测快照不一致，`/unified_status` 显示 `needs_migration=yes`，
执行 `/unified_migrate <kb_name>` 后台全量重建索引（快照分块 → 删除旧文档 → 按当前嵌入模型重传）。
迁移记忆库后 `Memory.kb_doc_id` 链接会清空（SQLite 为事实源）。
