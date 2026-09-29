"""Команды бота."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message

from app import texts
from app.config import Settings
from app.models import SlotKind
from app.services.llm import LlmError
from app.services.slots import SlotService
from app.themes import THEMES, get_theme

log = logging.getLogger(__name__)
router = Router(name="commands")


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(texts.START)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(texts.HELP)


@router.message(Command("status"))
async def cmd_status(message: Message, slot_service: SlotService) -> None:
    slot = await slot_service.db.slots.get_open()
    posts = await slot_service.db.posts.count()
    if slot is None:
        await message.answer(texts.NO_OPEN_SLOT + f"\nОпубликовано всего: {posts}")
        return
    materials = await slot_service.db.messages.count_user_messages(slot.id)
    await message.answer(
        texts.STATUS_TEMPLATE.format(
            slot_id=slot.id,
            kind=texts.KIND_NAMES.get(slot.kind.value, slot.kind.value),
            theme=get_theme(slot.theme).title,
            status=texts.STATUS_NAMES.get(slot.status.value, slot.status.value),
            manager=slot.assigned_user_id or "никто",
            materials=materials,
            posts=posts,
        )
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, slot_service: SlotService) -> None:
    slot = await slot_service.db.slots.get_open()
    if slot is None:
        await message.answer(texts.NO_OPEN_SLOT)
        return
    await slot_service.skip(slot, reason="cancel")
    await message.answer(texts.CANCELLED.format(slot_id=slot.id))


@router.message(Command("new"))
async def cmd_new(message: Message, command: CommandObject, slot_service: SlotService, settings: Settings) -> None:
    if message.from_user.id not in settings.admin_ids:
        await message.answer(texts.ADMIN_ONLY)
        return
    theme = (command.args or "free").strip().lower()
    if theme not in THEMES:
        await message.answer(texts.NEW_SLOT_THEME_UNKNOWN.format(theme=theme, themes=", ".join(THEMES)))
        return
    slot = await slot_service.open_slot(SlotKind.MANUAL, theme=theme)
    await message.answer(texts.NEW_SLOT_CREATED.format(slot_id=slot.id))


@router.message(Command("llmtest"))
async def cmd_llmtest(message: Message, slot_service: SlotService, settings: Settings) -> None:
    if message.from_user.id not in settings.admin_ids:
        await message.answer(texts.ADMIN_ONLY)
        return
    try:
        completion = await slot_service.llm.ping()
    except LlmError as exc:
        await message.answer(texts.LLMTEST_FAIL.format(error=exc.detail or exc.user_message))
        return
    await message.answer(
        texts.LLMTEST_OK.format(
            reply=completion.text.strip()[:200],
            request_id=completion.request_id,
            usage=completion.usage,
        )
    )


@router.message(F.forward_origin.type == "channel")
async def on_forwarded_channel_post(message: Message) -> None:
    """Помощь при настройке: id канала по пересланному посту."""
    chat = message.forward_origin.chat
    await message.answer(texts.CHANNEL_ID_INFO.format(title=chat.title, chat_id=chat.id))
