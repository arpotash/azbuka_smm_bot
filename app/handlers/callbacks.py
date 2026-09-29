"""Кнопки под напоминанием и превью: варианты от бота, опубликовать, правки, пропустить."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery

from app import texts
from app.models import SlotStatus
from app.services.publisher import PublishError
from app.services.slots import SlotService
from app.texts import SlotCb, SugCb

log = logging.getLogger(__name__)
router = Router(name="callbacks")


@router.callback_query(SlotCb.filter())
async def on_slot_action(cb: CallbackQuery, callback_data: SlotCb, slot_service: SlotService) -> None:
    slot = await slot_service.db.slots.get(callback_data.slot_id)
    if slot is None or not slot.is_open:
        await cb.answer(texts.SLOT_ALREADY_CLOSED, show_alert=True)
        await _drop_markup(cb)
        return

    action = callback_data.action
    if action == "skip":
        await cb.answer()
        await slot_service.skip(slot, reason="button")
        await cb.message.answer(texts.SLOT_SKIPPED.format(slot_id=slot.id))
        return

    if action == "revise":
        if slot.status != SlotStatus.DRAFTING:
            await cb.answer(texts.PREVIEW_STALE, show_alert=True)
            await _drop_markup(cb)
            return
        await cb.answer()
        await slot_service.start_revision(slot)
        await cb.message.answer(texts.REVISE_PROMPT)
        return

    if action == "publish":
        if slot.status != SlotStatus.DRAFTING:
            await cb.answer(texts.PREVIEW_STALE, show_alert=True)
            await _drop_markup(cb)
            return
        await cb.answer()
        try:
            result_text = await slot_service.publish(slot, by_user=cb.from_user.id)
        except PublishError as exc:
            log.error("Публикация слота #%d не удалась: %s", slot.id, exc)
            await cb.message.answer(exc.user_message)
            return
        await cb.message.answer(result_text)
        return

    await cb.answer()


@router.callback_query(SugCb.filter())
async def on_suggestion(cb: CallbackQuery, callback_data: SugCb, slot_service: SlotService) -> None:
    slot = await slot_service.db.slots.get(callback_data.slot_id)
    if slot is None or not slot.is_open:
        await cb.answer(texts.SLOT_ALREADY_CLOSED, show_alert=True)
        await _drop_markup(cb)
        return

    if callback_data.action == "more":
        if slot.status != SlotStatus.REMINDER and slot.assigned_user_id != cb.from_user.id:
            await cb.answer(texts.SLOT_TAKEN_BY_OTHER, show_alert=True)
            return
        await cb.answer()
        await slot_service.more_suggestions(slot, chat_id=cb.message.chat.id)
        return

    if callback_data.action == "pick":
        await cb.answer()
        try:
            suggestion = await slot_service.take_suggestion(slot, callback_data.idx, cb.from_user)
        except LookupError as exc:
            await cb.message.answer(str(exc))
            return
        await _drop_markup(cb)
        await cb.message.answer(
            texts.SUGGESTION_TAKEN.format(title=_esc(suggestion.title)), parse_mode=ParseMode.HTML
        )
        return

    await cb.answer()


def _esc(text: str) -> str:
    import html
    return html.escape(text, quote=False)


async def _drop_markup(cb: CallbackQuery) -> None:
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
