# Spec 007 — Native Layer Cross-Platform Build and Auto-Distribution

## Goal
Produce prebuilt native wheels for 4 platforms (linux-x86_64, linux-aarch64,
windows-x86_64, macos-arm64), publish them on GitHub Releases per version tag,
and let the plugin auto-fetch and load the matching binary at startup with a
sha256-verified, fail-silent bootstrap — keeping the pure-Python fallback as
the guaranteed floor.

## Context
- Current native facade (`unified_chat/native/__init__.py`): try-import
  `unified_chat._native`, any exception falls back to `native/fallback.py`.
  Only linux-x86_64 manylinux_2_28 wheels exist today (CI artifact only).
- `rust/Cargo.toml`: pyo3 0.26 with `extension-module`, no abi3 feature.
  Baseline Python is 3.12+ repo-wide, so `abi3-py312` yields one wheel per
  platform instead of per platform x CPython version.
- Plugin installs from source into `data/plugins/<name>`; the plugin source
  directory is wiped on update (AGENTS.md) — downloaded binaries MUST live
  under `data/plugin_data/astrbot_plugin_unified_chat/`.
- AstrBot runtime data dir resolves via existing `unified_chat.utils.path`
  helper chain (env override → StarTools → get_astrbot_data_path → cwd).
- GitHub repo: `zyzh2002/astrbot_plugin_unified_chat` (metadata.yaml still has
  placeholder `example/...`).
- CI lesson learned (run 32835734228): maturin-action must receive the
  manylinux target via its own `manylinux:` input, not CLI args, to actually
  enter the container.

## Requirements

### R001 — abi3 wheel
- Add `"abi3-py312"` to pyo3 features in `rust/Cargo.toml`.
- Wheel filename tags must become `cp312-abi3` (verify in release run logs).
- Existing `[tool.maturin.target.x86_64-unknown-linux-gnu]` rustflags
  (`target-cpu=x86-64-v3`) stay scoped to linux-x86_64 only.

### R002 — Release workflow
- New `.github/workflows/release.yml`, triggered on push of tags matching
  `v*`. Matrix:
  | target | runner | mechanism |
  |---|---|---|
  | linux-x86_64 | ubuntu-latest | maturin-action, `manylinux: "2_28"` input |
  | linux-aarch64 | ubuntu-latest | maturin-action, `manylinux: "2_28"` + `target: aarch64` |
  | windows-x86_64 | windows-latest | maturin-action (host build) |
  | macos-arm64 | macos-latest | maturin-action (host build) |
- Each job uploads its wheel; a final job generates `SHA256SUMS` over all
  wheels and creates the GitHub Release (tag = metadata version) attaching
  wheels + checksum file.
- Release job needs `contents: write` permission.
- Version consistency: tag `v0.1.0` must match Cargo package version;
  workflow fails fast if mismatch (grep check).

### R003 — Runtime loader (`unified_chat/native/bootstrap.py`)
- Load priority: direct import → cached binary → pure-Python fallback.
- `try_load_cached(data_dir: Path) -> bool`:
  - scan `<data_dir>/native/` for `_native*.so|*.pyd`
  - load via `importlib.util.spec_from_file_location("unified_chat._native", path)`
    inside a try/except; on success bind `chunk_text/hash_dedup/
    score_importance` onto the facade module and return True
- `platform_key() -> str | None`: maps current interpreter to asset selector:
  - windows + AMD64 → `win_amd64`
  - linux + x86_64 → `manylinux` + `x86_64`
  - linux + aarch64 → `manylinux` + `aarch64`
  - darwin + arm64 → `macosx` + `arm64`
  - anything else → None (no download)
- `wheel_asset_name(version: str) -> str | None`: deterministic expected wheel
  filename for the platform, e.g.
  `astrbot_plugin_unified_chat-<version>-cp312-abi3-manylinux_2_28_x86_64.whl`
  (package name comes from pyproject `[project] name`, normalized).
  Returns None when platform unsupported.
- `prefetch_async(config_enabled: bool) -> asyncio.Task | None`:
  - no-op when disabled, binary already loadable, or platform unsupported
  - otherwise schedules background download of
    `https://github.com/zyzh2002/astrbot_plugin_unified_chat/releases/download/v<version>/<asset>`
    plus `SHA256SUMS`; verifies sha256 of the wheel before extraction
  - extracts ONLY the `_native*.(so|pyd)` member into `<data_dir>/native/`
  - hard limits: timeout 30s, max size 32 MiB; ANY failure logs (info/warn)
    and returns silently — never raises, never blocks startup
- Facade integration: `native/__init__.py` calls `try_load_cached()` after the
  direct import fails; lifecycle `initialize()` triggers `prefetch_async()`
  once. Downloaded binaries take effect on NEXT start (no hot swap).

### R004 — Config
- Flat schema key `native_autodownload: bool = true` (+ description/hint),
  mirrored in `PluginConfig.DEFAULTS/from_dict/to_dict`.

### R005 — Metadata
- `metadata.yaml` repo → `https://github.com/zyzh2002/astrbot_plugin_unified_chat`.

### R006 — Tests (network fully mocked)
- platform_key mapping for all 4 supported + unsupported combos
- wheel_asset_name correctness incl. unsupported-platform None
- try_load_cached loads a stub .so/.pyd fixture and binds facade functions
- corrupt cache entry → clean fallback
- prefetch: sha256 mismatch → no extraction; timeout → silent give-up;
  happy path extracts binary into cache dir
- all existing tests stay green

## Out of scope
- Intel Mac, Windows ARM, musl/Alpine wheels
- Hot-swapping native functions mid-process
- pip-installing full wheels (bypasses AstrBot plugin loading)
- Any new runtime dependency
