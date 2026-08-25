"""Group slang mining and LLM meaning inference (zero-dep tokenization)."""

from __future__ import annotations

import contextlib
import json
import re
from typing import Any

from ..storage import repo as repos
from ..storage.models import SlangTerm

_STOPWORDS = {
    "the", "and", "for", "are", "but", "not", "you", "this", "that", "with",
    "have", "from", "what", "when", "where", "will", "can", "has", "was",
    "的", "了", "是", "在", "我", "有", "和", "就", "不", "人", "都", "一个",
    "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有", "看",
    "好", "自己", "这",
}

_TOKEN_RE = re.compile(r"[A-Za-z]{2,}|[\u4e00-\u9fff]{1,}")


def mine_terms(texts: list[str], top_k: int = 15, min_count: int = 8) -> list[tuple[str, int]]:
    """Count CJK bigrams and latin words; return deterministic top list."""
    counts: dict[str, int] = {}
    for text in texts:
        for token in _TOKEN_RE.findall(text or ""):
            lowered = token.lower()
            if lowered in _STOPWORDS:
                continue
            counts[lowered] = counts.get(lowered, 0) + 1
            if re.fullmatch(r"[\u4e00-\u9fff]+", token):
                for i in range(len(token) - 1):
                    bigram = token[i : i + 2]
                    if bigram not in _STOPWORDS:
                        counts[bigram] = counts.get(bigram, 0) + 1
    ranked = sorted(
        ((term, count) for term, count in counts.items() if count >= min_count),
        key=lambda item: (-item[1], item[0]),
    )
    seen: set[str] = set()
    result: list[tuple[str, int]] = []
    for term, count in ranked:
        if any(term in other or other in term for other, _ in result):
            continue
        result.append((term, count))
        seen.add(term)
        if len(result) >= top_k:
            break
    return result


def parse_meanings(raw: str) -> dict[str, str]:
    """Defensively parse an LLM reply into {term: meaning}."""
    if not raw:
        return {}
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in data.items():
        term = str(key).strip()
        meaning = str(value).strip()
        if term and meaning and len(meaning) <= 200:
            out[term[:64]] = meaning
    return out


INFER_SYSTEM_PROMPT = (
    "Below are high-frequency slang terms from one group chat. Infer each "
    "term's meaning IN CONTEXT of the sample messages. Reply ONLY with a "
    "JSON object mapping each term to a short Chinese meaning."
)


class SlangService:
    """Mines candidates and infers meanings (fail-silent)."""

    def __init__(self, context: Any, config: Any):
        self.context = context
        self.config = config

    async def refresh_candidates(self) -> int:
        """Mine recent messages per session; insert unseen candidates."""
        try:
            added = 0
            top_k = int(getattr(self.config, "slang_top_k", 15))
            min_count = int(getattr(self.config, "slang_min_count", 8))
            sessions = await repos.MessageScanRepo.distinct_umos(limit=10)
            for umo, _last in sessions:
                rows = await repos.MessageSessionRepo.list_recent_by_session(umo, 500)
                texts = [r.content for r in rows]
                for term, count in mine_terms(texts, top_k=top_k, min_count=min_count):
                    if await repos.SlangRepo.exists_term(term):
                        continue
                    await repos.SlangRepo.add(
                        SlangTerm(term=term, umo=umo, count=count)
                    )
                    added += 1
            return added
        except Exception:
            self._log_error("refresh_candidates")
            return 0

    async def infer_pending_meanings(self) -> int:
        """Ask the LLM for meanings of up to 10 pending candidates."""
        llm_generate = getattr(self.context, "llm_generate", None)
        provider_id = getattr(self.config, "chat_provider_id", "")
        if (
            not getattr(self.config, "slang_infer_enabled", False)
            or llm_generate is None
            or not provider_id
        ):
            return 0
        try:
            pending = await repos.SlangRepo.list_by_status("candidate", limit=10)
            if not pending:
                return 0
            samples = await self._sample_lines()
            listing = "\n".join(f"- {t.term} (x{t.count})" for t in pending)
            resp = await llm_generate(
                chat_provider_id=provider_id,
                prompt=f"Terms:\n{listing}\n\nRecent messages:\n{samples}",
                system_prompt=INFER_SYSTEM_PROMPT,
            )
            raw = (getattr(resp, "completion_text", "") or "").strip()
            meanings = parse_meanings(raw)
            updated = 0
            for term_obj in pending:
                meaning = meanings.get(term_obj.term)
                if meaning:
                    await repos.SlangRepo.set_meaning(term_obj.id, meaning)
                    updated += 1
            return updated
        except Exception:
            self._log_error("infer_pending")
            return 0

    async def _sample_lines(self) -> str:
        try:
            sessions = await repos.MessageScanRepo.distinct_umos(limit=3)
            lines: list[str] = []
            for umo, _ in sessions:
                rows = await repos.MessageSessionRepo.list_recent_by_session(umo, 20)
                lines.extend(r.content[:60] for r in rows if r.content.strip())
            return "\n".join(lines[:40])
        except Exception:
            return ""

    @staticmethod
    def _log_error(msg: str) -> None:
        with contextlib.suppress(Exception):
            from astrbot.api import logger  # type: ignore

            logger.error(f"[unified_chat] slang {msg}", exc_info=True)
