"""Материал от менеджера: текст и фото в личке."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

from app import texts
from app.services.slots import SlotService

router = Router(name="materials")
router.message.filter(F.chat.type == "private")


@router.message(F.photo)
async def on_photo(message: Message, slot_service: SlotService) -> None:
    await slot_service.add_material(message)


@router.message(F.text, ~F.text.startswith("/"))
async def on_text(message: Message, slot_service: SlotService) -> None:
    await slot_service.add_material(message)


@router.message(F.document)
async def on_document(message: Message) -> None:
    await message.answer(texts.SEND_AS_PHOTO)


@router.message()
async def on_other(message: Message) -> None:
    await message.answer(texts.UNSUPPORTED_CONTENT)
