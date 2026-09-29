"""Доменные модели: слот, сообщение слота, опубликованный пост."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum


class SlotStatus(StrEnum):
    REMINDER = "reminder"
    COLLECTING = "collecting"
    DRAFTING = "drafting"
    REVISION = "revision"
    PUBLISHED = "published"
    SKIPPED = "skipped"


OPEN_STATUSES = (
    SlotStatus.REMINDER,
    SlotStatus.COLLECTING,
    SlotStatus.DRAFTING,
    SlotStatus.REVISION,
)


class SlotKind(StrEnum):
    SCHEDULED = "scheduled"
    MANUAL = "manual"


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class MsgKind(StrEnum):
    TEXT = "text"
    PHOTO = "photo"
    EDIT_REQUEST = "edit_request"
    QUESTION = "question"
    DRAFT = "draft"


class FallbackMode(StrEnum):
    NORMAL = "normal"
    SPLIT = "split"   # фото и текст отдельными сообщениями
    PLAIN = "plain"   # текст без HTML-разметки


@dataclass
class Slot:
    id: int
    kind: SlotKind
    status: SlotStatus
    theme: str
    scheduled_for: str | None
    assigned_user_id: int | None
    current_draft: str | None
    draft_fallback: FallbackMode
    preview_chat_id: int | None
    preview_msg_ids: list[int]
    reminder_msgs: dict[int, list[int]]  # chat_id -> сообщения с кнопками напоминания
    llm_error_count: int
    created_at: str
    updated_at: str

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    @classmethod
    def from_row(cls, row) -> "Slot":
        return cls(
            id=row["id"],
            kind=SlotKind(row["kind"]),
            status=SlotStatus(row["status"]),
            theme=row["theme"] if "theme" in row.keys() else "free",
            scheduled_for=row["scheduled_for"],
            assigned_user_id=row["assigned_user_id"],
            current_draft=row["current_draft"],
            draft_fallback=FallbackMode(row["draft_fallback"] or "normal"),
            preview_chat_id=row["preview_chat_id"],
            preview_msg_ids=json.loads(row["preview_msg_ids"] or "[]"),
            reminder_msgs={
                int(k): (v if isinstance(v, list) else [v])
                for k, v in json.loads(row["reminder_msgs"] or "{}").items()
            },
            llm_error_count=row["llm_error_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class SlotMessage:
    id: int
    slot_id: int
    role: Role
    kind: MsgKind
    user_id: int | None
    tg_message_id: int | None
    media_group_id: str | None
    text: str | None
    file_id: str | None
    file_unique_id: str | None
    processed: bool
    created_at: str

    @classmethod
    def from_row(cls, row) -> "SlotMessage":
        return cls(
            id=row["id"],
            slot_id=row["slot_id"],
            role=Role(row["role"]),
            kind=MsgKind(row["kind"]),
            user_id=row["user_id"],
            tg_message_id=row["tg_message_id"],
            media_group_id=row["media_group_id"],
            text=row["text"],
            file_id=row["file_id"],
            file_unique_id=row["file_unique_id"],
            processed=bool(row["processed"]),
            created_at=row["created_at"],
        )


@dataclass
class Post:
    id: int
    slot_id: int
    channel_id: int
    message_id: int
    message_ids: list[int]
    html: str
    plain_text: str
    photo_file_ids: list[str]
    fallback_mode: FallbackMode
    published_by: int
    published_at: str

    @classmethod
    def from_row(cls, row) -> "Post":
        return cls(
            id=row["id"],
            slot_id=row["slot_id"],
            channel_id=row["channel_id"],
            message_id=row["message_id"],
            message_ids=json.loads(row["message_ids"] or "[]"),
            html=row["html"],
            plain_text=row["plain_text"],
            photo_file_ids=json.loads(row["photo_file_ids"] or "[]"),
            fallback_mode=FallbackMode(row["fallback_mode"]),
            published_by=row["published_by"],
            published_at=row["published_at"],
        )


@dataclass
class Draft:
    """Принятый черновик поста после всех проверок."""

    html: str
    plain: str
    fallback: FallbackMode = FallbackMode.NORMAL
    notes: list[str] = field(default_factory=list)  # что пришлось сделать: сокращение, снятие разметки


@dataclass
class Suggestion:
    """Вариант поста, предложенный ботом: товар, акция, статья блога или новость из RSS."""

    kind: str                 # product | promo | blog | rss
    ref: str                  # uuid товара/статьи или URL новости
    title: str
    line: str = ""            # короткая строка под заголовком: цена, категория, источник
    url: str = ""
    image_url: str | None = None
    payload: dict = field(default_factory=dict)
    id: int | None = None
    slot_id: int | None = None
    idx: int | None = None
    picked: bool = False

    @classmethod
    def from_row(cls, row) -> "Suggestion":
        return cls(
            kind=row["kind"],
            ref=row["ref"],
            title=row["title"],
            line=row["line"],
            url=row["url"],
            image_url=row["image_url"],
            payload=json.loads(row["payload"] or "{}"),
            id=row["id"],
            slot_id=row["slot_id"],
            idx=row["idx"],
            picked=bool(row["picked"]),
        )


@dataclass
class Material:
    """Материал, собранный ботом из подсказки: текст и фото по URL."""

    text: str
    photo_urls: list[str] = field(default_factory=list)
