from datetime import date

import pytest

from app import texts
from app.models import FallbackMode
from app.services.llm import LlmFormatError, parse_reply
from tests.fakes import ScriptedLlm

TODAY = date(2026, 9, 26)
USER = [{"role": "user", "content": [{"type": "text", "text": "Леса ЛРСП-40, 40 м, ссылка https://as-lesa.ru/x"}]}]


def test_parse_question():
    parsed = parse_reply("Какая высота и есть ли ссылка?")
    assert parsed.kind == "question"


def test_parse_post_with_fences_and_noise():
    parsed = parse_reply("```html\n<post>\n<b>Пост</b>\n</post>\n```")
    assert parsed.kind == "draft"
    assert parsed.body == "<b>Пост</b>"


def test_parse_unclosed_post_raises():
    with pytest.raises(LlmFormatError):
        parse_reply("<post><b>без конца</b>")


async def test_generate_question(settings):
    llm = ScriptedLlm(settings, ["Уточните нагрузку и ссылку."])
    reply = await llm.generate(USER, last_posts=[], has_photos=False, today=TODAY)
    assert reply.kind == "question"
    assert reply.draft is None
    system, messages = llm.calls[0]
    assert system[0]["text"] == "system prompt for tests"
    assert "cache_control" in system[0]
    assert "26 сентября 2026" in system[1]["text"]
    assert "пока ничего не публиковалось" in system[1]["text"]


async def test_generate_draft_ok(settings):
    llm = ScriptedLlm(settings, ["<post>\n<b>Леса ЛРСП-40</b>\nВысота 40 м.\n</post>"])
    reply = await llm.generate(USER, last_posts=["старый пост"], has_photos=True, today=TODAY)
    assert reply.kind == "draft"
    assert reply.draft.fallback == FallbackMode.NORMAL
    assert reply.draft.html == "<b>Леса ЛРСП-40</b>\nВысота 40 м."
    assert reply.text.startswith("<post>")
    assert "<post_1>\nстарый пост\n</post_1>" in llm.calls[0][0][1]["text"]
    assert len(llm.calls) == 1


async def test_invalid_html_fixed_on_retry(settings):
    llm = ScriptedLlm(settings, ["<post><b>Леса</post>", "<post><b>Леса</b></post>"])
    reply = await llm.generate(USER, last_posts=[], has_photos=False, today=TODAY)
    assert reply.draft.html == "<b>Леса</b>"
    assert reply.draft.fallback == FallbackMode.NORMAL
    assert len(llm.calls) == 2
    retry_messages = llm.calls[1][1]
    assert retry_messages[-1]["role"] == "user"
    assert "невалидная Telegram-HTML" in retry_messages[-1]["content"]
    assert retry_messages[-2]["role"] == "assistant"


async def test_invalid_html_twice_falls_back_to_plain(settings):
    llm = ScriptedLlm(settings, ["<post><p>Леса <b>40 м</p></post>"])
    reply = await llm.generate(USER, last_posts=[], has_photos=False, today=TODAY)
    assert reply.draft.fallback == FallbackMode.PLAIN
    assert reply.draft.html == "Леса 40 м"
    assert "<" not in reply.draft.html
    assert texts.PREVIEW_NOTE_PLAIN in reply.draft.notes


async def test_too_long_caption_shortened(settings):
    long_post = "<post>" + "Леса. " * 300 + "</post>"
    short_post = "<post><b>Леса</b> коротко.</post>"
    llm = ScriptedLlm(settings, [long_post, short_post])
    reply = await llm.generate(USER, last_posts=[], has_photos=True, today=TODAY)
    assert reply.draft.fallback == FallbackMode.NORMAL
    assert reply.draft.plain == "Леса коротко."
    shorten_request = llm.calls[1][1][-1]["content"]
    assert "Сократи до 974" in shorten_request


async def test_too_long_twice_falls_back_to_split(settings):
    long_post = "<post>" + "Леса. " * 300 + "</post>"
    llm = ScriptedLlm(settings, [long_post])
    reply = await llm.generate(USER, last_posts=[], has_photos=True, today=TODAY)
    assert reply.draft.fallback == FallbackMode.SPLIT
    assert texts.PREVIEW_NOTE_SPLIT in reply.draft.notes


async def test_long_text_without_photos_within_4096_is_normal(settings):
    post = "<post>" + "Леса. " * 300 + "</post>"  # ~1800 символов
    llm = ScriptedLlm(settings, [post])
    reply = await llm.generate(USER, last_posts=[], has_photos=False, today=TODAY)
    assert reply.draft.fallback == FallbackMode.NORMAL
    assert len(llm.calls) == 1


async def test_effort_not_sent_when_empty(settings):
    assert settings.llm_effort == ""
