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
