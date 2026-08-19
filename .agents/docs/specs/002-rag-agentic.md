# Spec 002 — RAG Agentic (Tool Injection Only)

## Goal
Give the LLM autonomous access to the plugin-configured knowledge bases by injecting a plugin-scoped `FunctionTool` into `ProviderRequest.func_tool` during `on_llm_request`. Agentic is the ONLY retrieval mode — no direct context injection fallback.

## Context
- Verified against AstrBot `v4.27.3` source:
  - `astrbot/core/provider/entities.py:91` — `ProviderRequest.func_tool: ToolSet | None = None`
  - `astrbot/core/agent/tool.py` — `FunctionTool(name, description, parameters, handler, active)` dataclass; `ToolSet.add_tool(FunctionTool)` dedups by name (new overwrites existing); `ToolExecResult = str | mcp.types.CallToolResult`
  - `astrbot/core/agent/astr_agent_tool_exec.py:_execute_local` — overridden `call` is invoked as `tool.call(ContextWrapper, **tool_args)`; string return is wrapped into text content
  - `astrbot/core/tools/knowledge_base_tools.py` — reference builtin `KnowledgeBaseQueryTool` pattern (fields + `call`); `kb_manager.retrieve(query, kb_names, top_k_fusion=20, top_m_final=5) -> dict | None` with keys `context_text` / `results`
- The builtin `astr_kb_search` tool reads KBs from AstrBot global/session config, NOT plugin config. We therefore build our OWN tool bound to the plugin's `rag_kbs`; do not use `get_builtin_tool`.

## Requirements

### R001 — Plugin-scoped knowledge tool
- `services/rag_service.py` exposes `RagService(context)`.
- `RagService.build_kb_tool(kb_names: list[str], top_m_final: int = 5) -> FunctionTool | None`:
  - Returns `None` when `kb_names` empty or `context.kb_manager` missing (`getattr`).
  - All `astrbot.*` / `pydantic` imports happen lazily INSIDE the method; import failure → `None` (plugin stays loadable on API mismatch).
  - Returns a `FunctionTool` subclass instance:
    - `name = "unified_chat_kb_query"`
    - `description` — tells the LLM to use this for facts/background from the plugin knowledge bases
    - `parameters` — JSON Schema object with required `query: string`
    - `call(context, **kwargs)` — mirrors builtin behavior:
      - empty/blank `query` → `"error: Query parameter is empty."`
      - calls `kb_manager.retrieve(query=query, kb_names=self.kb_names, top_k_fusion=20, top_m_final=self.top_m_final)` inside try/except → on exception returns `"error: {e}"`
      - `None`/empty result → `"No relevant knowledge found."`
      - non-dict result → `"No relevant knowledge found."`
      - success → `result["context_text"]` (non-empty), else the no-result message
- Tool instance is built per request (fresh `kb_manager` binding; no stale reference after hot reload).

### R002 — Agentic only
- No other retrieval mode exists. `rag_agentic=false` → hook does nothing. Empty `rag_kbs` → nothing.

### R003 — Injection hook
- `core/hooks.py` provides `async def inject_kb_tool(event, req, config: PluginConfig, rag_service: RagService) -> None`.
- Gating order: `config.rag_agentic` → non-empty `config.rag_kbs` → `build_kb_tool` → inject.
- `req.func_tool is None` → create `ToolSet()` (lazy import guarded) and assign.
- If `req.func_tool.get_tool(tool.name)` already exists → skip (no duplicate).
- Otherwise `req.func_tool.add_tool(tool)`.
- Any exception → log error, return (never raise into the LLM request path).

### R004 — Lifecycle wiring
- `PluginLifecycle.on_load` creates `self._rag_service = RagService(self.context)` inside the try block (optional: missing astrbot APIs must not fail load).
- `PluginLifecycle.handle_llm_request(event, req)` calls `inject_kb_tool(...)` when `_config` present, wrapped in try/except.

### R005 — Error isolation
- Single message / single request must never crash the plugin: all hook and tool paths catch exceptions and log via `astrbot.api.logger` (import guarded).

## Non-Goals
- Direct RAG context injection, rerank configuration changes, embedding migration (spec 006), memory vector retrieval (spec 004).

## Acceptance Criteria
- Unit tests (mocked astrbot stubs, real pydantic): no-KB → no tool; single-KB → one tool with correct `name`; multi-KB → `retrieve` called with all names; `rag_agentic=false` → `func_tool` untouched; existing tool set preserved (append); duplicate injection skipped; `call` error paths return documented strings; `kb_manager=None` → no tool.
- `opencode debug lsp diagnostics` clean for new files; `uv run ruff check .` clean; `uv run pytest -q` green.
- Docker e2e (`docker exec astrbot python -m pytest .../tests/e2e -q`) must pass when AstrBot is running (documented in e2e test file).

## Constraints
- AstrBot `>=4.27.3,<5.0.0`; `pydantic` provided by AstrBot at runtime, added as dev-only dep for tests.
- No global singletons; `RagService` instance per plugin lifecycle.
- English comments; `ruff` 100 col.

## Out of Scope
- Knowledge base indexing, chunking, migration.

## References
- `docs/ARCHITECTURE.md` (hooks), `docs/CONFIG.md` (`rag_agentic`, `rag_kbs`), `.agents/docs/specs/001-storage-config.md`.
