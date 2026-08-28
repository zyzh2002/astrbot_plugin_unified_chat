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

- `/unified_status` - 运行状态、记忆条数、消息数、学习流水各阶段统计、迁移标记、
  最近迁移结果（`migration_last=<kb>: <结果>`）
- `/unified_migrate <kb_name>` - 后台逐文档重建 KB 索引（更换嵌入模型后使用）；
  上传失败只影响单个文档并尽力回传原文；崩溃残留的 running 标志 6 小时后自动失效，
  插件启动时亦会清扫
- 日志 `astrbot.api.logger`，关键词 `unified_chat`（FTS 维护、后台任务失败均有告警）

## 备份

备份 `data/plugin_data/astrbot_plugin_unified_chat/` 与 `data/config/unified_chat_config.json`。
备份通过线程池执行，不阻塞事件循环。
