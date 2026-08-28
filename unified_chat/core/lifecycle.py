"""Plugin lifecycle: init, message handling, llm hook, migration."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent
    from astrbot.api.star import Context, Star

from ..config import PluginConfig


class PluginLifecycle:
    """Orchestrates all internal services. Keeps main.py thin."""

    def __init__(
        self,
        plugin: Star,
        context: Context,
        raw_config: dict | None = None,
    ):
        self.plugin = plugin
        self.context = context
        self._raw_config = raw_config
        self._status = "created"
        self._config: PluginConfig | None = None
        self._data_dir: Path | None = None
        self._rag_service: Any | None = None
        self._chat_service: Any | None = None
        self._pipeline: Any | None = None
        self._memory_service: Any | None = None
        self._cron: Any | None = None
        self._learning_service: Any | None = None
        self._migration_service: Any | None = None
        self._migration_tasks: list[Any] = []
        self._needs_migration = False
        self._prefetch_task: Any | None = None
        self._backup_service: Any | None = None
        self._humanize: Any | None = None
        self._proactive: Any | None = None
        self._learning_jobs: Any | None = None

    async def on_load(self):
        try:
            raw = self._raw_config
            if raw is None:
                candidate = getattr(self.plugin, "config", None)
                if not isinstance(candidate, dict):
                    candidate = getattr(self.context, "get_config", lambda: {})()
                raw = candidate if isinstance(candidate, dict) else {}

            from ..utils.path import resolve_data_dir

            data_dir = resolve_data_dir(raw, self.context)
            config = PluginConfig.from_dict(raw, data_dir=str(data_dir))
            self._config = config
            self._data_dir = data_dir

            from ..services.backup_service import BackupService
            from ..storage.database import get_engine

            db_path = data_dir / "unified_chat.db"
            self._backup_service = BackupService(config, db_path)
            await get_engine(
                db_path,
                before_migrate=lambda: self._backup_service.run_backup("schema_v1"),
            )
            self._status = "loaded"

            from ..services.rag_service import RagService

            self._rag_service = RagService(self.context)

            from ..services.chat_service import ChatService
            from .pipeline import MessagePipeline

            self._chat_service = ChatService()

            from ..services.memory_service import MemoryService

            self._memory_service = MemoryService(self.context, config)
            await self._memory_service.ensure_memory_kb()

            from ..services.learning_service import LearningService

            self._learning_service = LearningService(
                self.context,
                config,
                self._memory_service.store_atom,
            )

            self._pipeline = MessagePipeline(
                config, self._chat_service, self._memory_service, self._learning_service
            )

            from ..services.humanize_service import HumanizeService

            self._humanize = HumanizeService(self.context, config)

            from ..services.humanize_proactive import ProactiveService

            self._proactive = ProactiveService(self.context, config)

            from ..services.learning_jobs import DailyLearningJobs
            from .cron import MemoryCleanupCron

            self._learning_jobs = DailyLearningJobs(
                self.context,
                config,
                self._memory_service,
            )
            self._cron = MemoryCleanupCron(
                self._memory_service,
                backup_service=self._backup_service,
                learning_jobs=self._learning_jobs,
                config=config,
            )
            self._cron.start()
            if config.humanize_proactive:
                self._proactive.start()

            from ..storage import kv as kv_store

            # Persist the embedding snapshot only on first boot; afterwards it
            # is refreshed by a successful memory-KB migration, so a provider
            # change keeps signalling needs_migration until actually migrated.
            snapshot = await kv_store.kv_get("embedding_provider_snapshot")
            self._needs_migration = bool(
                snapshot is not None
                and config.embedding_provider_id
                and snapshot != config.embedding_provider_id
            )
            if snapshot is None:
                await kv_store.kv_set(
                    "embedding_provider_snapshot", config.embedding_provider_id
                )

            from ..services.migration_service import MigrationService

            self._migration_service = MigrationService(self.context, config)
            try:
                # crash leftovers would block future migrations forever
                for key in await kv_store.kv_keys_with_prefix("migration:"):
                    if key.endswith(":running"):
                        await kv_store.kv_delete(key)
            except Exception:
                pass

            try:
                from ..native.bootstrap import plugin_version

                await self._backup_service.maybe_backup_version(plugin_version())
            except Exception:
                pass

            try:
                from ..native import bootstrap

                self._prefetch_task = bootstrap.prefetch_async(
                    config.native_autodownload
                )
            except Exception:
                pass
        except Exception as e:  # pragma: no cover - defensive
            try:
                from astrbot.api import logger  # type: ignore

                logger.error(f"[unified_chat] on_load failed: {e}", exc_info=True)
            except Exception:
                pass
            self._status = f"load_failed: {e}"

    async def on_unload(self):
        with contextlib.suppress(Exception):
            if self._cron is not None:
                await self._cron.stop()
        with contextlib.suppress(Exception):
            if self._proactive is not None:
                await self._proactive.stop()
        with contextlib.suppress(Exception):
            if self._pipeline is not None:
                await self._pipeline.shutdown()
        migration_tasks = [task for task in self._migration_tasks if not task.done()]
        for task in migration_tasks:
            task.cancel()
        if migration_tasks:
            with contextlib.suppress(Exception):
                await asyncio.gather(*migration_tasks, return_exceptions=True)
        if self._prefetch_task is not None:
            with contextlib.suppress(Exception):
                self._prefetch_task.cancel()
                await asyncio.gather(self._prefetch_task, return_exceptions=True)
            self._prefetch_task = None
        with contextlib.suppress(Exception):
            from ..storage.database import close_engine

            await close_engine()
        self._status = "unloaded"

    async def handle_message(self, event: AstrMessageEvent):
        if self._pipeline is None:
            return
        if self._config is not None:
            # blacklist and blocked keywords are unconditional (documented in
            # the config schema), independent of the humanize toggle
            try:
                from ..services.humanize_service import (
                    blocked_keyword_hit,
                    is_blacklisted,
                )

                if is_blacklisted(event, self._config) or blocked_keyword_hit(
                    event, self._config
                ):
                    event.stop_event()
                    return
            except Exception:
                with contextlib.suppress(Exception):
                    from astrbot.api import logger  # type: ignore

                    logger.error("[unified_chat] pre-filter failed", exc_info=True)
        if self._humanize is not None and getattr(
            self._config, "humanize_enable", False
        ):
            try:
                if self._humanize.blocked_keyword_hit(event):
                    event.stop_event()
                    return
                outcome = await self._humanize.process(event)
                if not outcome.allow:
                    event.stop_event()
                    if outcome.reason == "blacklisted":
                        return  # blacklisted users leave no trace
                elif outcome.merged_context:
                    event._unified_chat_merge = outcome.merged_context
            except Exception:
                with contextlib.suppress(Exception):
                    from astrbot.api import logger  # type: ignore

                    logger.error("[unified_chat] humanize gate failed", exc_info=True)
        try:
            await self._pipeline.process(event)
        except Exception:
            with contextlib.suppress(Exception):
                from astrbot.api import logger  # type: ignore

                logger.error("[unified_chat] handle_message failed", exc_info=True)

    async def handle_llm_request(self, event: AstrMessageEvent, req):
        if self._config is None:
            return
        if self._rag_service is not None:
            try:
                from .hooks import inject_kb_tool

                await inject_kb_tool(event, req, self._config, self._rag_service)
            except Exception:
                with contextlib.suppress(Exception):
                    from astrbot.api import logger  # type: ignore

                    logger.error("[unified_chat] handle_llm_request failed", exc_info=True)
        if self._chat_service is not None:
            try:
                from .hooks import inject_social_context

                await inject_social_context(event, req, self._config, self._chat_service)
                merged = getattr(event, "_unified_chat_merge", "")
                with contextlib.suppress(Exception):
                    delattr(event, "_unified_chat_merge")
                if merged:
                    contexts = getattr(req, "contexts", None)
                    if contexts is None:
                        contexts = []
                        req.contexts = contexts
                    contexts.append({"role": "system", "content": merged})
            except Exception:
                with contextlib.suppress(Exception):
                    from astrbot.api import logger  # type: ignore

                    logger.error("[unified_chat] inject_social_context failed", exc_info=True)
        if self._memory_service is not None:
            try:
                from .hooks import inject_memory_tools

                await inject_memory_tools(event, req, self._config, self._memory_service)
            except Exception:
                with contextlib.suppress(Exception):
                    from astrbot.api import logger  # type: ignore

                    logger.error("[unified_chat] inject_memory_tools failed", exc_info=True)
            try:
                from .hooks import inject_memories

                await inject_memories(event, req, self._config, self._memory_service)
                await self._inject_learning_block(event, req)
            except Exception:
                with contextlib.suppress(Exception):
                    from astrbot.api import logger  # type: ignore

                    logger.error("[unified_chat] inject_memories failed", exc_info=True)

    async def _inject_learning_block(self, event: AstrMessageEvent, req) -> None:
        if not getattr(self._config, "enable_style_learning", True):
            return
        try:
            from ..services.learning_jobs import get_mood
            from ..storage import repo as repos
            from .hooks import inject_learning_block

            umo = getattr(event, "unified_msg_origin", "") or ""
            sender = ""
            with contextlib.suppress(Exception):
                sender = str(event.get_sender_id() or "")
            slang_terms = await repos.SlangRepo.confirmed_for_umo(umo, limit=100)
            affinity = None
            if getattr(self._config, "enable_affinity", True) and umo and sender:
                affinity = await repos.AffinityLookupRepo.get_score(umo, sender)
            mood = await get_mood() if self._config else 0.0
            await inject_learning_block(
                event,
                req,
                self._config,
                {
                    "slang_terms": slang_terms,
                    "affinity_score": affinity,
                    "mood_scalar": mood,
                },
            )
        except Exception:
            with contextlib.suppress(Exception):
                from astrbot.api import logger  # type: ignore

                logger.error("[unified_chat] learning block failed", exc_info=True)

    async def umem(self, event: AstrMessageEvent, action: str = "", arg: str = "") -> str:
        """Handle /umem subcommands. Returns plain text reply."""
        if self._memory_service is None or self._config is None:
            return "[umem] Plugin not initialized"
        action = (action or "").strip().lower()
        arg = (arg or "").strip()
        session_id = self._memory_service.session_id_for(event)
        if action in ("", "help"):
            return (
                "[umem] Usage:\n"
                "/umem status - counts by type\n"
                "/umem search <query> - hybrid search\n"
                "/umem forget <id> - delete one memory\n"
                "/umem backup - take a DB backup now\n"
                "/umem reset - clear this session's memories"
            )
        try:
            if action == "status":
                from ..storage import repo as repos

                by_type = await repos.MemoryAdminRepo.count_by_type()
                total = sum(by_type.values()) or 0
                parts = [f"{k}={v}" for k, v in sorted(by_type.items())] or ["empty"]
                backups = (
                    len(self._backup_service.list_backups())
                    if self._backup_service is not None
                    else 0
                )
                return f"[umem] total={total} | {' '.join(parts)} | backups={backups}"
            if action == "search":
                if not arg:
                    return "[umem] Usage: /umem search <query>"
                hits = await self._memory_service.retrieve_hybrid(
                    arg,
                    session_id=session_id,
                    top_k=5,
                )
                if not hits:
                    return "[umem] no matches"
                return "\n".join(f"[{m.id}] ({m.memory_type}) {m.content}" for m in hits)
            if action == "forget":
                if not arg.isdigit():
                    return "[umem] Usage: /umem forget <id>"
                removed = await self._memory_service.forget(int(arg), session_id)
                return f"[umem] deleted {removed}"
            if action == "backup":
                if self._backup_service is None:
                    return "[umem] backup unavailable"
                import asyncio

                dest = await asyncio.to_thread(self._backup_service.run_backup, "manual")
                return f"[umem] backup -> {dest.name}" if dest else "[umem] backup failed"
            if action == "reset":
                if not session_id:
                    return (
                        "[umem] reset requires memory_session_isolation; "
                        "refusing to clear the shared memory pool"
                    )
                removed = await self._memory_service.forget_session(session_id)
                return f"[umem] cleared {removed} memories for this session"
            return "[umem] unknown action; try /umem help"
        except Exception as exc:  # pragma: no cover - defensive
            return f"[umem] error: {exc}"

    def get_status(self) -> str:
        if self._config is not None and self._data_dir is not None:
            return (
                f"{self._status} | data_dir={self._data_dir} | "
                f"rag_kbs={self._config.rag_kbs} agentic={self._config.rag_agentic} "
                f"mem_days={self._config.memory_cleanup_days}"
            )
        return self._status

    async def get_status_async(self) -> str:
        parts = [f"status={self._status}"]
        if self._data_dir is not None:
            parts.append(f"data_dir={self._data_dir}")
        if self._config is not None:
            parts.append(
                f"rag_kbs={self._config.rag_kbs} agentic={self._config.rag_agentic} "
                f"mem_days={self._config.memory_cleanup_days}"
            )
            parts.append(f"needs_migration={'yes' if self._needs_migration else 'no'}")
        try:
            from ..storage import repo as repos

            memories = await repos.MemoryRepo.count()
            messages = await repos.MessageRepo.count()
            f_count = await repos.LearningLogRepo.count_by_stage("filter")
            r_count = await repos.LearningLogRepo.count_by_stage("refine")
            rf_count = await repos.LearningLogRepo.count_by_stage("reinforce")
            parts.append(
                f"memories={memories} messages={messages} "
                f"learning(filter={f_count},refine={r_count},reinforce={rf_count})"
            )
        except Exception:
            parts.append("counts=n/a")
        try:
            from ..storage import kv as kv_store

            results = []
            for key in await kv_store.kv_keys_with_prefix("migration:"):
                if not key.endswith(":last_result"):
                    continue
                value = await kv_store.kv_get(key)
                kb = key[len("migration:") : -len(":last_result")]
                if value:
                    results.append(f"{kb}: {value}")
            if results:
                parts.append("migration_last=" + " | ".join(results))
        except Exception:
            pass
        return " | ".join(parts)

    async def migrate_kb(self, event: AstrMessageEvent, kb_name: str) -> str:
        if not kb_name:
            return "Usage: /unified_migrate <kb_name>"
        if self._migration_service is None:
            return "Plugin not initialized"
        if await self._migration_service.is_running(kb_name):
            return f"Migration for '{kb_name}' is already running."
        task = asyncio.create_task(
            self._migration_service.run_migration(kb_name),
            name="unified_chat_migration",
        )
        task.add_done_callback(self._log_migration_done)
        self._migration_tasks.append(task)
        return f"Migration for '{kb_name}' started in background. Check /unified_status."

    def _log_migration_done(self, task: asyncio.Task) -> None:
        self._migration_tasks = [t for t in self._migration_tasks if not t.done()]
        if task.cancelled():
            return
        with contextlib.suppress(Exception):
            exc = task.exception()
            if exc is not None:
                from astrbot.api import logger  # type: ignore

                logger.error(f"[unified_chat] migration task failed: {exc}")
            else:
                result = task.result()
                if result:
                    from astrbot.api import logger  # type: ignore

                    logger.info(f"[unified_chat] migration finished: {result}")

    async def uslang(self, action: str = "", arg: str = "") -> str:
        """Handle /uslang subcommands."""
        from ..storage import repo as repos

        action = (action or "").strip().lower()
        arg = (arg or "").strip()
        if action in ("", "help"):
            return "[uslang] Usage: list | confirm <id> | deny <id>"
        try:
            if action == "list":
                pending = await repos.SlangRepo.list_by_status("candidate", limit=15)
                confirmed = await repos.SlangRepo.list_by_status("confirmed", limit=15)
                pl = "\n".join(
                    f"[{t.id}] {t.term} (x{t.count}) {t.meaning[:40]}" for t in pending
                )
                cl = "\n".join(
                    f"[{t.id}] {t.term}: {t.meaning[:40]}" for t in confirmed
                )
                return (
                    f"[uslang] candidates:\n{pl or '(none)'}"
                    f"\nconfirmed:\n{cl or '(none)'}"
                )
            if action in ("confirm", "deny") and arg.isdigit():
                status = "confirmed" if action == "confirm" else "denied"
                await repos.SlangRepo.set_status(int(arg), status)
                return f"[uslang] term {arg} -> {status}"
            return "[uslang] unknown action"
        except Exception as exc:
            return f"[uslang] error: {exc}"

    async def upersona(self, action: str = "", arg: str = "") -> str:
        """Handle /upersona review chain."""
        from ..services.persona_review import PersonaReviewService

        action = (action or "").strip().lower()
        arg = (arg or "").strip()
        try:
            if action in ("", "list"):
                items = await PersonaReviewService.list_pending()
                if not items:
                    return "[upersona] no pending suggestions"
                lines = "\n".join(
                    f"[{i['id']}] {i['created_at']} {i['text'][:80]}" for i in items
                )
                return f"[upersona] pending:\n{lines}\napprove <id> returns full text."
            if action == "approve" and arg:
                ok, text = await PersonaReviewService.resolve(arg, True)
                if not ok:
                    return "[upersona] id not found"
                return f"[upersona] approved; paste into persona editor:\n{text}"
            if action == "reject" and arg:
                ok, _ = await PersonaReviewService.resolve(arg, False)
                return "[upersona] rejected" if ok else "[upersona] id not found"
            return "[upersona] Usage: list | approve <id> | reject <id>"
        except Exception as exc:
            return f"[upersona] error: {exc}"
