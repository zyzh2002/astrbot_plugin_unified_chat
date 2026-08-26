"""Affinity decay, mood drift and persona suggestion cron helpers."""

from __future__ import annotations

import contextlib
import random
from typing import Any

from ..storage import kv as kv_store
from ..storage import repo as repos

MOOD_KEY = "mood_scalar"
MOOD_MIN = -1.0
MOOD_MAX = 1.0


def mood_label(scalar: float) -> str:
    if scalar > 0.5:
        return "excited"
    if scalar > 0.1:
        return "happy"
    if scalar >= -0.1:
        return "calm"
    if scalar > -0.5:
        return "down"
    return "grumpy"


async def get_mood() -> float:
    try:
        raw = await kv_store.kv_get(MOOD_KEY)
        return max(MOOD_MIN, min(MOOD_MAX, float(raw or 0.0)))
    except Exception:
        return 0.0


async def set_mood(scalar: float) -> None:
    await kv_store.kv_set(MOOD_KEY, max(MOOD_MIN, min(MOOD_MAX, float(scalar))))


class DailyLearningJobs:
    """Runs affinity decay, mood drift, slang refresh, persona suggestions."""

    def __init__(
        self,
        context: Any,
        config: Any,
        memory_service: Any | None = None,
        rng: random.Random | None = None,
    ):
        self.context = context
        self.config = config
        self.memory_service = memory_service
        self.rng = rng or random.Random()

    async def run(self) -> dict[str, int]:
        results = {
            "affinity_decayed": 0,
            "mood": 0,
            "slang_candidates": 0,
            "slang_inferred": 0,
            "persona_suggested": 0,
        }
        with contextlib.suppress(Exception):
            results["affinity_decayed"] = await self._decay_affinity()
        with contextlib.suppress(Exception):
            results["mood"] = await self._drift_mood()
        if getattr(self.config, "enable_style_learning", True):
            from .slang_service import SlangService

            slang = SlangService(self.context, self.config)
            with contextlib.suppress(Exception):
                results["slang_candidates"] = await slang.refresh_candidates()
            with contextlib.suppress(Exception):
                results["slang_inferred"] = await slang.infer_pending_meanings()
        if getattr(self.config, "persona_auto_suggest", False):
            from .persona_review import PersonaReviewService

            review = PersonaReviewService(
                self.context,
                self.config,
                self.memory_service,
            )
            with contextlib.suppress(Exception):
                sessions = await repos.MessageScanRepo.distinct_group_umos(limit=10)
                for session_id, _last_ts in sessions:
                    suggested = await review.maybe_suggest(session_id)
                    results["persona_suggested"] += int(bool(suggested))
        return results

    async def _decay_affinity(self) -> int:
        rows = await repos.AffinityRepo.all_rows()
        changed = 0
        for row in rows:
            baseline = repos.AffinityRepo.BASELINE
            new_score = baseline + (row.score - baseline) * 0.9
            new_score = round(new_score, 2)
            if abs(new_score - row.score) >= 0.01:
                row.score = new_score
                await repos.AffinityRepo.save_score(row)
                changed += 1
        return changed

    async def _drift_mood(self) -> int:
        current = await get_mood()
        next_value = max(
            MOOD_MIN, min(MOOD_MAX, current + self.rng.uniform(-0.2, 0.2))
        )
        await set_mood(round(next_value, 3))
        return 1


def _log_error(msg: str) -> None:
    with contextlib.suppress(Exception):
        from astrbot.api import logger  # type: ignore

        logger.error(f"[unified_chat] learning-jobs {msg}", exc_info=True)
