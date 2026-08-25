# Spec 009 — Group Chat Humanization (Air Gate, Attention, Unreplied Cache, Proactive)

## Goal
Give the bot human-like group-chat behavior: probabilistic "air reading"
reply gating with an optional LLM second opinion, attention/fatigue state,
an unreplied-message cache merged into the next reply, and silence-triggered
proactive openers — all behind a master switch defaulting OFF.

## Context
- The plugin observes messages via `@filter.event_message_type(ALL)`; the
  actual reply is produced by AstrBot core. Preventing a reply therefore
  requires `event.stop_event()` (verified in astrbot 4.27.4) before core
  dispatches to LLM.
- `event.is_wake` marks @bot/wake-word/private messages — these must bypass
  any gate (always reply).
- Proactive sending: `Context.send_message(unified_msg_origin, MessageChain)`
  (verified).
- LLM calls: `context.llm_generate(chat_provider_id, prompt, system_prompt)`
  pattern (learning_service).
- Zero new runtime deps; flat config schema; fail-silent everywhere.

## Requirements

### R001 — Config (flat keys, all prefixed `humanize_`)
- `humanize_enable: bool = false` — master switch (conservative default).
- `humanize_base_probability: float = 0.15`
- `humanize_after_reply_probability: float = 0.8` (boost value after our reply)
- `humanize_boost_window_seconds: int = 120`
- `humanize_attention_enabled: bool = true`
- `humanize_attention_boost_max: float = 0.3`
- `humanize_fatigue_penalty_max: float = 0.35`
- `humanize_air_reading_llm: bool = true` (second layer)
- `humanize_air_reading_provider_id: str = ""` (fallback: chat_provider_id)
- `humanize_proactive: bool = false`
- `humanize_proactive_min_silence_minutes: int = 45`
- `blacklist_users: list[str] = []`
- `trigger_keywords: list[str] = []` (probability forced to 1.0)
- `blocked_keywords: list[str] = []` (message dropped entirely)

### R002 — Reply gate (`services/humanize_gate.py`)
- Pure logic module with injected RNG (`random.Random`) for determinism.
- `GateState` per session (in-memory dict on service): last_reply_ts,
  last_message_ts, consecutive_replies, per-user attention {user: float}.
- `decide(event, now) -> Decision(reply: bool, reason: str)`:
  - bypass reply=True reasons: not enabled / private-or-wake / command /
    blacklist-user(reply=False, reason=blacklist) / trigger keyword hit
    (reply=True, reason=trigger)
  - probability = base + boost(now-last_reply within window) +
    attention(user) * max_boost − fatigue(consecutive_replies) * max_penalty;
    clamp [0,1]; roll RNG.
- Attention: +0.25 per message from user (cap 1.0), exponential decay
  half-life 10 min applied lazily using elapsed time.
- Fatigue: consecutive_replies increments on allow-within-window, resets on
  silence > window; penalty = min(consecutive * 0.12, max_penalty).

### R003 — Air-reading LLM layer (`services/humanize_air.py`)
- When first-layer allows AND config enables it: ask provider
  `"Should the bot reply? Answer strictly YES or NO."` with recent context
  lines (last ≤10 cached/recent texts). Timeout 8s; ANY failure or unparsable
  answer → treat as YES (fail-open to reply). NO → gate denies with
  reason=air_no.

### R004 — Unreplied cache (`services/unreplied_cache.py`)
- Per-session deque(maxlen=20) of `(sender, text, ts)`; entries older than
  30 min pruned on access.
- On deny: append message, then `event.stop_event()`.
- On allow: if cache non-empty, build system-context block
  `"Recent group chatter without reply:\n- ..."` returned by service for the
  existing social-injection hook to append; cache cleared.
- Cold-group promotion: when cache ages > 30 min without replies, entries are
  already persisted by chat_service.record() — promotion = clear only.

### R005 — Pipeline integration
- In `main.on_message` path BEFORE background stages: run guard chain:
  blocked_keyword → drop entirely (no store); blacklist → stop_event, no
  store; gate deny (groups only) → cache + stop_event, still record message
  history (record stays).
- On allow: expose merged unreplied block through ChatService.social_context
  composition (appended after existing social buffer).

### R006 — Proactive opener (`services/humanize_proactive.py`)
- Cron-tick style check every run of the existing daily cron? No — separate
  lightweight loop (60s interval) started only when enabled.
- For each known session (from MessageRepo distinct umo): if silence ≥
  threshold minutes and RNG < 0.5 → llm_generate one-sentence opener prompt →
  `context.send_message(umo, MessageChain([Plain(text)]))`; duplicate-output
  suppression: skip if identical text sent in last 24h (KV set).
- All failures silent; loop never crashes plugin.

### R007 — Tests
- Gate table-driven: private/command/@/blacklist/trigger paths deterministic.
- Probability math: boosts and penalties bounded; RNG seeded outcomes.
- Attention decay over simulated elapsed time; fatigue accumulation/reset.
- Air layer: YES/NO parse; timeout→allow; garbage→allow.
- Cache: append/prune/merge/clear lifecycle; merge text format.
- Blocked keyword drops before storage (repo count unchanged).
- fullboot: webchat is non-group → gate must NOT interfere (regression that
  private chats always reply even with humanize_enable=true).

## Non-goals (deferred)
- Typo simulation (needs pypinyin), send-side reply-delay simulation and
  outgoing-duplicate interception (requires response-stage hooks), sleep
  mode, poke/forward parsing.
