import pytest

from app.services import html_tools
from app.services.html_tools import HtmlInvalid, normalize, split_text, strip_tags, utf16_len


def test_valid_html_roundtrip():
    clean, plain = normalize('<b>Леса ЛРСП-40</b>\nВысота до <i>40 м</i>. <a href="https://as-lesa.ru/x?a=1&amp;b=2">Купить</a>')
    assert clean == '<b>Леса ЛРСП-40</b>\nВысота до <i>40 м</i>. <a href="https://as-lesa.ru/x?a=1&amp;b=2">Купить</a>'
    assert plain == "Леса ЛРСП-40\nВысота до 40 м. Купить"


def test_unescaped_ampersand_is_normalized():
    clean, plain = normalize("Болты & гайки")
    assert clean == "Болты &amp; гайки"
    assert plain == "Болты & гайки"


def test_entities_preserved():
    clean, plain = normalize("a &lt; b")
    assert clean == "a &lt; b"
    assert plain == "a < b"


@pytest.mark.parametrize(
    "raw",
    [
        "<b>не закрыт",
        "<b><i>пере</b>крёст</i>",
        "<p>абзац</p>",
        "<br/>",
        "строка<br>строка",
        '<a>без href</a>',
        '<a href="x"><a href="y">вложено</a></a>',
        "<span>обычный span</span>",
        "<!-- комментарий -->",
    ],
)
def test_invalid_html(raw):
    with pytest.raises(HtmlInvalid):
        normalize(raw)


def test_spoiler_span_and_blockquote_allowed():
    clean, _ = normalize('<span class="tg-spoiler">секрет</span> <blockquote expandable>цитата</blockquote>')
    assert 'class="tg-spoiler"' in clean
    assert "<blockquote expandable>" in clean


def test_utf16_len_counts_surrogate_pairs():
    assert utf16_len("abc") == 3
    assert utf16_len("🚧") == 2
    assert utf16_len("Леса 🚧") == 7


def test_strip_tags_tolerant():
    assert strip_tags("<b>жирный <i>и") == "жирный и"
    assert strip_tags("a &amp; b") == "a & b"


def test_split_text_by_paragraphs():
    text = "\n".join(["абзац " * 100] * 10)
    chunks = split_text(text, limit=1500)
    assert len(chunks) > 1
    assert all(utf16_len(c) <= 1500 for c in chunks)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_limits():
    assert html_tools.CAPTION_LIMIT == 1024
    assert html_tools.TEXT_LIMIT == 4096
