# AstrBot 统一对话插件

> 统一对话增强、持久记忆与自适应学习的一体化 AstrBot 插件 · 基于 AstrBot 4.27.3 · Python + Rust 混合加速

快速开始请见 [docs/README.md](docs/README.md)。

## 特性

- **对话增强**：群组上下文压缩、社交上下文注入、指令过滤
- **持久记忆**：重要度评分、30 天自动清理、向量检索
- **自适应学习**：后台过滤→提炼→强化流水线
- **RAG**：复用 AstrBot 内置知识库（FAISS + FTS5 + RRF + Rerank），Agentic 自主调用
- **性能**：Rust 激进优化（`lto fat / x86-64-v3`）+ Python 回退

## 安装

```bash
# 宿主机（开发）
uv sync
uv run maturin develop --release

# Docker 内的 AstrBot（插件挂载于 data/plugins/astrbot_plugin_unified_chat）
docker restart astrbot
```

详见 `docs/` 人类文档。
