"""SQLite-хранилище: слоты, сообщения слотов, опубликованные посты."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from app.models import (
    OPEN_STATUSES,
    Suggestion,
    FallbackMode,
    MsgKind,
    Post,
    Role,
    Slot,
    SlotKind,
    SlotMessage,
    SlotStatus,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS slots (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    kind             TEXT NOT NULL,
    status           TEXT NOT NULL,
    theme            TEXT NOT NULL DEFAULT 'free',
    scheduled_for    TEXT,
    assigned_user_id INTEGER,
    current_draft    TEXT,
    draft_fallback   TEXT NOT NULL DEFAULT 'normal',
    preview_chat_id  INTEGER,
    preview_msg_ids  TEXT NOT NULL DEFAULT '[]',
    reminder_msgs    TEXT NOT NULL DEFAULT '{}',
    llm_error_count  INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_slots_scheduled_for
    ON slots(scheduled_for) WHERE scheduled_for IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_slots_status ON slots(status);

CREATE TABLE IF NOT EXISTS slot_messages (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_id        INTEGER NOT NULL REFERENCES slots(id) ON DELETE CASCADE,
    role           TEXT NOT NULL,
    kind           TEXT NOT NULL,
    user_id        INTEGER,
    tg_message_id  INTEGER,
    media_group_id TEXT,
    text           TEXT,
    file_id        TEXT,
    file_unique_id TEXT,
    width          INTEGER,
    height         INTEGER,
    processed      INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_msgs_slot ON slot_messages(slot_id, id);

CREATE TABLE IF NOT EXISTS posts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_id        INTEGER NOT NULL REFERENCES slots(id),
    channel_id     INTEGER NOT NULL,
    message_id     INTEGER NOT NULL,
    message_ids    TEXT NOT NULL,
    html           TEXT NOT NULL,
    plain_text     TEXT NOT NULL,
    photo_file_ids TEXT NOT NULL,
    fallback_mode  TEXT NOT NULL,
    published_by   INTEGER NOT NULL,
    published_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS suggestions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_id    INTEGER NOT NULL REFERENCES slots(id) ON DELETE CASCADE,
    idx        INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    ref        TEXT NOT NULL,
    title      TEXT NOT NULL,
    line       TEXT NOT NULL DEFAULT '',
    url        TEXT NOT NULL DEFAULT '',
    image_url  TEXT,
    payload    TEXT NOT NULL DEFAULT '{}',
    picked     INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_suggestions_slot ON suggestions(slot_id, idx);
CREATE INDEX IF NOT EXISTS ix_suggestions_picked ON suggestions(kind, picked, created_at);
"""

# Колонки, добавленные после первой версии схемы: (таблица, колонка, DDL)
MIGRATIONS = [
    ("slots", "theme", "ALTER TABLE slots ADD COLUMN theme TEXT NOT NULL DEFAULT 'free'"),
]


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._conn: aiosqlite.Connection | None = None
        self.slots = SlotRepo(self)
        self.messages = MessageRepo(self)
        self.posts = PostRepo(self)
        self.suggestions = SuggestionRepo(self)

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "Database.connect() не вызван"
        return self._conn

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(SCHEMA)
        await self._migrate()
        await self._conn.commit()

    async def _migrate(self) -> None:
        for table, column, ddl in MIGRATIONS:
            cur = await self.conn.execute(f"PRAGMA table_info({table})")
            columns = {row["name"] for row in await cur.fetchall()}
            if column not in columns:
                await self.conn.execute(ddl)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None


class SlotRepo:
    def __init__(self, db: Database):
        self.db = db

    async def create(
        self,
        kind: SlotKind,
        status: SlotStatus,
        scheduled_for: str | None,
        assigned_user_id: int | None = None,
        theme: str = "free",
    ) -> Slot | None:
        """Создаёт слот. Возвращает None, если слот на эту дату уже есть (уникальный индекс)."""
        ts = now_iso()
        try:
            cur = await self.db.conn.execute(
                "INSERT INTO slots(kind, status, theme, scheduled_for, assigned_user_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (kind.value, status.value, theme, scheduled_for, assigned_user_id, ts, ts),
            )
        except sqlite3.IntegrityError:
            return None
        await self.db.conn.commit()
        return await self.get(cur.lastrowid)

    async def get(self, slot_id: int) -> Slot | None:
        cur = await self.db.conn.execute("SELECT * FROM slots WHERE id = ?", (slot_id,))
        row = await cur.fetchone()
        return Slot.from_row(row) if row else None

    async def get_open(self) -> Slot | None:
        placeholders = ",".join("?" for _ in OPEN_STATUSES)
        cur = await self.db.conn.execute(
            f"SELECT * FROM slots WHERE status IN ({placeholders}) ORDER BY id DESC LIMIT 1",
            tuple(s.value for s in OPEN_STATUSES),
        )
        row = await cur.fetchone()
        return Slot.from_row(row) if row else None

    async def list_open(self) -> list[Slot]:
        placeholders = ",".join("?" for _ in OPEN_STATUSES)
        cur = await self.db.conn.execute(
            f"SELECT * FROM slots WHERE status IN ({placeholders}) ORDER BY id",
            tuple(s.value for s in OPEN_STATUSES),
        )
        return [Slot.from_row(r) for r in await cur.fetchall()]

    async def exists_for_date(self, scheduled_for: str) -> bool:
        cur = await self.db.conn.execute(
            "SELECT 1 FROM slots WHERE scheduled_for = ? LIMIT 1", (scheduled_for,)
        )
        return await cur.fetchone() is not None

    async def update(self, slot_id: int, **fields) -> None:
        if not fields:
            return
        encoded = {}
        for key, value in fields.items():
            if key in ("preview_msg_ids", "reminder_msgs"):
                value = json.dumps(value)
            elif isinstance(value, (SlotStatus, SlotKind, FallbackMode)):
                value = value.value
            encoded[key] = value
        encoded["updated_at"] = now_iso()
        assignments = ", ".join(f"{k} = ?" for k in encoded)
        await self.db.conn.execute(
            f"UPDATE slots SET {assignments} WHERE id = ?", (*encoded.values(), slot_id)
        )
        await self.db.conn.commit()

    async def set_status(self, slot_id: int, status: SlotStatus, **fields) -> None:
        await self.update(slot_id, status=status, **fields)

    async def increment_llm_errors(self, slot_id: int) -> int:
        await self.db.conn.execute(
            "UPDATE slots SET llm_error_count = llm_error_count + 1, updated_at = ? WHERE id = ?",
            (now_iso(), slot_id),
        )
        await self.db.conn.commit()
        slot = await self.get(slot_id)
        return slot.llm_error_count if slot else 0


class MessageRepo:
    def __init__(self, db: Database):
        self.db = db

    async def add(
        self,
        slot_id: int,
        role: Role,
        kind: MsgKind,
        *,
        user_id: int | None = None,
        tg_message_id: int | None = None,
        media_group_id: str | None = None,
        text: str | None = None,
        file_id: str | None = None,
        file_unique_id: str | None = None,
        width: int | None = None,
        height: int | None = None,
        processed: bool = False,
    ) -> int:
        cur = await self.db.conn.execute(
            "INSERT INTO slot_messages(slot_id, role, kind, user_id, tg_message_id, media_group_id, "
            "text, file_id, file_unique_id, width, height, processed, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                slot_id, role.value, kind.value, user_id, tg_message_id, media_group_id,
                text, file_id, file_unique_id, width, height, int(processed), now_iso(),
            ),
        )
        await self.db.conn.commit()
        return cur.lastrowid

    async def list_for_slot(self, slot_id: int) -> list[SlotMessage]:
        cur = await self.db.conn.execute(
            "SELECT * FROM slot_messages WHERE slot_id = ? ORDER BY id", (slot_id,)
        )
        return [SlotMessage.from_row(r) for r in await cur.fetchall()]

    async def unprocessed_user_ids(self, slot_id: int) -> list[int]:
        cur = await self.db.conn.execute(
            "SELECT id FROM slot_messages WHERE slot_id = ? AND role = 'user' AND processed = 0 ORDER BY id",
            (slot_id,),
        )
        return [r["id"] for r in await cur.fetchall()]

    async def mark_processed(self, ids: list[int], processed: bool = True) -> None:
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        await self.db.conn.execute(
            f"UPDATE slot_messages SET processed = ? WHERE id IN ({placeholders})",
            (int(processed), *ids),
        )
        await self.db.conn.commit()

    async def photos_for_slot(self, slot_id: int) -> list[SlotMessage]:
        """Фото слота в порядке отправки в Telegram (порядок апдейтов не гарантирован)."""
        cur = await self.db.conn.execute(
            "SELECT * FROM slot_messages WHERE slot_id = ? AND kind = 'photo' "
            "ORDER BY COALESCE(tg_message_id, id), id",
            (slot_id,),
        )
        return [SlotMessage.from_row(r) for r in await cur.fetchall()]

    async def count_user_messages(self, slot_id: int) -> int:
        cur = await self.db.conn.execute(
            "SELECT COUNT(*) AS c FROM slot_messages WHERE slot_id = ? AND role = 'user'", (slot_id,)
        )
        row = await cur.fetchone()
        return row["c"]


class PostRepo:
    def __init__(self, db: Database):
        self.db = db

    async def add(
        self,
        *,
        slot_id: int,
        channel_id: int,
        message_ids: list[int],
        html: str,
        plain_text: str,
        photo_file_ids: list[str],
        fallback_mode: FallbackMode,
        published_by: int,
    ) -> int:
        cur = await self.db.conn.execute(
            "INSERT INTO posts(slot_id, channel_id, message_id, message_ids, html, plain_text, "
            "photo_file_ids, fallback_mode, published_by, published_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                slot_id, channel_id, message_ids[0], json.dumps(message_ids), html, plain_text,
                json.dumps(photo_file_ids), fallback_mode.value, published_by, now_iso(),
            ),
        )
        await self.db.conn.commit()
        return cur.lastrowid

    async def last(self, n: int) -> list[Post]:
        cur = await self.db.conn.execute(
            "SELECT * FROM posts ORDER BY id DESC LIMIT ?", (n,)
        )
        return [Post.from_row(r) for r in await cur.fetchall()]

    async def count(self) -> int:
        cur = await self.db.conn.execute("SELECT COUNT(*) AS c FROM posts")
        row = await cur.fetchone()
        return row["c"]


class SuggestionRepo:
    def __init__(self, db: Database):
        self.db = db

    async def add_batch(self, slot_id: int, items: list[Suggestion], start_idx: int = 1) -> list[Suggestion]:
        saved: list[Suggestion] = []
        for offset, item in enumerate(items):
            idx = start_idx + offset
            cur = await self.db.conn.execute(
                "INSERT INTO suggestions(slot_id, idx, kind, ref, title, line, url, image_url, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    slot_id, idx, item.kind, item.ref, item.title, item.line, item.url,
                    item.image_url, json.dumps(item.payload, ensure_ascii=False), now_iso(),
                ),
            )
            saved.append(Suggestion(
                kind=item.kind, ref=item.ref, title=item.title, line=item.line, url=item.url,
                image_url=item.image_url, payload=item.payload, id=cur.lastrowid, slot_id=slot_id, idx=idx,
            ))
        await self.db.conn.commit()
        return saved

    async def get(self, slot_id: int, idx: int) -> Suggestion | None:
        cur = await self.db.conn.execute(
            "SELECT * FROM suggestions WHERE slot_id = ? AND idx = ?", (slot_id, idx)
        )
        row = await cur.fetchone()
        return Suggestion.from_row(row) if row else None

    async def list_for_slot(self, slot_id: int) -> list[Suggestion]:
        cur = await self.db.conn.execute(
            "SELECT * FROM suggestions WHERE slot_id = ? ORDER BY idx", (slot_id,)
        )
        return [Suggestion.from_row(r) for r in await cur.fetchall()]

    async def mark_picked(self, suggestion_id: int) -> None:
        await self.db.conn.execute("UPDATE suggestions SET picked = 1 WHERE id = ?", (suggestion_id,))
        await self.db.conn.commit()

    async def recent(
        self, kinds: tuple[str, ...], since_iso: str | None = None, picked_only: bool = False
    ) -> list[Suggestion]:
        """Подсказки указанных видов за период: для исключения повторов."""
        placeholders = ",".join("?" for _ in kinds)
        sql = f"SELECT * FROM suggestions WHERE kind IN ({placeholders})"
        params: list = list(kinds)
        if picked_only:
            sql += " AND picked = 1"
        if since_iso:
            sql += " AND created_at >= ?"
            params.append(since_iso)
        cur = await self.db.conn.execute(sql, params)
        return [Suggestion.from_row(r) for r in await cur.fetchall()]
