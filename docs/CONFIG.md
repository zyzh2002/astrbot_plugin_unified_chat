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

## 原生加速（跨平台）

- `native_autodownload` - 启动时自动下载匹配平台的原生库（默认 true）。
- 加载优先级：内置 → `data/plugin_data/.../native/` 缓存 → 纯 Python 回退；
  下载带 sha256 校验，失败静默降级。
- 发布产物：GitHub Release 按 tag 提供 linux-x86_64 / linux-aarch64 /
  windows-x86_64 / macos-arm64 四平台 abi3 wheel。

## 记忆深化

- 记忆原子：类型自动分类（EPISODIC/FACTUAL/RELATIONAL/PREFERENCE/PLANNED），
  按类型 TTL（14/30/90/180/365 天）。
- 混合检索：SQLite FTS5 稀疏 + LIKE 关键词 RRF 融合；无需嵌入模型即可召回。
- 会话隔离：`memory_session_isolation`（默认 true，记忆按会话隔离）。
- LLM 总结：`summary_batch_size`（默认每 10 条消息总结一次，0 关闭）。
- Agent 工具：`unified_chat_memory_recall` / `unified_chat_memory_memorize`。
- 备份：版本变更/每日 03:00 自动备份 DB，保留 `backup_keep_last` 份。
- 命令：`/umem status|search|forget|backup|reset|help`。

## 群聊拟人（默认关闭）

- `humanize_enable` 总开关；仅作用于群聊（私聊/@/唤醒词/指令必答）。
- 读空气两层过滤：动态概率（基础+回复后提升+注意力加成−疲劳惩罚）→
  可选 LLM 二段判断（`humanize_air_reading_llm`，超时兜底为回复）。
- 未回复消息进入缓存，下次回复时合并为上下文注入。
- 主动话题：`humanize_proactive`（沉默超阈值后随机开口）。
- 安全：`blacklist_users` / `trigger_keywords` / `blocked_keywords`。

## 学习深化

- 黑话挖掘：按会话统计高频词 → 候选入库 → 可选 LLM 推断含义
  （`slang_infer_enabled`）→ `/uslang list|confirm <id>|deny <id>` 审核确认后注入。
- 好感度：`enable_affinity`，互动 +1、每日向基线 50 衰减 10%，分档语气提示。
- 情绪：`enable_mood`，每日随机漂移 ±0.2，映射五档情绪注入 prompt。
- 人格审查链：`persona_auto_suggest` 定期生成建议，
  `/upersona list|approve <id>|reject <id>` 人工审批，绝不自动应用。
