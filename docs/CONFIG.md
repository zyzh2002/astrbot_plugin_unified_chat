# 配置说明

WebUI 插件页由 `_conf_schema.json` 驱动。

## 核心开关

- `enable_conversation_enhance` - 对话增强
- `enable_persistent_memory` - 持久记忆
- `enable_adaptive_learning` - 自适应学习
- `rag_agentic` - RAG 自主调用（默认 true）
- `rag_kbs` - 关联知识库（`_special: select_knowledgebase` 多选）
- `embedding_provider_id` / `rerank_provider_id` / `chat_provider_id` - `_special: select_provider`

## 存储

- `data_dir` - 覆盖 `StarTools.get_data_dir()`，默认自动管理。
- 记忆清理：`memory_cleanup_days=30`, `importance_threshold=0.3`，每日 03:00 执行。

## 迁移

更换嵌入模型时 WebUI 提示 `needs_migration`，执行 `/unified_migrate <kb_name>` 后台重建索引。
