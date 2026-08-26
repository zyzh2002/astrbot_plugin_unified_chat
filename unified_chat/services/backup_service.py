"""SQLite database backups with retention (fail-silent)."""

from __future__ import annotations

import contextlib
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

_BACKUP_DIR = "backups"
_STAMP_RE = re.compile(r"^\w+-\d{8}-\d{6}$")


class BackupService:
    """Copies the live plugin DB via the sqlite backup API."""

    def __init__(self, config: Any, db_path: Path):
        self.config = config
        self.db_path = Path(db_path)

    @property
    def backup_root(self) -> Path:
        return self.db_path.parent / _BACKUP_DIR

    def run_backup(self, reason: str = "manual") -> Path | None:
        """Snapshot the DB; returns backup dir or None on failure."""
        try:
            if not self.db_path.exists():
                return None
            stamp = time.strftime("%Y%m%d-%H%M%S")
            safe_reason = re.sub(r"[^A-Za-z0-9_-]", "", reason)[:24] or "backup"
            dest_dir = self.backup_root / f"{safe_reason}-{stamp}"
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / self.db_path.name

            src = sqlite3.connect(str(self.db_path))
            try:
                dst = sqlite3.connect(str(dest))
                try:
                    src.backup(dst)
                finally:
                    dst.close()
            finally:
                src.close()
            self._prune()
            return dest_dir
        except Exception:
            self._log_error("run_backup")
            return None

    def _prune(self) -> None:
        keep_last = int(getattr(self.config, "backup_keep_last", 10) or 10)
        if keep_last <= 0:
            return
        entries = sorted(
            (
                entry
                for entry in self.backup_root.iterdir()
                if entry.is_dir() and _STAMP_RE.match(entry.name)
            ),
            key=lambda entry: entry.stat().st_mtime,
        )
        excess = len(entries) - keep_last
        for entry in entries[: max(0, excess)]:
            with contextlib.suppress(Exception):
                import shutil

                shutil.rmtree(entry, ignore_errors=True)

    def list_backups(self) -> list[str]:
        if not self.backup_root.is_dir():
            return []
        return sorted(
            entry.name
            for entry in self.backup_root.iterdir()
            if entry.is_dir() and _STAMP_RE.match(entry.name)
        )

    async def maybe_backup_version(self, version: str) -> bool:
        """Backup once per plugin version; returns True when taken."""
        from ..storage import kv as kv_store

        try:
            previous = await kv_store.kv_get("last_backup_version")
            if previous == version:
                return False
            result = self.run_backup(f"v{version}")
            if result is not None:
                await kv_store.kv_set("last_backup_version", version)
                return True
        except Exception:
            self._log_error("maybe_backup_version")
        return False

    async def daily_tick(self) -> bool:
        """Run one scheduled daily backup."""
        return self.run_backup("daily") is not None

    @staticmethod
    def _log_error(msg: str) -> None:
        with contextlib.suppress(Exception):
            from astrbot.api import logger  # type: ignore

            logger.error(f"[unified_chat] backup {msg}", exc_info=True)
