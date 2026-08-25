# Spec 010 — Learning Depth (Slang, Affinity, Mood, Persona Review)

## Goal
Deepen the adaptive-learning domain with group slang mining + LLM-inferred
meanings, per-user affinity with decay and tone hints, a drifting mood state,
and a persona-suggestion review chain — injected at on_llm_request under a
token budget. Zero new runtime deps; all features degrade silently without
a configured provider.

## Context
- Message history: `MessageRecord(umo, sender_id, content, created_at)` via
  `MessageRepo`; window helpers in `MessageSessionRepo`.
- Injection happens in `lifecycle.handle_llm_request` (memories, social
  context already exist); new block appended after memories.
- KV store (`UnifiedKV`) available for mood state and review queue.
- Tables must be registered in `_PLUGIN_TABLES` (SQLModel metadata is global).

## Requirements

### R001 — New models
- `SlangTerm`: id PK, `term`(idx,64), `meaning`(""), `umo`(idx,255),
  `status`("candidate"/"confirmed", idx), `count`(int), `created_at`.
- `UserAffinity`: id PK, `(umo,user_id)` unique-ish (index both, 255/64),
  `score` float default 50 clamp [0,100], `updated_at`.

### R002 — Slang mining (`services/slang_service.py`)
- `mine_terms(texts) -> list[(term, count)]`: CJK bigrams + latin words,
  stopwords filtered (tiny builtin list), min length 2, deterministic order.
- Cron-tick job (piggyback daily cron): for each active umo, mine recent
  messages (last 500); candidates = top-K (`slang_top_k`, default 15) terms
  with count ≥ `slang_min_count` (default 8) not already stored; insert as
  status=candidate.
- Inference pass (only when `slang_infer_enabled` and provider set): batch
  candidates (≤10) to llm_generate asking JSON `{"term": "meaning"}` map;
  parse defensively; write meaning keeping status=candidate.
- Commands via `/uslang list|confirm <id>|deny <id>`: confirm/deny flip
  status ("confirmed"/"denied").

### R003 — Injection composer (`services/inject_composer.py`)
- `compose(event, config, services...) -> str`: assembles one system block:
  - slang hits: confirmed terms whose term string appears in current message
    → `- {term}: {meaning}` (max 8)
  - affinity tone line from sender score bands: >70 warm / <30 cool / else
    neutral (only when `enable_affinity`)
  - mood one-liner from KV scalar [-1,1] → label map (only when
    `enable_mood`)
- Budget-trim final block to 800 chars (hard cut with ellipsis).
- Empty parts omitted; returns "" when nothing to say.

### R004 — Affinity (`services/affinity_service.py`)
- Bump on every stored message: +1 toward cap 100.
- Daily decay in cron: move score 10% toward baseline 50.
- Repo upsert + band helper.

### R005 — Mood (`services/mood_service.py`)
- KV key `mood_scalar`; drift ±0.2 random walk clamped [-1,1] on daily cron;
  label map: >0.5 excited, >0.1 happy, ≥-0.1 calm, >-0.5 down, else grumpy.

### R006 — Persona review chain (`services/persona_review.py`)
- KV key `persona_pending` holds JSON list of `{id, text, created_at}`;
- `maybe_suggest()`: only when `persona_auto_suggest` true and provider set;
  builds prompt from recent memory atoms (top 10 hybrid) → one paragraph
  persona tweak suggestion appended to pending (cap 20, drop oldest).
- Commands `/upersona list|approve <id>|reject <id>`; approve marks applied
  by removing entry AND returning suggestion text for admin to paste into
  AstrBot persona editor (we never touch core persona config directly).
- Backup-before-apply satisfied by construction: nothing applied automatically.

### R007 — Wiring & commands
- New hook call in handle_llm_request after inject_memories:
  `inject_learning_block(event, req, config, composer_inputs...)`.
- `/uslang`, `/upersona` command groups routed through lifecycle methods.

### R008 — Config keys (flat)
- `enable_style_learning: bool = true` (master for slang+injection block)
- `slang_top_k: int = 15`, `slang_min_count: int = 8`,
  `slang_infer_enabled: bool = false`
- `enable_affinity: bool = true`, `enable_mood: bool = true`
- `persona_auto_suggest: bool = false`

### R009 — Tests
- Mining determinism/stopwords; inference parse robustness; composer budget &
  empty-input ""; affinity bump/clamp/decay/bands; mood drift clamp/labels;
  review chain add/cap/approve/reject transitions; lifecycle injection appends
  system message when block non-empty.
- fullboot regression: existing suite stays green (block may be empty on
  fresh sandbox — no assertion change needed).

## Non-goals
- Automatic persona application, user→bot few-shot pair mining (requires
  assistant-reply capture), Hub HTTP API.
