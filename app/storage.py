from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import IncomingMessage, ManualSellerMessage, NeedState


class Store:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init(self) -> None:
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    message_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    buyer_id TEXT NOT NULL,
                    buyer_name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    listing_id TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL
                )
                """
            )

    def has_message(self, message_id: str) -> bool:
        with self._connect() as db:
            row = db.execute(
                "SELECT 1 FROM messages WHERE message_id = ?",
                (message_id,),
            ).fetchone()
            return row is not None

    def add_incoming(self, message: IncomingMessage) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT OR IGNORE INTO messages (
                    message_id, conversation_id, buyer_id, buyer_name,
                    role, text, listing_id, created_at
                ) VALUES (?, ?, ?, ?, 'buyer', ?, ?, ?)
                """,
                (
                    message.message_id,
                    message.conversation_id,
                    message.buyer_id,
                    message.buyer_name,
                    message.text,
                    message.listing_id,
                    message.created_at,
                ),
            )

    def add_assistant_reply(self, conversation_id: str, text: str, created_at: str) -> None:
        message_id = f"assistant:{conversation_id}:{created_at}"
        with self._connect() as db:
            db.execute(
                """
                INSERT OR IGNORE INTO messages (
                    message_id, conversation_id, buyer_id, buyer_name,
                    role, text, listing_id, created_at
                ) VALUES (?, ?, '', '', 'assistant', ?, '', ?)
                """,
                (message_id, conversation_id, text, created_at),
            )

    def add_seller_message(self, message: ManualSellerMessage) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT OR IGNORE INTO messages (
                    message_id, conversation_id, buyer_id, buyer_name,
                    role, text, listing_id, created_at
                ) VALUES (?, ?, '', '', 'seller', ?, ?, ?)
                """,
                (
                    message.message_id,
                    message.conversation_id,
                    message.text,
                    message.listing_id,
                    message.created_at,
                ),
            )

    def history(self, conversation_id: str) -> list[tuple[str, str]]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT role, text FROM messages
                WHERE conversation_id = ?
                ORDER BY created_at ASC
                """,
                (conversation_id,),
            ).fetchall()
        return [(row["role"], row["text"]) for row in rows]

    def history_records(self, conversation_id: str) -> list[tuple[str, str, str]]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT role, text, created_at FROM messages
                WHERE conversation_id = ?
                ORDER BY created_at ASC
                """,
                (conversation_id,),
            ).fetchall()
        return [(row["role"], row["text"], row["created_at"]) for row in rows]

    def state(self, conversation_id: str) -> NeedState:
        with self._connect() as db:
            row = db.execute(
                "SELECT state_json FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        if not row:
            return NeedState()
        return NeedState.from_dict(json.loads(row["state_json"]))

    def save_state(self, conversation_id: str, state: NeedState) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO conversations (conversation_id, state_json)
                VALUES (?, ?)
                ON CONFLICT(conversation_id) DO UPDATE SET state_json = excluded.state_json
                """,
                (conversation_id, json.dumps(state.to_dict(), ensure_ascii=False)),
            )
