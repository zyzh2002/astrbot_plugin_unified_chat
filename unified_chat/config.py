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
        }
