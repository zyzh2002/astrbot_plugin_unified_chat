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

## Rust

- `rust/Cargo.toml` 单 crate `cdylib`，`pyproject.toml: module-name = "unified_chat._native"`。
- 激进 `release`：`lto fat / codegen-units 1 / strip / x86-64-v3`，仅 `linux`。
- 所有导出需 `unified_chat/native/fallback.py` 回退。

## 分支

`feat/xxx` 单目标分支，`rebase` 线性历史，push 前需用户批准。
