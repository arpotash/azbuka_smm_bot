"""Сквозной сценарий с подсказками: напоминание с вариантами → «Взять» → превью → публикация."""

import asyncio
import random

import pytest
from aiogram.types import BufferedInputFile

from app import texts
from app.models import MsgKind, SlotKind, SlotStatus
from app.services.llm import FakeLlm
from app.services.media import MediaStore
from app.services.site_api import SiteApiError
from app.services.slots import SlotService
from app.services.suggest import Suggester
from tests.fakes import FakeBot, FakeSiteApi, make_message

DRAFT_REPLY = "<post>\n<b>Полок из липы</b>\nНе нагревается, без смолы.\n</post>"


class User:
    def __init__(self, uid):
        self.id = uid
        self.full_name = f"Менеджер {uid}"


@pytest.fixture
async def service(settings, db, tmp_path):
    reply = tmp_path / "reply.txt"
    reply.write_text(DRAFT_REPLY, encoding="utf-8")
    bot = FakeBot()
    site = FakeSiteApi()
    media = MediaStore(bot, settings.media_dir, fetch_url=site.get_bytes)
    suggester = Suggester(site, feeds=["https://feed.example/rss"], rng=random.Random(7))
    svc = SlotService(bot, db, FakeLlm(settings, "system", reply_file=str(reply)), media, settings, suggester=suggester)
    svc.fake_bot, svc.site = bot, site
    return svc


async def wait_processing(svc):
    await asyncio.sleep(0.2)
    for task in list(svc._debounce_tasks.values()):
        if not task.done():
            await task
    for lock in svc._locks.values():
        async with lock:
            pass


def keyboard_texts(markup):
    return [b.text for row in markup.inline_keyboard for b in row]


async def test_product_reminder_pick_and_publish(service: SlotService, db, settings):
    bot = service.fake_bot
    slot = await service.open_slot(SlotKind.SCHEDULED, theme="product", scheduled_for="2026-09-30")

    # напоминание: тема, три варианта, кнопки
    reminders = bot.by_method("send_message")
    assert {r.chat_id for r in reminders} == {1, 2}
    text = reminders[0].kwargs["text"]
    assert "Тема сегодня: <b>Товар или категория</b>" in text
    assert "1. <b>" in text and "3. <b>" in text
    assert keyboard_texts(reminders[0].kwargs["reply_markup"]) == ["Взять 1", "Взять 2", "Взять 3", "Другие варианты", "Пропустить"]
    saved = await db.suggestions.list_for_slot(slot.id)
    assert [s.idx for s in saved] == [1, 2, 3] and all(s.kind == "product" for s in saved)

    # «Другие варианты»: новые ref, idx продолжается
    more = await service.more_suggestions(slot, chat_id=1)
    assert more and {s.ref for s in more}.isdisjoint({s.ref for s in saved})
    assert more[0].idx == 4
    slot = await db.slots.get(slot.id)
    assert len(slot.reminder_msgs[1]) == 2  # у менеджера 1 теперь два сообщения с кнопками

    # менеджер 2 берёт вариант 2
    picked = await service.take_suggestion(slot, 2, User(2))
    assert picked.idx == 2
    slot = await db.slots.get(slot.id)
    assert slot.status == SlotStatus.COLLECTING and slot.assigned_user_id == 2
    # у менеджера 1 сняты кнопки с обоих сообщений и пришло уведомление
    edited_for_1 = [m for c, m in bot.edited if c == 1]
    assert len(edited_for_1) == 2
    assert any("выбрал вариант" in s.kwargs["text"] for s in bot.by_method("send_message", 1))

    rows = await db.messages.list_for_slot(slot.id)
    assert rows[0].kind == MsgKind.TEXT and rows[0].text.startswith("Карточка товара с сайта")
    assert "Ссылка на товар: https://as-lesa.ru/catalog/product/" in rows[0].text
    photos = [r for r in rows if r.kind == MsgKind.PHOTO]
    assert [p.file_id for p in photos] == ["https://storage/p_full_1.jpeg", "https://storage/p_full_2.jpeg"]
    assert all(p.file_unique_id.startswith("url_") for p in photos)
    assert (await db.suggestions.get(slot.id, 2)).picked

    # модель получила карточку и два изображения, тема в system-блоке
    await wait_processing(service)
    system, messages = service.llm.calls[-1]
    assert "Тема поста: Товар или категория." in system[1]["text"]
    images = [b for b in messages[0]["content"] if b["type"] == "image"]
    assert len(images) == 2

    # превью: фото по URL ушли байтами, не ссылкой
    slot = await db.slots.get(slot.id)
    assert slot.status == SlotStatus.DRAFTING
    group = bot.by_method("send_media_group", 2)
    assert len(group) == 2
    assert all(isinstance(g.kwargs["media"].media, BufferedInputFile) for g in group)

    # публикация и запись в posts с URL фото
    result = await service.publish(slot, by_user=2)
    assert result.startswith("Опубликовано")
    post = (await db.posts.last(1))[0]
    assert post.photo_file_ids == ["https://storage/p_full_1.jpeg", "https://storage/p_full_2.jpeg"]

    # следующий слот той же темы не предложит выбранный товар и его категорию
    slot2 = await service.open_slot(SlotKind.SCHEDULED, theme="product", scheduled_for="2026-10-02")
    refs2 = {s.ref for s in await db.suggestions.list_for_slot(slot2.id)}
    assert picked.ref not in refs2
    shown_before = {s.ref for s in saved} | {s.ref for s in more}
    assert refs2.isdisjoint(shown_before)  # показанные за последние 30 дней тоже не повторяются
    cats2 = {s.payload["category_uuid"] for s in await db.suggestions.list_for_slot(slot2.id)}
    assert picked.payload["category_uuid"] not in cats2


async def test_manager_can_add_own_material_after_pick(service: SlotService, db):
    slot = await service.open_slot(SlotKind.SCHEDULED, theme="product", scheduled_for="2026-09-30")
    await service.take_suggestion(slot, 1, User(1))
    await service.add_material(make_message(1, "Добавь, что доставка по Барнаулу бесплатная", message_id=5))
    await wait_processing(service)
    _, messages = service.llm.calls[-1]
    text_block = [b for b in messages[0]["content"] if b["type"] == "text"][0]
    assert "Карточка товара" in text_block["text"] and "доставка по Барнаулу" in text_block["text"]


async def test_pick_by_second_manager_rejected(service: SlotService, db):
    slot = await service.open_slot(SlotKind.SCHEDULED, theme="product", scheduled_for="2026-09-30")
    await service.take_suggestion(slot, 1, User(1))
    slot = await db.slots.get(slot.id)
    with pytest.raises(LookupError) as exc:
        await service.take_suggestion(slot, 2, User(2))
    assert str(exc.value) == texts.SLOT_TAKEN_BY_OTHER


async def test_news_reminder_and_promo_none(service: SlotService, db):
    bot = service.fake_bot
    slot = await service.open_slot(SlotKind.SCHEDULED, theme="news", scheduled_for="2026-10-02")
    saved = await db.suggestions.list_for_slot(slot.id)
    assert {s.kind for s in saved} <= {"blog", "rss"} and saved[0].kind == "blog"
    text = bot.by_method("send_message", 1)[-1].kwargs["text"]
    assert "Новости компании" in text and "блог as-lesa.ru" in text

    await service.skip(slot)
    service.site.promoted = []
    slot = await service.open_slot(SlotKind.SCHEDULED, theme="promo", scheduled_for="2026-10-05")
    text = bot.by_method("send_message", 1)[-1].kwargs["text"]
    assert "Акций на сайте сейчас нет" in text
    assert keyboard_texts(bot.by_method("send_message", 1)[-1].kwargs["reply_markup"]) == ["Другие варианты", "Пропустить"]


async def test_site_down_degrades_to_plain_reminder(service: SlotService, db, settings):
    service.site.fail_categories = True
    slot = await service.open_slot(SlotKind.SCHEDULED, theme="product", scheduled_for="2026-09-30")
    bot = service.fake_bot
    reminder = [s for s in bot.by_method("send_message", 2)][-1]
    assert "Сайт сейчас не отвечает" in reminder.kwargs["text"]
    assert await db.suggestions.list_for_slot(slot.id) == []
    admin_msgs = bot.by_method("send_message", 1)
    assert any("не удалось подобрать варианты" in s.kwargs["text"] for s in admin_msgs)


async def test_free_theme_has_plain_reminder(service: SlotService):
    await service.open_slot(SlotKind.MANUAL, theme="free")
    reminder = service.fake_bot.by_method("send_message", 1)[-1]
    assert "Свободная тема" in reminder.kwargs["text"]
    assert keyboard_texts(reminder.kwargs["reply_markup"]) == ["Пропустить"]


async def test_migration_adds_theme_column(settings, tmp_path):
    import sqlite3
    from app.db import Database

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE slots (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, status TEXT NOT NULL,
            scheduled_for TEXT, assigned_user_id INTEGER, current_draft TEXT, draft_fallback TEXT NOT NULL DEFAULT 'normal',
            preview_chat_id INTEGER, preview_msg_ids TEXT NOT NULL DEFAULT '[]', reminder_msgs TEXT NOT NULL DEFAULT '{}',
            llm_error_count INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        INSERT INTO slots(kind, status, reminder_msgs, created_at, updated_at) VALUES ('manual', 'skipped', '{"1": 55}', 'x', 'x');
    """)
    conn.commit(); conn.close()
    db = Database(path)
    await db.connect()
    slot = await db.slots.get(1)
    assert slot.theme == "free"
    assert slot.reminder_msgs == {1: [55]}  # старый формат читается
    assert (await db.slots.create(SlotKind.MANUAL, SlotStatus.REMINDER, None, theme="news")).theme == "news"
    await db.close()
