"""Белый список менеджеров."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app import texts

log = logging.getLogger(__name__)


class WhitelistMiddleware(BaseMiddleware):
    def __init__(self, allowed_ids: set[int]):
        self.allowed_ids = allowed_ids

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if user is None or user.id not in self.allowed_ids:
            user_id = user.id if user else None
            log.info("Отклонён апдейт от %s (%s)", user_id, type(event).__name__)
            if isinstance(event, Message) and event.chat.type == "private":
                await event.answer(texts.ACCESS_DENIED.format(user_id=user_id))
            elif isinstance(event, CallbackQuery):
                await event.answer()
            return None
        return await handler(event, data)
