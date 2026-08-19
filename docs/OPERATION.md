# 运维与部署

## 部署

- **Docker**：插件目录挂载 `data/plugins/astrbot_plugin_unified_chat`，重启容器即可热加载。无需在容器内编译 Rust，使用预构建 `manylinux_2_28` wheel。
- **宿主机开发**：`uv run maturin develop --release` 本地编译并安装至虚拟环境。

## 测试

```bash
uv run pytest -q                          # 单测（mock Context，无需 AstrBot 运行）
uv run pytest --cov=unified_chat
cargo test -p unified_chat_native
docker exec astrbot pytest /data/plugins/astrbot_plugin_unified_chat/tests/e2e -q
```

Docker 不影响单测，单测已 mock `Context`。

## 监控

- `/unified_status` - 学习统计、记忆条数、RAG 状态
- 日志 `astrbot.api.logger`，关键词 `unified_chat`

## 备份

备份 `data/plugin_data/unified_chat/` 与 `data/config/unified_chat_config.json`。
