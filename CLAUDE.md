# AGENTS.md

Instructions for AI coding agents working on this repository.

## Mandatory Skill Invocation

- **ZERO TOLERANCE: You MUST invoke relevant skills via the `skill` tool BEFORE doing anything else - before replying, before exploring files, before asking clarifying questions.**
- This rule overrides any tendency to "just answer quickly" or "just check one thing first."
- Check the available skills list first. If even one skill might apply (brainstorming, systematic-debugging, test-driven-development, writing-plans, etc.), invoke it.
- If you are unsure whether a skill applies, invoke it anyway. You can always stop following it if it turns out irrelevant.
- Failure to invoke applicable skills is a protocol violation.

## Language & Style

- Use English for internal reasoning and Chinese for all user-facing communication.
- **All code comments, commit messages, and agent-facing documentation must be in English.** Chinese characters are forbidden in source files, comments, commit messages, and agent-facing docs.
- Human-facing documentation under `docs/` is written in Chinese (see "Documentation Layout" below). It is the human handover language and is exempt from the English-only rule.

## Documentation Layout

Repository documentation is split by audience:

- `docs/` — **Human-facing, Chinese.** Onboarding, project status, roadmap, architecture, build environment, and development workflow. Start at `docs/README.md`.
- `.agents/docs/` — **Agent-facing, English.** Working artifacts: the milestone plan (`plan.md`), design specs (`specs/`), and implementation plans (`plans/`). Produced by the Superpowers workflow.
- `AGENTS.md` / `CLAUDE.md` — Agent instructions at the repository root.
- `README.md` — Human quick start, kept intentionally short; it links into `docs/`.

Agents write working artifacts under `.agents/docs/`, never under `docs/`. Human docs are updated deliberately, in Chinese.

## Commands

Build and test commands for this AstrBot plugin:

```bash
# Python env (uv)
uv sync
uv run pytest -q                          # unit + integration (mocked Context)
uv run pytest --cov=unified_chat --cov-report=term-missing
uv run ruff check .
uv run ruff format .

# Rust native extension (requires cargo + maturin)
cargo test -p unified_chat_native         # Rust unit tests
uv run maturin develop --release          # local develop (release, aggressive opt)
uv run maturin build --release --strip --manylinux 2_28  # portable wheel
uv run maturin develop --release --strip  # alias for local aggressive build

# E2E inside Docker AstrBot (plugin mounted at data/plugins/astrbot_plugin_unified_chat)
docker exec astrbot python -m pytest /data/plugins/astrbot_plugin_unified_chat/tests/e2e -q
```

## Skills Management

The vendored Superpowers skills under `.agents/skills/` are installed and maintained with the `npx skills` CLI (Vercel Labs). `skills-lock.json` is the lock file, with the same semantics as npm `package-lock.json`: it records exact upstream hashes so skill versions are reproducible across machines and branches.

| Command | Purpose |
|---|---|
| `npx skills check` | Compare local hashes against upstream and list available updates |
| `npx skills add <owner>/<repo>` | Install/upgrade a skill and update the lock file |
| `npx skills update` | Update all skills and the lock file |
| `npx skills experimental_install` | Restore skills strictly per the lock file (`npm ci` semantics) |

Rules:

- Only the CLI writes `skills-lock.json`; never hand-edit it. Hand-editing breaks reproducibility, the same way a hand-edited `package-lock.json` does.
- Run `npx skills check` before upgrading so updates are applied deliberately, not blindly.
- Do not install skills by hand (git clone / copy). A manual install makes the lock file and the actual install disagree.
- `npx skills rm` does not update the lock file; removing a skill leaves a stale entry that `experimental_install` will restore.
- `brainstorming`, `writing-plans`, `requesting-code-review`, and `subagent-driven-development` carry a deliberate local path patch: their spec/plan save defaults and example strings point to `.agents/docs/specs/` and `.agents/docs/plans/` instead of the upstream `docs/superpowers/...`. `npx skills check` reports these as diverged; that is expected. `npx skills update` overwrites them — re-apply the path patch after any update.

## AstrBot Plugin Constraints

- **Baseline:** AstrBot `>=4.27.3,<5.0.0`. Verify against `astrbot/core/star/star_manager.py` and `astrbot/core/star/context.py`.
- **Plugin entry:** `metadata.yaml` + `main.py` (`Star` subclass) is the only discovery path. Do not use legacy `@register` metadata; `metadata.yaml` is authoritative.
- **Imports MUST be package-relative:** AstrBot imports plugins as `data.plugins.<name>.main` and does NOT add the plugin directory to `sys.path`. Absolute `from unified_chat...` imports fail at load. Use relative imports (`from ..storage...`, `from .hooks...`) everywhere inside `unified_chat/`; `main.py` uses try-relative/except-absolute for local dev. Hot-reload only purges `data.plugins.<name>.*` modules, so absolute top-level packages would leak stale code across reloads.
- **Config schema is FLAT:** `_conf_schema.json` must be `{key: {type, description, default, hint, _special}}` — NOT JSON-Schema `{"type":"object","properties":{...}}` wrapped. `AstrBotConfig._config_schema_to_default_config` iterates top-level items. Valid types: `int float bool string text list file object template_list dict` (NOT `integer`/`boolean`). `_special: select_provider` / `select_knowledgebase` are flat per-key.
- **Storage:** Persistent data MUST go under `data/plugin_data/<plugin_name>/` via `StarTools.get_data_dir()` or `get_astrbot_data_path()`. Never write to the plugin source directory; it is wiped on update. In AstrBot runtime `StarTools.get_data_dir()` already resolves to `data/plugin_data/<plugin_name>/`.
- **SQLModel metadata is shared with AstrBot:** `SQLModel.metadata` is a global registry — `create_all` without `tables=` would create AstrBot's own tables inside the plugin DB. Always pass `tables=[MessageRecord.__table__, Memory.__table__, LearningLog.__table__, UnifiedKV.__table__]`.
- **AstrBot import graph is order-sensitive:** deep submodule imports (e.g. `astrbot.core.knowledge_base.kb_helper`) fail with a circular import unless `astrbot.api` is imported first. E2E test modules must `pytest.importorskip("astrbot.api")` before other astrbot imports.
- **Config:** Parsed config lands at `data/config/<plugin>_config.json`.
- **RAG:** Use built-in `Context.kb_manager` (`KnowledgeBaseManager`) and `EmbeddingProvider`/`RerankProvider`. Do not vendor a separate vector DB. Agentic mode injects `KnowledgeBaseQueryTool`.
- **Rust:** `pyproject.toml [tool.maturin] module-name = "unified_chat._native"` `bindings = "pyo3"`. Aggressive release profile (`lto="fat"`, `codegen-units=1`, `strip=true`; baseline x86-64 with NO `target-cpu` for distributed wheels, and panics must unwind - never `panic="abort"` in a PyO3 cdylib). All Rust exports must have a Python fallback in `unified_chat/native/fallback.py`.
- **Isolation:** No global singletons. All state via `Context` injection. Handlers use `functools.partial` binding; `__init__` must not raise — defer to `initialize()`.

## Docker E2E

- Local dev venv has no `astrbot`; e2e tests skip locally and must pass inside the real container:
  `docker exec astrbot python -m pytest /AstrBot/data/plugins/astrbot_plugin_unified_chat/tests/e2e -q`
- Test image: `soulter/astrbot:latest`, data volume `/AstrBot/data`, plugin dir `data/plugins/astrbot_plugin_unified_chat`.

## Code Style

- **Language:** Python 3.12+ (`asyncio`), Rust `edition 2021` for native crate.
- **Format:** `ruff` (Python, 100 col), `rustfmt` (Rust).
- **Naming:** `snake_case` for files/functions, `PascalCase` for types.
- **No Chinese comments.** No non-English identifiers.
- **Error handling:** Never let a single message crash the plugin; log and continue.

## Git Workflow

- Commit messages in English, Conventional Commits: `chore:`, `feat:`, `fix:`, `docs:`.
- Linear history via rebase, no merge commits.
- Branch prefixes: `feat/`, `fix:`, `docs/`, `chore/`, `refactor/`, `test/`.
- One objective per branch. Delete after merge.
- Agents need explicit user approval before push/PR/merge.

## Verification

- Before claiming completion, run `uv run pytest -q` and `uv run ruff check .`.
- For Rust changes, also `cargo test` and `uv run maturin develop --release` smoke import.
- Human docs under `docs/` must remain Chinese; agent docs under `.agents/docs/` must remain English.
