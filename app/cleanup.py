from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Settings


DEBUG_FILE_PATTERNS = (
    "xianyu_ws_events.log",
    "xianyu_last_ws_event.json",
    "xianyu_last_unparsed*.json",
    "xianyu_last_unparsed*.txt",
    "xianyu_recent_conversations.json",
    "xianyu_history_*.json",
    "xianyu_login_debug.json",
)
DEFAULT_MESSAGE_DAYS = 3
DEFAULT_DEBUG_DAYS = 3
AUTO_CLEANUP_INTERVAL_SECONDS = 24 * 60 * 60


@dataclass
class CleanupResult:
    dry_run: bool
    db_path: Path
    matched_messages: int = 0
    deleted_messages: int = 0
    matched_states: int = 0
    deleted_states: int = 0
    matched_debug_files: int = 0
    deleted_debug_files: int = 0
    matched_debug_bytes: int = 0
    deleted_debug_bytes: int = 0
    vacuumed: bool = False

    def report(self) -> str:
        mode = "DRY RUN" if self.dry_run else "DONE"
        lines = [
            f"Cleanup {mode}",
            f"database: {self.db_path}",
            f"messages matched/deleted: {self.matched_messages}/{self.deleted_messages}",
            f"conversation states matched/deleted: {self.matched_states}/{self.deleted_states}",
            (
                "debug files matched/deleted: "
                f"{self.matched_debug_files}/{self.deleted_debug_files} "
                f"({format_bytes(self.deleted_debug_bytes)} deleted)"
            ),
        ]
        if self.vacuumed:
            lines.append("database vacuumed: yes")
        return "\n".join(lines)


def cleanup_data(
    settings: Settings,
    *,
    message_days: int = DEFAULT_MESSAGE_DAYS,
    debug_days: int = DEFAULT_DEBUG_DAYS,
    all_messages: bool = False,
    clear_states: bool = False,
    skip_debug: bool = False,
    dry_run: bool = False,
) -> CleanupResult:
    if message_days < 0:
        raise ValueError("message_days must be >= 0")
    if debug_days < 0:
        raise ValueError("debug_days must be >= 0")

    result = CleanupResult(dry_run=dry_run, db_path=settings.db_path)
    cleanup_database(
        settings.db_path,
        result,
        message_days=message_days,
        all_messages=all_messages,
        clear_states=clear_states,
        dry_run=dry_run,
    )
    if not skip_debug:
        cleanup_debug_files(
            settings.db_path.parent,
            result,
            debug_days=debug_days,
            dry_run=dry_run,
        )
    return result


def cleanup_database(
    db_path: Path,
    result: CleanupResult,
    *,
    message_days: int,
    all_messages: bool,
    clear_states: bool,
    dry_run: bool,
) -> None:
    if not db_path.exists():
        return

    cutoff = datetime.now(timezone.utc) - timedelta(days=message_days)
    message_ids: list[str] = []
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT message_id, created_at FROM messages").fetchall()
        for row in rows:
            if all_messages or is_older_than(row["created_at"], cutoff):
                message_ids.append(row["message_id"])
        result.matched_messages = len(message_ids)

        if clear_states:
            state_row = db.execute("SELECT COUNT(*) AS count FROM conversations").fetchone()
            result.matched_states = int(state_row["count"] if state_row else 0)

        if dry_run:
            return

        delete_message_ids(db, message_ids)
        result.deleted_messages = len(message_ids)

        if clear_states:
            db.execute("DELETE FROM conversations")
            result.deleted_states = result.matched_states

    if not dry_run and (result.deleted_messages or result.deleted_states):
        with sqlite3.connect(db_path) as db:
            db.execute("VACUUM")
        result.vacuumed = True


def delete_message_ids(db: sqlite3.Connection, message_ids: list[str]) -> None:
    for index in range(0, len(message_ids), 500):
        chunk = message_ids[index : index + 500]
        db.executemany("DELETE FROM messages WHERE message_id = ?", [(item,) for item in chunk])


def cleanup_debug_files(
    data_dir: Path,
    result: CleanupResult,
    *,
    debug_days: int,
    dry_run: bool,
) -> None:
    cutoff_timestamp = (datetime.now(timezone.utc) - timedelta(days=debug_days)).timestamp()
    for path in iter_debug_files(data_dir):
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_mtime > cutoff_timestamp:
            continue
        result.matched_debug_files += 1
        result.matched_debug_bytes += stat.st_size
        if dry_run:
            continue
        try:
            path.unlink()
        except OSError:
            continue
        result.deleted_debug_files += 1
        result.deleted_debug_bytes += stat.st_size


def iter_debug_files(data_dir: Path):
    seen: set[Path] = set()
    for pattern in DEBUG_FILE_PATTERNS:
        for path in data_dir.glob(pattern):
            resolved = path.resolve()
            if resolved in seen or not path.is_file():
                continue
            seen.add(resolved)
            yield path


def is_older_than(value: str, cutoff: datetime) -> bool:
    parsed = parse_datetime(value)
    return parsed is not None and parsed < cutoff


def parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed.astimezone(timezone.utc)


def format_bytes(value: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f}{unit}" if unit != "B" else f"{int(size)}B"
        size /= 1024
    return f"{value}B"
