"""Отправка черновика в чат: единый путь для превью менеджеру и публикации в канал."""

from __future__ import annotations

import asyncio
import html as html_lib
import logging
from dataclasses import dataclass

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup, InputMediaPhoto, Message

from app import texts
from app.models import Draft, FallbackMode
from app.services import html_tools

log = logging.getLogger(__name__)

MAX_ALBUM = 10


class PublishError(Exception):
    def __init__(self, user_message: str, detail: str = ""):
        super().__init__(detail or user_message)
        self.user_message = user_message


@dataclass
class RenderResult:
    messages: list[Message]
    fallback: FallbackMode
    html: str
    plain: str

    @property
    def message_ids(self) -> list[int]:
        return [m.message_id for m in self.messages]


async def render(
    bot,
    chat_id: int,
    draft: Draft,
    photo_file_ids: list[str],
    reply_markup: InlineKeyboardMarkup | None = None,
) -> RenderResult:
    photos = photo_file_ids[:MAX_ALBUM]
    if len(photo_file_ids) > MAX_ALBUM:
        log.warning("Фото больше %d, отправляю первые %d", MAX_ALBUM, MAX_ALBUM)

    current = draft
    for attempt in range(2):
        try:
            return await _send(bot, chat_id, current, photos, reply_markup)
        except TelegramRetryAfter as exc:
            log.warning("Flood control, жду %s с", exc.retry_after)
            await asyncio.sleep(exc.retry_after)
        except TelegramBadRequest as exc:
            message = (exc.message or "").lower()
            if "can't parse entities" in message and current.fallback != FallbackMode.PLAIN:
                log.warning("Telegram отклонил разметку (%s), отправляю без HTML", exc.message)
                current = Draft(
                    html=html_lib.escape(current.plain, quote=False),
                    plain=current.plain,
                    fallback=FallbackMode.PLAIN,
                    notes=current.notes + [texts.PREVIEW_NOTE_PLAIN],
                )
                continue
            raise PublishError(texts.PUBLISH_FAILED.format(error=exc.message), exc.message) from exc
        except TelegramForbiddenError as exc:
            raise PublishError(texts.PUBLISH_FORBIDDEN, exc.message) from exc
    raise PublishError(texts.PUBLISH_FAILED.format(error="повторные ошибки Telegram"))


async def _send(bot, chat_id: int, draft: Draft, photos: list[str], reply_markup) -> RenderResult:
    fallback = draft.fallback
    html = draft.html
    plain = draft.plain
    sent: list[Message] = []

    if photos and fallback != FallbackMode.SPLIT and html_tools.utf16_len(plain) > html_tools.CAPTION_LIMIT:
        fallback = FallbackMode.SPLIT

    if not photos:
        chunks = _text_chunks(html, plain)
        if len(chunks) > 1:
            fallback = FallbackMode.PLAIN
        for i, chunk in enumerate(chunks):
            markup = reply_markup if i == len(chunks) - 1 else None
            sent.append(await bot.send_message(chat_id, chunk, parse_mode=ParseMode.HTML, reply_markup=markup))
        return RenderResult(sent, fallback, html, plain)

    if fallback == FallbackMode.SPLIT:
        sent.extend(await _send_photos(bot, chat_id, photos, caption=None))
        chunks = _text_chunks(html, plain)
        for i, chunk in enumerate(chunks):
            markup = reply_markup if i == len(chunks) - 1 else None
            sent.append(await bot.send_message(chat_id, chunk, parse_mode=ParseMode.HTML, reply_markup=markup))
        return RenderResult(sent, fallback, html, plain)

    if len(photos) == 1:
        sent.append(
            await bot.send_photo(
                chat_id, photos[0], caption=html, parse_mode=ParseMode.HTML, reply_markup=reply_markup
            )
        )
        return RenderResult(sent, fallback, html, plain)

    sent.extend(await _send_photos(bot, chat_id, photos, caption=html))
    if reply_markup is not None:
        # у медиагруппы нет inline-кнопок, клавиатура отдельным сообщением
        sent.append(await bot.send_message(chat_id, texts.PREVIEW_ACTIONS, reply_markup=reply_markup))
    return RenderResult(sent, fallback, html, plain)


async def _send_photos(bot, chat_id: int, photos: list[str], caption: str | None) -> list[Message]:
    if len(photos) == 1:
        msg = await bot.send_photo(
            chat_id, photos[0], caption=caption, parse_mode=ParseMode.HTML if caption else None
        )
        return [msg]
    media = []
    for i, file_id in enumerate(photos):
        if i == 0 and caption:
            media.append(InputMediaPhoto(media=file_id, caption=caption, parse_mode=ParseMode.HTML))
        else:
            media.append(InputMediaPhoto(media=file_id))
    return list(await bot.send_media_group(chat_id, media))


def _text_chunks(html: str, plain: str) -> list[str]:
    if html_tools.utf16_len(plain) <= html_tools.TEXT_LIMIT and html_tools.utf16_len(html) <= html_tools.TEXT_LIMIT * 2:
        return [html]
    # слишком длинно для одного сообщения: режем видимый текст без разметки
    return [html_lib.escape(c, quote=False) for c in html_tools.split_text(plain)]


def post_link(channel_id: int, channel_username: str, message_id: int) -> str | None:
    if channel_username:
        return f"https://t.me/{channel_username.lstrip('@')}/{message_id}"
    raw = str(channel_id)
    if raw.startswith("-100"):
        return f"https://t.me/c/{raw[4:]}/{message_id}"
    return None
