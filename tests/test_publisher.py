import pytest

from app import texts
from app.models import Draft, FallbackMode
from app.services import publisher
from app.services.publisher import PublishError, post_link, render
from app.texts import preview_keyboard
from tests.fakes import FakeBot

DRAFT = Draft(html="<b>Леса</b> 40 м", plain="Леса 40 м")


async def test_text_only():
    bot = FakeBot()
    result = await render(bot, 7, DRAFT, [], reply_markup=preview_keyboard(1))
    assert [s.method for s in bot.sent] == ["send_message"]
    assert bot.sent[0].kwargs["text"] == "<b>Леса</b> 40 м"
    assert bot.sent[0].kwargs["reply_markup"] is not None
    assert result.fallback == FallbackMode.NORMAL


async def test_single_photo_caption_with_keyboard():
    bot = FakeBot()
    result = await render(bot, 7, DRAFT, ["f1"], reply_markup=preview_keyboard(1))
    assert [s.method for s in bot.sent] == ["send_photo"]
    assert bot.sent[0].kwargs["caption"] == "<b>Леса</b> 40 м"
    assert bot.sent[0].kwargs["reply_markup"] is not None
    assert result.message_ids == [101]


async def test_media_group_caption_on_first_and_separate_keyboard():
    bot = FakeBot()
    await render(bot, 7, DRAFT, ["f1", "f2", "f3"], reply_markup=preview_keyboard(1))
    group = bot.by_method("send_media_group")
    assert len(group) == 3
    assert group[0].kwargs["media"].caption == "<b>Леса</b> 40 м"
    assert group[1].kwargs["media"].caption is None
    assert group[2].kwargs["media"].caption is None
    last = bot.sent[-1]
    assert last.method == "send_message" and last.kwargs["text"] == texts.PREVIEW_ACTIONS
    assert last.kwargs["reply_markup"] is not None


async def test_media_group_in_channel_without_keyboard():
    bot = FakeBot()
    result = await render(bot, -100, DRAFT, ["f1", "f2"])
    assert [s.method for s in bot.sent] == ["send_media_group", "send_media_group"]
    assert len(result.message_ids) == 2


async def test_long_caption_switches_to_split():
    long = Draft(html="Леса. " * 300, plain="Леса. " * 300)
    bot = FakeBot()
    result = await render(bot, 7, long, ["f1"], reply_markup=preview_keyboard(1))
    assert [s.method for s in bot.sent] == ["send_photo", "send_message"]
    assert bot.sent[0].kwargs["caption"] is None
    assert bot.sent[1].kwargs["reply_markup"] is not None
    assert result.fallback == FallbackMode.SPLIT


async def test_explicit_split_mode_with_album():
    draft = Draft(html=DRAFT.html, plain=DRAFT.plain, fallback=FallbackMode.SPLIT)
    bot = FakeBot()
    await render(bot, 7, draft, ["f1", "f2"])
    assert [s.method for s in bot.sent] == ["send_media_group", "send_media_group", "send_message"]
    assert bot.sent[0].kwargs["media"].caption is None


async def test_parse_error_falls_back_to_plain():
    bot = FakeBot(reject_html=True)
    result = await render(bot, 7, DRAFT, ["f1"])
    assert result.fallback == FallbackMode.PLAIN
    assert bot.sent[-1].kwargs["caption"] == "Леса 40 м"


async def test_album_capped_at_ten():
    bot = FakeBot()
    await render(bot, 7, DRAFT, [f"f{i}" for i in range(15)])
    assert len(bot.by_method("send_media_group")) == publisher.MAX_ALBUM


async def test_forbidden_raises_publish_error():
    from aiogram.exceptions import TelegramForbiddenError
    from types import SimpleNamespace

    class ForbiddenBot(FakeBot):
        async def send_message(self, *a, **kw):
            raise TelegramForbiddenError(method=SimpleNamespace(), message="Forbidden: bot is not a member")

    with pytest.raises(PublishError) as exc:
        await render(ForbiddenBot(), 7, DRAFT, [])
    assert exc.value.user_message == texts.PUBLISH_FORBIDDEN


def test_post_link():
    assert post_link(-1001234567890, "", 5) == "https://t.me/c/1234567890/5"
    assert post_link(-1001234567890, "@aslesa", 5) == "https://t.me/aslesa/5"
    assert post_link(12345, "", 5) is None
