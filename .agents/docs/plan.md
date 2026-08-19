# Unified Chat Plugin - Milestone Plan

> Agent-facing, English. Human docs live in `docs/`.

## Milestones

### Phase 0: Scaffold & Baseline [IN PROGRESS]
- Repo scaffold, AGENTS.md, .agents/docs, skills-lock.json
- Minimal loadable plugin: metadata.yaml + main.py + _conf_schema.json
- pyproject.toml + Cargo.toml (maturin aggressive) + fallback
- Verify: `uv run pytest -q`, `ruff check`, `maturin develop --release`

### Phase 1: Storage & Config
- PluginConfig, StarTools.get_data_dir(), SQLite models, KV wrapper
- Config schema with select_provider/select_knowledgebase

### Phase 2: RAG Agentic
- rag_service wrapping Context.kb_manager.retrieve
- on_llm_request injecting KnowledgeBaseQueryTool
- Tests for no-KB / single-KB / multi-KB

### Phase 3: Core Domains
- chat_service (context compression, filtering)
- memory_service (CRUD, importance, 30d cleanup cron)
- learning_service (filter→refine→reinforce pipeline, background tasks)
- Rust chunk/dedup + fallback

### Phase 4: Hardening
- Manylinux wheel, CI, hot-reload, error isolation, terminate cleanup

### Phase 5: Docs & Release
- Human docs finalization, marketplace metadata, pre-release validation

## Constraints
- AstrBot >=4.27.3,<5.0.0, linux manylinux_2_28 x86_64 only, sqlite only
- RAG agentic, memory 30d, embedding migration required
- Human docs Chinese in docs/, agent docs English in .agents/docs/
