# 统一对话插件 - 人类文档总览

> 本目录为人人类文档（中文），Agent 产物请见 `.agents/docs/`。

## 目录

- [ARCHITECTURE.md](ARCHITECTURE.md) - 架构与模块划分
- [CONFIG.md](CONFIG.md) - WebUI 配置项说明
- [OPERATION.md](OPERATION.md) - 运维、部署与迁移
- [DEVELOPMENT.md](DEVELOPMENT.md) - 本地开发与测试

## 快速开始

1. **环境**：AstrBot `>=4.27.3`，Python 3.12，`uv`，可选 `cargo + maturin`。
2. **安装**：将本仓库置于 `data/plugins/astrbot_plugin_unified_chat`，重启 AstrBot。
3. **配置**：WebUI 插件页配置 `对话提供商/嵌入提供商/知识库`，启用所需域开关。
4. **验证**：发送 `/unified_status` 查看状态。

## 构建

```bash
uv sync
uv run pytest -q
uv run ruff check .
cargo test -p unified_chat_native
uv run maturin develop --release
```

详见各子文档。

## 功能总览

- **对话增强**：社交上下文注入、去重窗口、指令过滤
- **持久记忆**：原子化分类记忆、FTS5+关键词混合检索、会话隔离、LLM 批量总结、
  Agent 主动回忆/写入工具、自动备份
- **自适应学习**：filter→refine→reinforce 流水线、群黑话学习与审核、
  好感度/情绪体系、人格建议审查链
- **RAG**：agentic 知识库查询工具注入、嵌入变更全量迁移
- **群聊拟人**（可选）：读空气门控、注意力/疲劳、未回复缓存合并、主动话题
- **原生加速**：Rust 扩展四平台自动分发（abi3），失败回退纯 Python

命令：`/unified_status` `/unified_migrate` `/umem …` `/uslang …` `/upersona …`
