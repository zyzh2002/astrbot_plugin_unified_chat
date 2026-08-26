"""Plugin configuration with defaults and helpers."""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULTS = {
    "enable_conversation_enhance": True,
    "enable_persistent_memory": True,
    "enable_adaptive_learning": True,
    "rag_agentic": True,
    "rag_kbs": [],
    "chat_provider_id": "",
    "embedding_provider_id": "",
    "rerank_provider_id": "",
    "memory_cleanup_days": 30,
    "importance_threshold": 0.3,
    "memory_kb_name": "unified_chat_memories",
    "native_autodownload": True,
    "memory_session_isolation": True,
    "summary_batch_size": 10,
    "backup_keep_last": 10,
    "humanize_enable": False,
    "humanize_base_probability": 0.15,
    "humanize_after_reply_probability": 0.8,
    "humanize_boost_window_seconds": 120,
    "humanize_attention_enabled": True,
    "humanize_attention_boost_max": 0.3,
    "humanize_fatigue_penalty_max": 0.35,
    "humanize_air_reading_llm": True,
    "humanize_air_reading_provider_id": "",
    "humanize_proactive": False,
    "humanize_proactive_min_silence_minutes": 45,
    "blacklist_users": [],
    "trigger_keywords": [],
    "blocked_keywords": [],
    "enable_style_learning": True,
    "slang_top_k": 15,
    "slang_min_count": 8,
    "slang_infer_enabled": False,
    "enable_affinity": True,
    "enable_mood": True,
    "persona_auto_suggest": False,
}


@dataclass
class PluginConfig:
    enable_conversation_enhance: bool = True
    enable_persistent_memory: bool = True
    enable_adaptive_learning: bool = True
    rag_agentic: bool = True
    rag_kbs: list[str] = field(default_factory=list)
    chat_provider_id: str = ""
    embedding_provider_id: str = ""
    rerank_provider_id: str = ""
    memory_cleanup_days: int = 30
    importance_threshold: float = 0.3
    memory_kb_name: str = "unified_chat_memories"
    native_autodownload: bool = True
    memory_session_isolation: bool = True
    summary_batch_size: int = 10
    backup_keep_last: int = 10
    humanize_enable: bool = False
    humanize_base_probability: float = 0.15
    humanize_after_reply_probability: float = 0.8
    humanize_boost_window_seconds: int = 120
    humanize_attention_enabled: bool = True
    humanize_attention_boost_max: float = 0.3
    humanize_fatigue_penalty_max: float = 0.35
    humanize_air_reading_llm: bool = True
    humanize_air_reading_provider_id: str = ""
    humanize_proactive: bool = False
    humanize_proactive_min_silence_minutes: int = 45
    blacklist_users: list[str] = field(default_factory=list)
    trigger_keywords: list[str] = field(default_factory=list)
    blocked_keywords: list[str] = field(default_factory=list)
    enable_style_learning: bool = True
    slang_top_k: int = 15
    slang_min_count: int = 8
    slang_infer_enabled: bool = False
    enable_affinity: bool = True
    enable_mood: bool = True
    persona_auto_suggest: bool = False
    data_dir: str = ""

    @classmethod
    def from_dict(cls, raw: dict | None, data_dir: str = "") -> PluginConfig:
        raw = raw or {}

        def pick(key: str, default):
            return raw.get(key, default)

        d = DEFAULTS

        # Handle rag_kbs coercion: must be list of strings
        raw_kbs = pick("rag_kbs", d["rag_kbs"])
        if isinstance(raw_kbs, list):
            rag_kbs = [str(x) for x in raw_kbs if isinstance(x, (str, int, float))]
        else:
            rag_kbs = list(d["rag_kbs"])  # fallback to default

        # Clamp memory_cleanup_days
        try:
            mcd = int(pick("memory_cleanup_days", d["memory_cleanup_days"]))
        except (TypeError, ValueError):
            mcd = int(d["memory_cleanup_days"])
        if mcd < 1:
            mcd = int(d["memory_cleanup_days"])

        # Clamp importance_threshold into [0,1]
        try:
            thr = float(pick("importance_threshold", d["importance_threshold"]))
        except (TypeError, ValueError):
            thr = float(d["importance_threshold"])
        if not 0.0 <= thr <= 1.0:
            thr = float(d["importance_threshold"])

        return cls(
            enable_conversation_enhance=bool(
                pick("enable_conversation_enhance", d["enable_conversation_enhance"])  # noqa: E501
            ),
            enable_persistent_memory=bool(
                pick("enable_persistent_memory", d["enable_persistent_memory"])  # noqa: E501
            ),
            enable_adaptive_learning=bool(
                pick("enable_adaptive_learning", d["enable_adaptive_learning"])  # noqa: E501
            ),
            rag_agentic=bool(pick("rag_agentic", d["rag_agentic"])),
            rag_kbs=rag_kbs,
            chat_provider_id=str(pick("chat_provider_id", d["chat_provider_id"])),
            embedding_provider_id=str(pick("embedding_provider_id", d["embedding_provider_id"])),
            rerank_provider_id=str(pick("rerank_provider_id", d["rerank_provider_id"])),
            memory_cleanup_days=mcd,
            importance_threshold=thr,
            memory_kb_name=str(pick("memory_kb_name", d["memory_kb_name"])),
            blacklist_users=[
                str(x)
                for x in (
                    raw.get("blacklist_users")
                    if isinstance(raw.get("blacklist_users"), list)
                    else []
                )
            ],
            trigger_keywords=[
                str(x)
                for x in (
                    raw.get("trigger_keywords")
                    if isinstance(raw.get("trigger_keywords"), list)
                    else []
                )
            ],
            blocked_keywords=[
                str(x)
                for x in (
                    raw.get("blocked_keywords")
                    if isinstance(raw.get("blocked_keywords"), list)
                    else []
                )
            ],
            enable_style_learning=bool(
                pick("enable_style_learning", d["enable_style_learning"])
            ),
            slang_top_k=int(pick("slang_top_k", d["slang_top_k"])),
            slang_min_count=int(pick("slang_min_count", d["slang_min_count"])),
            slang_infer_enabled=bool(
                pick("slang_infer_enabled", d["slang_infer_enabled"])
            ),
            enable_affinity=bool(pick("enable_affinity", d["enable_affinity"])),
            enable_mood=bool(pick("enable_mood", d["enable_mood"])),
            persona_auto_suggest=bool(
                pick("persona_auto_suggest", d["persona_auto_suggest"])
            ),
            native_autodownload=bool(
                pick("native_autodownload", d["native_autodownload"])
            ),
            memory_session_isolation=bool(
                pick("memory_session_isolation", d["memory_session_isolation"])
            ),
            summary_batch_size=int(pick("summary_batch_size", d["summary_batch_size"])),
            backup_keep_last=int(pick("backup_keep_last", d["backup_keep_last"])),
            humanize_enable=bool(pick("humanize_enable", d["humanize_enable"])),
            humanize_base_probability=float(
                pick("humanize_base_probability", d["humanize_base_probability"])
            ),
            humanize_after_reply_probability=float(
                pick("humanize_after_reply_probability", d["humanize_after_reply_probability"])
            ),
            humanize_boost_window_seconds=int(
                pick("humanize_boost_window_seconds", d["humanize_boost_window_seconds"])
            ),
            humanize_attention_enabled=bool(
                pick("humanize_attention_enabled", d["humanize_attention_enabled"])
            ),
            humanize_attention_boost_max=float(
                pick("humanize_attention_boost_max", d["humanize_attention_boost_max"])
            ),
            humanize_fatigue_penalty_max=float(
                pick("humanize_fatigue_penalty_max", d["humanize_fatigue_penalty_max"])
            ),
            humanize_air_reading_llm=bool(
                pick("humanize_air_reading_llm", d["humanize_air_reading_llm"])
            ),
            humanize_air_reading_provider_id=str(
                pick("humanize_air_reading_provider_id", d["humanize_air_reading_provider_id"])
            ),
            humanize_proactive=bool(pick("humanize_proactive", d["humanize_proactive"])),
            humanize_proactive_min_silence_minutes=int(
                pick(
                    "humanize_proactive_min_silence_minutes",
                    d["humanize_proactive_min_silence_minutes"],
                )
            ),
            data_dir=data_dir,
        )

    def to_dict(self) -> dict:
        return {
            "enable_conversation_enhance": self.enable_conversation_enhance,
            "enable_persistent_memory": self.enable_persistent_memory,
            "enable_adaptive_learning": self.enable_adaptive_learning,
            "rag_agentic": self.rag_agentic,
            "rag_kbs": self.rag_kbs,
            "chat_provider_id": self.chat_provider_id,
            "embedding_provider_id": self.embedding_provider_id,
            "rerank_provider_id": self.rerank_provider_id,
            "memory_cleanup_days": self.memory_cleanup_days,
            "importance_threshold": self.importance_threshold,
            "memory_kb_name": self.memory_kb_name,
            "native_autodownload": self.native_autodownload,
            "memory_session_isolation": self.memory_session_isolation,
            "summary_batch_size": self.summary_batch_size,
            "backup_keep_last": self.backup_keep_last,
            "humanize_enable": self.humanize_enable,
            "humanize_base_probability": self.humanize_base_probability,
            "humanize_after_reply_probability": self.humanize_after_reply_probability,
            "humanize_boost_window_seconds": self.humanize_boost_window_seconds,
            "humanize_attention_enabled": self.humanize_attention_enabled,
            "humanize_attention_boost_max": self.humanize_attention_boost_max,
            "humanize_fatigue_penalty_max": self.humanize_fatigue_penalty_max,
            "humanize_air_reading_llm": self.humanize_air_reading_llm,
            "humanize_air_reading_provider_id": self.humanize_air_reading_provider_id,
            "humanize_proactive": self.humanize_proactive,
            "humanize_proactive_min_silence_minutes": (
                self.humanize_proactive_min_silence_minutes
            ),
            "blacklist_users": self.blacklist_users,
            "trigger_keywords": self.trigger_keywords,
            "blocked_keywords": self.blocked_keywords,
            "enable_style_learning": self.enable_style_learning,
            "slang_top_k": self.slang_top_k,
            "slang_min_count": self.slang_min_count,
            "slang_infer_enabled": self.slang_infer_enabled,
            "enable_affinity": self.enable_affinity,
            "enable_mood": self.enable_mood,
            "persona_auto_suggest": self.persona_auto_suggest,
        }
