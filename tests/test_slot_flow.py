"""Сквозной сценарий: напоминание → материал → альбом → превью → публикация. Без сети."""

import asyncio

import pytest

from app import texts
from app.models import MsgKind, Role, SlotKind, SlotStatus
from app.scheduler import catch_up
from app.services.llm import FakeLlm
from app.services.media import MediaStore
from app.services.slots import SlotService
from tests.fakes import FakeBot, make_message

DRAFT_REPLY = "<post>\n<b>Леса ЛРСП-40</b>\nВысота до 40 м.\n</post>"


@pytest.fixture
def reply_file(tmp_path):
    path = tmp_path / "reply.txt"
    path.write_text(DRAFT_REPLY, encoding="utf-8")
    return path


@pytest.fixture
async def service(settings, db, reply_file):
    bot = FakeBot()
    llm = FakeLlm(settings, "system", reply_file=str(reply_file))
    media = MediaStore(bot, settings.media_dir)
    svc = SlotService(bot, db, llm, media, settings)
    svc.fake_bot = bot
    svc.fake_llm = llm
    svc.reply_file = reply_file
    return svc


async def wait_processing(svc: SlotService):
    await asyncio.sleep(0.2)
    for task in list(svc._debounce_tasks.values()):
        if not task.done():
            await task
    for lock in svc._locks.values():
        async with lock:
            pass


async def test_full_flow(service: SlotService, db, settings):
    bot: FakeBot = service.fake_bot

    # 1. Планировщик открывает слот, оба менеджера получают напоминание с кнопкой
    slot = await service.open_slot(SlotKind.SCHEDULED, theme="free", scheduled_for="2026-09-28")
    assert slot.status == SlotStatus.REMINDER
    assert slot.theme == "free" and slot.scheduled_for == "2026-09-28"
    assert {s.chat_id for s in bot.by_method("send_message")} == {1, 2}
    assert all(s.kwargs["reply_markup"] is not None for s in bot.by_method("send_message"))
    assert set(slot.reminder_msgs) == {1, 2}

    # 2. Первый менеджер присылает текст: слот его, второму снимают кнопку и сообщают
    await service.add_material(make_message(1, "Леса ЛРСП-40, высота 40 м", message_id=10))
    slot = await db.slots.get(slot.id)
    assert slot.status == SlotStatus.COLLECTING
    assert slot.assigned_user_id == 1
    assert (2, slot.reminder_msgs[2][0]) in bot.edited
    assert any(texts.SLOT_TAKEN.format(slot_id=slot.id, name="Менеджер 1") == s.kwargs["text"]
               for s in bot.by_method("send_message", 2))

    # 3. Второй менеджер пробует вмешаться
    msg2 = make_message(2, "А я тоже", message_id=11)
    await service.add_material(msg2)
    msg2.answer.assert_awaited_once_with(texts.SLOT_TAKEN_BY_OTHER)

    # 4. После debounce один вызов модели, превью текстом с клавиатурой
    await wait_processing(service)
    assert len(service.fake_llm.calls) == 1
    slot = await db.slots.get(slot.id)
    assert slot.status == SlotStatus.DRAFTING
    assert slot.current_draft == "<b>Леса ЛРСП-40</b>\nВысота до 40 м."
    preview = bot.by_method("send_message", 1)[-1]
    assert preview.kwargs["text"] == slot.current_draft
    assert preview.kwargs["reply_markup"] is not None
    assert slot.preview_msg_ids == [preview.message_id]

    # 5. Альбом из трёх фото после превью = правки; один вызов модели на весь альбом
    calls_before = len(service.fake_llm.calls)
    for i in range(3):
        await service.add_material(
            make_message(1, message_id=20 + i, photo=True, media_group_id="g1", caption="фото" if i == 0 else None)
        )
    slot_mid = await db.slots.get(slot.id)
    assert slot_mid.status == SlotStatus.REVISION
    assert (1, preview.message_id) in bot.edited  # клавиатура со старого превью снята
    await wait_processing(service)
    assert len(service.fake_llm.calls) == calls_before + 1
    _, messages = service.fake_llm.calls[-1]
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant" and "<post>" in messages[1]["content"]
    last_turn = messages[-1]
    assert last_turn["role"] == "user"
    images = [b for b in last_turn["content"] if b["type"] == "image"]
    assert len(images) == 3
    assert images[0]["source"]["media_type"] == "image/jpeg"
    text_block = [b for b in last_turn["content"] if b["type"] == "text"][0]
    assert "Подпись к фото 1: фото" in text_block["text"]
    rows = await db.messages.list_for_slot(slot.id)
    assert len([r for r in rows if r.kind == MsgKind.PHOTO and r.media_group_id == "g1"]) == 3

    slot = await db.slots.get(slot.id)
    assert slot.status == SlotStatus.DRAFTING
    # превью с 3 фото: медиагруппа и отдельное сообщение с кнопками
    assert len(bot.by_method("send_media_group", 1)) == 3
    assert bot.sent[-1].kwargs["text"] == texts.PREVIEW_ACTIONS

    # 6. Публикация в канал
    result = await service.publish(slot, by_user=1)
    assert result.startswith("Опубликовано: https://t.me/c/1234567890/")
    channel_msgs = [s for s in bot.sent if s.chat_id == settings.channel_id]
    assert [s.method for s in channel_msgs] == ["send_media_group"] * 3
    assert channel_msgs[0].kwargs["media"].caption == slot.current_draft
    slot = await db.slots.get(slot.id)
    assert slot.status == SlotStatus.PUBLISHED
    posts = await db.posts.last(5)
    assert len(posts) == 1
    assert posts[0].plain_text == "Леса ЛРСП-40\nВысота до 40 м."
    assert len(posts[0].message_ids) == 3

    # 7. Повторная публикация того же слота невозможна
    from app.services.publisher import PublishError
    with pytest.raises(PublishError):
        await service.publish(slot, by_user=1)

    # 8. last_posts попадают в системный промпт следующего слота
    await service.add_material(make_message(1, "Вышка-тура ВСП-250", message_id=30))
    await wait_processing(service)
    system, _ = service.fake_llm.calls[-1]
    assert "Леса ЛРСП-40" in system[1]["text"]
    new_slot = await db.slots.get_open()
    assert new_slot.kind == SlotKind.MANUAL


async def test_question_keeps_collecting(service: SlotService, db):
    service.reply_file.write_text("Уточните нагрузку и ссылку.", encoding="utf-8")
    await service.add_material(make_message(1, "Леса", message_id=1))
    await wait_processing(service)
    slot = await db.slots.get_open()
    assert slot.status == SlotStatus.COLLECTING
    rows = await db.messages.list_for_slot(slot.id)
    assert rows[-1].role == Role.ASSISTANT and rows[-1].kind == MsgKind.QUESTION
    sent = service.fake_bot.by_method("send_message", 1)[-1]
    assert sent.kwargs["text"] == "Уточните нагрузку и ссылку."
    assert sent.kwargs["parse_mode"] is None


async def test_new_slot_skips_open_and_guards_duplicates(service: SlotService, db):
    first = await service.open_slot(SlotKind.SCHEDULED, scheduled_for="2026-09-28")
    await service.add_material(make_message(1, "материал", message_id=1))
    dup = await service.open_slot(SlotKind.SCHEDULED, scheduled_for="2026-09-28")
    assert dup is None
    # старый слот при этом уже закрыт как пропущенный
    first = await db.slots.get(first.id)
    assert first.status == SlotStatus.SKIPPED
    notice = service.fake_bot.by_method("send_message", 1)[-1]
    assert notice.kwargs["text"] == texts.SLOT_SKIPPED_NEW_SLOT.format(slot_id=first.id)

    second = await service.open_slot(SlotKind.SCHEDULED, scheduled_for="2026-09-30")
    assert second is not None and second.status == SlotStatus.REMINDER
    assert len(await db.slots.list_open()) == 1


async def test_llm_error_resets_processed_and_alerts_admin(service: SlotService, db):
    from app.services.llm import LlmError

    async def failing(*a, **kw):
        raise LlmError(texts.LLM_CONNECTION, "connection refused")

    service.llm.generate = failing
    await service.add_material(make_message(2, "Леса", message_id=1))
    await wait_processing(service)
    slot = await db.slots.get_open()
    assert slot.status == SlotStatus.COLLECTING
    assert await db.messages.unprocessed_user_ids(slot.id) != []
    assert service.fake_bot.by_method("send_message", 2)[-1].kwargs["text"] == texts.LLM_CONNECTION
    # второе сообщение доводит до порога и уведомляет админа (id=1)
    await service.add_material(make_message(2, "ещё", message_id=2))
    await wait_processing(service)
    admin_msgs = service.fake_bot.by_method("send_message", 1)
    assert any("ошибок модели" in s.kwargs["text"] for s in admin_msgs)


async def test_catch_up_creates_slot_once(service: SlotService, db, settings, monkeypatch):
    import app.scheduler as sched
    from datetime import datetime

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 28, 12, 30, tzinfo=tz)  # понедельник, после 12:00

    monkeypatch.setattr(sched, "datetime", FixedDatetime)
    await catch_up(settings, service)
    await catch_up(settings, service)
    slots = await db.slots.list_open()
    assert len(slots) == 1 and slots[0].scheduled_for == "2026-09-28"
    assert slots[0].theme == "promo"  # понедельник в плане по умолчанию
