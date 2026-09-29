"""Конечный автомат слота: напоминание → сбор → черновик → правки → публикация или пропуск."""

from __future__ import annotations

import asyncio
import html as html_lib
import logging
from datetime import UTC, datetime, timedelta

from aiogram.enums import ChatAction, ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import Message

from app import texts
from app.config import Settings
from app.db import Database
from app.models import Draft, FallbackMode, MsgKind, Role, Slot, SlotKind, SlotMessage, SlotStatus, Suggestion
from app.services import publisher
from app.services.html_tools import strip_tags
from app.services.llm import BaseLlm, LlmError, build_messages
from app.services.media import MediaStore, url_unique_id
from app.services.publisher import PublishError
from app.services.site_api import SiteApiError
from app.services.suggest import Suggester
from app.texts import preview_keyboard, reminder_keyboard, suggestions_keyboard
from app.themes import Theme, get_theme

log = logging.getLogger(__name__)


class SlotService:
    def __init__(
        self,
        bot,
        db: Database,
        llm: BaseLlm,
        media: MediaStore,
        settings: Settings,
        suggester: Suggester | None = None,
    ):
        self.bot = bot
        self.db = db
        self.llm = llm
        self.media = media
        self.settings = settings
        self.suggester = suggester
        self._locks: dict[int, asyncio.Lock] = {}
        self._debounce_tasks: dict[int, asyncio.Task] = {}

    # ------------------------------------------------------------------ открытие

    async def open_slot(
        self, kind: SlotKind, theme: str = "free", scheduled_for: str | None = None
    ) -> Slot | None:
        """Закрывает незавершённые слоты, создаёт новый, подбирает варианты и рассылает напоминания.

        Возвращает None, если слот на эту дату уже существует (защита от дублей).
        """
        for old in await self.db.slots.list_open():
            await self.skip(old, reason="new_slot")
        slot = await self.db.slots.create(kind, SlotStatus.REMINDER, scheduled_for, theme=theme)
        if slot is None:
            log.info("Слот на %s уже существует, пропускаю создание", scheduled_for)
            return None
        suggestions, fetch_failed = await self._suggest(slot, start_idx=1)
        await self._send_reminders(slot, suggestions, fetch_failed)
        log.info("Открыт слот #%d (%s, тема %s, %s)", slot.id, kind.value, theme, scheduled_for)
        return await self.db.slots.get(slot.id)

    async def _suggest(self, slot: Slot, start_idx: int) -> tuple[list[Suggestion], bool]:
        theme = get_theme(slot.theme)
        if self.suggester is None or not theme.suggests:
            return [], False
        try:
            exclude, exclude_categories = await self._exclusions(slot, theme)
            found = await self.suggester.for_theme(
                theme.key, exclude, n=self.settings.suggest_count, exclude_categories=exclude_categories
            )
        except SiteApiError as exc:
            log.warning("Подсказки для слота #%d не подобраны: %s", slot.id, exc)
            await self.notify_admins(texts.ADMIN_SUGGEST_FAILED.format(slot_id=slot.id, error=exc))
            return [], True
        except Exception as exc:
            log.exception("Сбой подбора подсказок для слота #%d", slot.id)
            await self.notify_admins(texts.ADMIN_SUGGEST_FAILED.format(slot_id=slot.id, error=repr(exc)))
            return [], True
        if not found:
            return [], False
        saved = await self.db.suggestions.add_batch(slot.id, found, start_idx=start_idx)
        return saved, False

    async def _exclusions(self, slot: Slot, theme: Theme) -> tuple[set[str], set[str]]:
        """Что не предлагать: выбранное ранее, недавно показанное, категории недавно выбранных товаров."""
        now = datetime.now(UTC)
        shown_in_this_slot = await self.db.suggestions.list_for_slot(slot.id)
        exclude = {s.ref for s in shown_in_this_slot}
        exclude_categories: set[str] = set()

        if theme.key in ("product", "promo"):
            kinds = ("product", "promo")
            picked = await self.db.suggestions.recent(
                kinds, (now - timedelta(days=self.settings.suggest_product_repeat_days)).isoformat(), picked_only=True
            )
            exclude |= {s.ref for s in picked}
            shown = await self.db.suggestions.recent(
                kinds, (now - timedelta(days=self.settings.suggest_shown_repeat_days)).isoformat()
            )
            exclude |= {s.ref for s in shown}
            cat_since = (now - timedelta(days=self.settings.suggest_category_repeat_days)).isoformat()
            for s in await self.db.suggestions.recent(kinds, cat_since, picked_only=True):
                if s.payload.get("category_uuid"):
                    exclude_categories.add(s.payload["category_uuid"])
        elif theme.key == "news":
            kinds = ("blog", "rss")
            exclude |= {s.ref for s in await self.db.suggestions.recent(kinds, None, picked_only=True)}
            exclude |= {
                s.ref for s in await self.db.suggestions.recent(
                    kinds, (now - timedelta(days=self.settings.suggest_shown_repeat_days)).isoformat()
                )
            }
        return exclude, exclude_categories

    def _reminder_text(self, slot: Slot, suggestions: list[Suggestion], fetch_failed: bool, more: bool = False) -> str:
        theme = get_theme(slot.theme)
        if more:
            parts = [texts.MORE_VARIANTS_TITLE]
        else:
            parts = [texts.REMINDER_THEMED.format(title=html_lib.escape(theme.title), hint=html_lib.escape(theme.reminder_hint))]
            if theme.suggests and fetch_failed:
                parts.append(texts.SUGGESTIONS_UNAVAILABLE)
            elif theme.key == "promo" and not suggestions:
                parts.append(texts.PROMO_NONE)
        if suggestions:
            if not more:
                parts.append(texts.SUGGESTIONS_HEADER)
            for s in suggestions:
                parts.append(texts.SUGGESTION_ITEM.format(
                    idx=s.idx, title=html_lib.escape(s.title), line=html_lib.escape(s.line), url=html_lib.escape(s.url)
                ))
        if not more:
            parts.append(texts.REMINDER_FOOTER)
        return "\n".join(parts)

    def _reminder_keyboard(self, slot: Slot, suggestions: list[Suggestion]):
        theme = get_theme(slot.theme)
        if theme.suggests and self.suggester is not None:
            return suggestions_keyboard(slot.id, [s.idx for s in suggestions], allow_more=True)
        return reminder_keyboard(slot.id)

    async def _send_reminders(self, slot: Slot, suggestions: list[Suggestion], fetch_failed: bool) -> None:
        text = self._reminder_text(slot, suggestions, fetch_failed)
        keyboard = self._reminder_keyboard(slot, suggestions)
        reminder_msgs: dict[int, list[int]] = {}
        for user_id in sorted(self.settings.manager_ids):
            try:
                msg = await self.bot.send_message(
                    user_id, text, parse_mode=ParseMode.HTML, reply_markup=keyboard, disable_web_page_preview=True
                )
                reminder_msgs[user_id] = [msg.message_id]
            except (TelegramForbiddenError, TelegramBadRequest) as exc:
                log.warning("Не удалось отправить напоминание %s: %s", user_id, exc)
                await self.notify_admins(texts.ADMIN_REMINDER_FAILED.format(user_id=user_id))
        await self.db.slots.update(slot.id, reminder_msgs=reminder_msgs)

    async def more_suggestions(self, slot: Slot, chat_id: int) -> list[Suggestion]:
        """Кнопка «Другие варианты»: новая порция с исключением уже показанных."""
        existing = await self.db.suggestions.list_for_slot(slot.id)
        start_idx = max((s.idx for s in existing), default=0) + 1
        suggestions, fetch_failed = await self._suggest(slot, start_idx=start_idx)
        if not suggestions:
            text = texts.SUGGESTIONS_UNAVAILABLE.strip() if fetch_failed else texts.SUGGESTIONS_NONE_LEFT
            await self.bot.send_message(chat_id, text, reply_markup=reminder_keyboard(slot.id))
            return []
        msg = await self.bot.send_message(
            chat_id,
            self._reminder_text(slot, suggestions, False, more=True),
            parse_mode=ParseMode.HTML,
            reply_markup=self._reminder_keyboard(slot, suggestions),
            disable_web_page_preview=True,
        )
        slot = await self.db.slots.get(slot.id)
        reminder_msgs = {k: list(v) for k, v in slot.reminder_msgs.items()}
        reminder_msgs.setdefault(chat_id, []).append(msg.message_id)
        await self.db.slots.update(slot.id, reminder_msgs=reminder_msgs)
        return suggestions

    async def take_suggestion(self, slot: Slot, idx: int, user) -> Suggestion:
        """Менеджер выбрал вариант: материал из подсказки становится материалом слота."""
        suggestion = await self.db.suggestions.get(slot.id, idx)
        if suggestion is None:
            raise LookupError(texts.SUGGESTION_NOT_FOUND)
        if slot.status not in (SlotStatus.REMINDER, SlotStatus.COLLECTING):
            raise LookupError(texts.PREVIEW_STALE)
        if slot.status == SlotStatus.COLLECTING and slot.assigned_user_id not in (None, user.id):
            raise LookupError(texts.SLOT_TAKEN_BY_OTHER)
        if self.suggester is None:
            raise LookupError(texts.SUGGESTION_NOT_FOUND)
        try:
            material = await self.suggester.materialize(suggestion)
        except SiteApiError as exc:
            log.warning("Материал подсказки %s не загружен: %s", suggestion.ref, exc)
            raise LookupError(texts.SUGGESTION_FETCH_FAILED) from exc

        await self.db.suggestions.mark_picked(suggestion.id)
        await self._claim(slot, user, notify=texts.SUGGESTION_TAKEN_OTHERS.format(
            slot_id=slot.id, name=user.full_name, title=suggestion.title
        ))
        await self.db.messages.add(slot.id, Role.USER, MsgKind.TEXT, user_id=user.id, text=material.text)
        for url in material.photo_urls:
            await self.db.messages.add(
                slot.id, Role.USER, MsgKind.PHOTO, user_id=user.id,
                file_id=url, file_unique_id=url_unique_id(url),
            )
        self.schedule_process(slot.id)
        return suggestion

    async def _claim(self, slot: Slot, user, notify: str) -> None:
        """Слот забирает менеджер: статус collecting, у остальных снимаются кнопки."""
        if slot.status == SlotStatus.REMINDER or slot.assigned_user_id is None:
            await self.db.slots.update(slot.id, status=SlotStatus.COLLECTING, assigned_user_id=user.id)
            await self._strip_reminders(slot, except_user=user.id, notify=notify)
            for message_id in slot.reminder_msgs.get(user.id, []):
                await self._remove_markup(user.id, message_id)

    async def _strip_reminders(self, slot: Slot, except_user: int | None = None, notify: str | None = None) -> None:
        for user_id, message_ids in slot.reminder_msgs.items():
            if user_id == except_user:
                continue
            for message_id in message_ids:
                await self._remove_markup(user_id, message_id)
            if notify:
                try:
                    await self.bot.send_message(user_id, notify)
                except (TelegramForbiddenError, TelegramBadRequest):
                    pass

    async def _strip_preview(self, slot: Slot) -> None:
        if slot.preview_chat_id is None:
            return
        for message_id in slot.preview_msg_ids:
            await self._remove_markup(slot.preview_chat_id, message_id)

    async def _remove_markup(self, chat_id: int, message_id: int) -> None:
        try:
            await self.bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
        except (TelegramBadRequest, TelegramForbiddenError):
            pass  # сообщение без клавиатуры или уже изменено

    async def notify_admins(self, text: str) -> None:
        for admin_id in self.settings.admin_ids:
            try:
                await self.bot.send_message(admin_id, text)
            except (TelegramForbiddenError, TelegramBadRequest):
                pass

    # ------------------------------------------------------------------ материал

    async def add_material(self, message: Message) -> None:
        user = message.from_user
        slot = await self.db.slots.get_open()

        if slot is None:
            slot = await self.db.slots.create(
                SlotKind.MANUAL, SlotStatus.COLLECTING, None, assigned_user_id=user.id
            )
            await message.answer(texts.SLOT_OPENED_MANUAL.format(slot_id=slot.id))
        elif slot.status == SlotStatus.REMINDER:
            await self._claim(slot, user, notify=texts.SLOT_TAKEN.format(slot_id=slot.id, name=user.full_name))
            slot = await self.db.slots.get(slot.id)
        elif slot.assigned_user_id not in (None, user.id):
            await message.answer(texts.SLOT_TAKEN_BY_OTHER)
            return

        if slot.status == SlotStatus.DRAFTING:
            # свободный текст или новое фото после превью = правки без нажатия кнопки
            await self._strip_preview(slot)
            await self.db.slots.update(slot.id, status=SlotStatus.REVISION)
            slot = await self.db.slots.get(slot.id)

        if message.photo:
            kind = MsgKind.PHOTO
        elif slot.status == SlotStatus.REVISION:
            kind = MsgKind.EDIT_REQUEST
        else:
            kind = MsgKind.TEXT

        photo = message.photo[-1] if message.photo else None
        await self.db.messages.add(
            slot.id,
            Role.USER,
            kind,
            user_id=user.id,
            tg_message_id=message.message_id,
            media_group_id=message.media_group_id,
            text=message.text or message.caption,
            file_id=photo.file_id if photo else None,
            file_unique_id=photo.file_unique_id if photo else None,
            width=photo.width if photo else None,
            height=photo.height if photo else None,
        )
        self.schedule_process(slot.id)

    def schedule_process(self, slot_id: int) -> None:
        """Debounce: альбом приходит отдельными апдейтами, ждём паузу и обрабатываем всё разом."""
        task = self._debounce_tasks.get(slot_id)
        if task is not None and not task.done():
            task.cancel()
        self._debounce_tasks[slot_id] = asyncio.create_task(self._debounced(slot_id))

    async def _debounced(self, slot_id: int) -> None:
        try:
            await asyncio.sleep(self.settings.debounce_sec)
        except asyncio.CancelledError:
            return
        self._debounce_tasks.pop(slot_id, None)
        try:
            await self.process(slot_id)
        except Exception:
            log.exception("Необработанная ошибка при обработке слота #%d", slot_id)

    def _lock(self, slot_id: int) -> asyncio.Lock:
        return self._locks.setdefault(slot_id, asyncio.Lock())

    # ------------------------------------------------------------------ обработка

    async def process(self, slot_id: int) -> None:
        async with self._lock(slot_id):
            while True:
                slot = await self.db.slots.get(slot_id)
                if slot is None or not slot.is_open:
                    return
                pending = await self.db.messages.unprocessed_user_ids(slot_id)
                if not pending:
                    return
                await self.db.messages.mark_processed(pending)
                chat_id = slot.assigned_user_id
                try:
                    reply = await self._ask_model(slot)
                except LlmError as exc:
                    await self.db.messages.mark_processed(pending, False)
                    count = await self.db.slots.increment_llm_errors(slot_id)
                    log.warning("Ошибка модели по слоту #%d (%d): %s", slot_id, count, exc.detail)
                    await self.bot.send_message(chat_id, exc.user_message)
                    if count >= self.settings.llm_error_alert_threshold:
                        await self.notify_admins(
                            texts.ADMIN_LLM_ALERT.format(slot_id=slot_id, count=count, error=exc.detail)
                        )
                    return
                except Exception as exc:
                    log.exception("Сбой обработки слота #%d", slot_id)
                    await self.db.messages.mark_processed(pending, False)
                    await self.bot.send_message(chat_id, texts.PROCESSING_FAILED_GENERIC)
                    await self.notify_admins(texts.ADMIN_LLM_ALERT.format(slot_id=slot_id, count=1, error=repr(exc)))
                    return

                if reply.kind == "question":
                    await self.db.messages.add(
                        slot_id, Role.ASSISTANT, MsgKind.QUESTION, text=reply.text, processed=True
                    )
                    await self.bot.send_message(chat_id, reply.text)
                    if slot.status != SlotStatus.REVISION:
                        await self.db.slots.update(slot_id, status=SlotStatus.COLLECTING, llm_error_count=0)
                    else:
                        await self.db.slots.update(slot_id, llm_error_count=0)
                else:
                    await self.db.messages.add(
                        slot_id, Role.ASSISTANT, MsgKind.DRAFT, text=reply.text, processed=True
                    )
                    await self._show_preview(slot, reply.draft)

    async def _ask_model(self, slot: Slot):
        chat_id = slot.assigned_user_id
        try:
            await self.bot.send_chat_action(chat_id, ChatAction.TYPING)
        except (TelegramBadRequest, TelegramForbiddenError):
            pass
        rows = await self.db.messages.list_for_slot(slot.id)
        has_photos = any(r.kind == MsgKind.PHOTO for r in rows)
        messages = await build_messages(rows, self.media)
        last_posts = [p.plain_text for p in await self.db.posts.last(self.settings.last_posts_n)]
        today = datetime.now(self.settings.tz).date()
        return await self.llm.generate(
            messages, last_posts=last_posts, has_photos=has_photos, today=today, theme=get_theme(slot.theme)
        )

    async def _photo_inputs(self, photos: list[SlotMessage]) -> list:
        inputs = []
        for p in photos:
            try:
                inputs.append(await self.media.telegram_input(p.file_id, p.file_unique_id))
            except Exception as exc:
                log.warning("Фото %s не загружено, пропускаю: %s", p.file_id, exc)
        return inputs

    async def _show_preview(self, slot: Slot, draft: Draft) -> None:
        chat_id = slot.assigned_user_id
        photos = await self.db.messages.photos_for_slot(slot.id)
        try:
            result = await publisher.render(
                self.bot, chat_id, draft, await self._photo_inputs(photos), reply_markup=preview_keyboard(slot.id)
            )
        except PublishError as exc:
            log.error("Не удалось показать превью слота #%d: %s", slot.id, exc)
            await self.bot.send_message(chat_id, exc.user_message)
            return
        notes = list(draft.notes)
        if result.fallback == FallbackMode.PLAIN and texts.PREVIEW_NOTE_PLAIN not in notes:
            notes.append(texts.PREVIEW_NOTE_PLAIN)
        if notes:
            await self.bot.send_message(chat_id, "\n".join(notes))
        await self.db.slots.update(
            slot.id,
            status=SlotStatus.DRAFTING,
            current_draft=result.html,
            draft_fallback=result.fallback,
            preview_chat_id=chat_id,
            preview_msg_ids=result.message_ids,
            llm_error_count=0,
        )

    # ------------------------------------------------------------------ решения

    async def start_revision(self, slot: Slot) -> None:
        await self._strip_preview(slot)
        await self.db.slots.update(slot.id, status=SlotStatus.REVISION)

    async def publish(self, slot: Slot, by_user: int) -> str:
        """Публикует текущий черновик в канал. Возвращает текст для менеджера. Бросает PublishError."""
        async with self._lock(slot.id):
            slot = await self.db.slots.get(slot.id)
            if slot is None or slot.status != SlotStatus.DRAFTING or not slot.current_draft:
                raise PublishError(texts.PREVIEW_STALE)
            draft = Draft(
                html=slot.current_draft,
                plain=strip_tags(slot.current_draft),
                fallback=slot.draft_fallback,
            )
            photos = await self.db.messages.photos_for_slot(slot.id)
            result = await publisher.render(
                self.bot, self.settings.channel_id, draft, await self._photo_inputs(photos)
            )
            await self.db.posts.add(
                slot_id=slot.id,
                channel_id=self.settings.channel_id,
                message_ids=result.message_ids,
                html=result.html,
                plain_text=result.plain,
                photo_file_ids=[p.file_id for p in photos][: publisher.MAX_ALBUM],
                fallback_mode=result.fallback,
                published_by=by_user,
            )
            await self.db.slots.update(slot.id, status=SlotStatus.PUBLISHED)
            await self._strip_preview(slot)
            log.info("Слот #%d опубликован, сообщения %s", slot.id, result.message_ids)
            link = publisher.post_link(
                self.settings.channel_id, self.settings.channel_username, result.message_ids[0]
            )
            return texts.PUBLISHED.format(link=link) if link else texts.PUBLISHED_NO_LINK

    async def skip(self, slot: Slot, reason: str = "manual") -> None:
        task = self._debounce_tasks.pop(slot.id, None)
        if task is not None and not task.done():
            task.cancel()
        await self.db.slots.update(slot.id, status=SlotStatus.SKIPPED)
        await self._strip_reminders(slot)
        await self._strip_preview(slot)
        log.info("Слот #%d пропущен (%s)", slot.id, reason)
        if reason == "new_slot" and slot.assigned_user_id:
            try:
                await self.bot.send_message(
                    slot.assigned_user_id, texts.SLOT_SKIPPED_NEW_SLOT.format(slot_id=slot.id)
                )
            except (TelegramForbiddenError, TelegramBadRequest):
                pass
