# Unified Chat Plugin - Milestone Plan

> Agent-facing, English. Human docs live in `docs/`.

## Milestones

### Phase 0: Scaffold & Baseline [DONE]
- Repo scaffold, AGENTS.md, .agents/docs, skills-lock.json
- Minimal loadable plugin: metadata.yaml + main.py + _conf_schema.json
- pyproject.toml + Cargo.toml (maturin aggressive) + fallback
- Verify: `uv run pytest -q`, `ruff check`, `maturin develop --release`

### Phase 1: Storage & Config [DONE]
- PluginConfig, StarTools.get_data_dir(), SQLite models, KV wrapper
- Config schema with select_provider/select_knowledgebase
- Spec: specs/001-storage-config.md

### Phase 2: RAG Agentic [DONE]
- rag_service wrapping Context.kb_manager.retrieve (plugin-scoped FunctionTool)
- on_llm_request injecting unified_chat_kb_query tool (agentic only)
- Tests for no-KB / single-KB / multi-KB
- Spec: specs/002-rag-agentic.md

### Phase 3: Core Domains [DONE]
- chat_service (command filter, dedup hash window, social context buffer)
- memory_service (scoring, KB-backed vector storage, 30d cleanup cron 03:00)
- learning_service (filter→refine→reinforce, background tasks, Semaphore 2)
- Rust chunk/dedup (FNV-1a hash_dedup) + bit-identical fallback
- Specs: specs/003-chat-enhance.md, 004-persistent-memory.md, 005-adaptive-learning.md

### Phase 4: Hardening [DONE]
- Migration service: full KB index rebuild via snapshot-delete-reupload
- needs_migration detection (KV embedding snapshot), async unified_status
- Manylinux 2_28 CI (GitHub Actions), error isolation, terminate cleanup, e2e contracts
- Spec: specs/006-hardening.md

### Phase 5: Docs & Release [PENDING]
- Human docs finalization (docs/ already updated), marketplace metadata, pre-release validation
- Real Docker E2E run against AstrBot >=4.27.3 (local machine has no astrbot container yet)

## Constraints
- AstrBot >=4.27.3,<5.0.0, linux manylinux_2_28 x86_64 only, sqlite only
- RAG agentic only, memory 30d, embedding migration = full rebuild
- Human docs Chinese in docs/, agent docs English in .agents/docs/
