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

### Phase 5: Docs & Release [DONE]
- Human docs finalized, repository metadata corrected, release workflow validated
- AstrBot optional `e2e` dependency, portable E2E and full-boot harness shipped
- v0.1.0 published with 4-platform native wheels and checksums

### Phase 6: Memory Depth [DONE]
- Typed atoms, per-type TTL, session isolation and FTS5 + LIKE RRF retrieval
- LLM summaries, recall/memorize tools, backups and `/umem` administration
- In-place schema migration with mandatory pre-migration backup
- Spec: specs/008-memory-depth.md

### Phase 7: Group Humanization [DONE]
- Air-reading gate, attention/fatigue state and unreplied-message cache
- Group-only proactive openers with persisted cooldown/deduplication
- Spec: specs/009-group-humanize.md

### Phase 8: Learning Depth [DONE]
- Per-session slang learning, affinity, mood and persona review chain
- Budgeted context injection and stable sender identity
- Spec: specs/010-learning-depth.md

### Phase 9: Hardening Round 2 [DONE]
- Aware-UTC storage comparisons, epoch conversion and FTS datetime binding
- Per-document KB migration with stale-flag recovery and persisted results
- Blacklist/blocked-keyword enforcement decoupled from humanize; reset guards
- Message/learning-log retention, async backups, per-session gate serialization
- Slang status advancement, budget-aware injection, SQL affinity decay, kv upsert
- FTS reconcile, cancellation-safe callbacks, state sweeps, sha256 sidecar
- Baseline x86-64 wheels, unwinding panics, capture dedup scoped per session
- Spec: specs/011-hardening-round-2.md

## Constraints
- AstrBot >=4.27.3,<5.0.0, Python >=3.12, SQLite/FTS5 storage
- Native wheels: linux-x86_64, linux-aarch64, windows-x86_64, macos-arm64
- RAG uses AstrBot's built-in KB only; embedding migration = full rebuild
- Persistent memory is session-isolated by default; destructive commands require admin
- Human docs Chinese in docs/, agent docs English in .agents/docs/
