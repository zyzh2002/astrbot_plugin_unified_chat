# 开发指南

## 环境

```bash
uv sync
uv run ruff check .
uv run ruff format .
uv run pytest -q
cargo test
uv run maturin develop --release --strip
```

## 代码规范

- `Python 3.12+`，`ruff 100 col`，`snake_case` 文件/函数，`PascalCase` 类型。
- 注释与提交均为英文，`Conventional Commits`。
- `__init__` 不抛异常，`initialize()` 中做 I/O。

## AstrBot 插件硬约束（实测踩坑）

- **包内相对导入**：AstrBot 以 `data.plugins.<name>.main` 导入插件且不把插件目录加入 `sys.path`，
  `unified_chat/` 内部必须使用相对导入（`from ..storage...`）；`main.py` 用 try-relative/except-absolute 兼容本地开发。
  热重载只清理 `data.plugins.<name>.*` 前缀模块，绝对顶层包会残留旧代码。
- **配置 schema 必须扁平**：`_conf_schema.json` 是 `{key: {type, description, default, hint, _special}}`，
  不能套 `{"type":"object","properties":{...}}`。合法类型：`int float bool string text list file object template_list dict`
  （`boolean`/`integer` 会直接加载失败）。
- **SQLModel metadata 全局共享**：`create_all` 必须传 `tables=[...]`，否则会把 AstrBot 自己的表建进插件库。
- **导入顺序敏感**：深度子模块（如 `kb_helper`）必须先 `import astrbot.api`，否则循环导入。

## Docker 调试

```bash
# 同步插件到容器数据卷并重启
rsync -a --exclude .venv --exclude target --exclude .git --exclude '*.so' . /tmp/opencode/astrbot-e2e/data/plugins/astrbot_plugin_unified_chat/
docker restart astrbot
docker logs astrbot | grep unified
# 容器内 e2e
docker exec astrbot python -m pytest /AstrBot/data/plugins/astrbot_plugin_unified_chat/tests/e2e -q
```

## Rust

- `rust/Cargo.toml` 单 crate `cdylib`，`pyproject.toml: module-name = "unified_chat._native"`。
- 激进 `release`：`lto fat / codegen-units 1 / strip / x86-64-v3`，仅 `linux`。
- 所有导出需 `unified_chat/native/fallback.py` 回退。

## 分支

`feat/xxx` 单目标分支，`rebase` 线性历史，push 前需用户批准。
