"""Проверка и нормализация Telegram-HTML, подсчёт длины так, как считает Telegram."""

from __future__ import annotations

import html
from html.parser import HTMLParser

# Теги, которые принимает Telegram (parse_mode=HTML). Ключ — тег, значение — допустимые атрибуты.
ALLOWED_TAGS: dict[str, set[str]] = {
    "b": set(), "strong": set(),
    "i": set(), "em": set(),
    "u": set(), "ins": set(),
    "s": set(), "strike": set(), "del": set(),
    "a": {"href"},
    "code": {"class"},
    "pre": set(),
    "tg-spoiler": set(),
    "span": {"class"},          # только class="tg-spoiler"
    "blockquote": {"expandable"},
}

CAPTION_LIMIT = 1024
TEXT_LIMIT = 4096


class HtmlInvalid(ValueError):
    """Разметка не пройдёт в Telegram."""


def utf16_len(text: str) -> int:
    """Длина в UTF-16 code units: именно так Telegram считает лимиты."""
    return len(text.encode("utf-16-le")) // 2


class _Normalizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.plain: list[str] = []
        self.stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in ALLOWED_TAGS:
            raise HtmlInvalid(f"тег <{tag}> не поддерживается Telegram")
        if tag == "a" and "a" in self.stack:
            raise HtmlInvalid("ссылка <a> вложена в другую ссылку")
        allowed_attrs = ALLOWED_TAGS[tag]
        kept: list[str] = []
        for name, value in attrs:
            if name not in allowed_attrs:
                continue
            if tag == "span" and (name != "class" or value != "tg-spoiler"):
                raise HtmlInvalid("<span> допустим только с class=\"tg-spoiler\"")
            if value is None:
                kept.append(name)
            else:
                kept.append(f'{name}="{html.escape(value, quote=True)}"')
        if tag == "a" and not any(k.startswith("href=") for k in kept):
            raise HtmlInvalid("ссылка <a> без href")
        if tag == "span" and not kept:
            raise HtmlInvalid("<span> без class=\"tg-spoiler\"")
        self.stack.append(tag)
        attr_str = (" " + " ".join(kept)) if kept else ""
        self.out.append(f"<{tag}{attr_str}>")

    def handle_endtag(self, tag: str) -> None:
        if not self.stack or self.stack[-1] != tag:
            expected = self.stack[-1] if self.stack else "ничего"
            raise HtmlInvalid(f"закрывающий </{tag}> не совпадает с открытым (<{expected}>)")
        self.stack.pop()
        self.out.append(f"</{tag}>")

    def handle_startendtag(self, tag: str, attrs) -> None:
        raise HtmlInvalid(f"самозакрывающийся тег <{tag}/> не поддерживается")

    def handle_data(self, data: str) -> None:
        self.plain.append(data)
        self.out.append(html.escape(data, quote=False))

    def handle_comment(self, data: str) -> None:
        raise HtmlInvalid("HTML-комментарии не поддерживаются")

    def handle_decl(self, decl: str) -> None:
        raise HtmlInvalid("декларации не поддерживаются")

    def handle_pi(self, data: str) -> None:
        raise HtmlInvalid("инструкции обработки не поддерживаются")


def normalize(raw: str) -> tuple[str, str]:
    """Возвращает (чистый HTML, видимый текст) или бросает HtmlInvalid."""
    parser = _Normalizer()
    try:
        parser.feed(raw)
        parser.close()
    except HtmlInvalid:
        raise
    except Exception as exc:  # ошибки самого парсера
        raise HtmlInvalid(f"не удалось разобрать разметку: {exc}") from exc
    if parser.stack:
        raise HtmlInvalid(f"не закрыт тег <{parser.stack[-1]}>")
    clean = "".join(parser.out).strip()
    plain = "".join(parser.plain).strip()
    return clean, plain


class _Stripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def strip_tags(raw: str) -> str:
    """Терпимо снимает любую разметку. Используется для фолбэка без HTML."""
    parser = _Stripper()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:
        return raw
    return "".join(parser.parts).strip()


def to_plain_html(raw: str) -> tuple[str, str]:
    """Снять разметку и вернуть (экранированный текст для parse_mode=HTML, видимый текст)."""
    plain = strip_tags(raw)
    return html.escape(plain, quote=False), plain


def split_text(text: str, limit: int = TEXT_LIMIT) -> list[str]:
    """Режет длинный текст по абзацам, не превышая лимит в UTF-16."""
    if utf16_len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n"):
        candidate = paragraph if not current else current + "\n" + paragraph
        if utf16_len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        # абзац сам по себе длиннее лимита: режем по символам
        while utf16_len(paragraph) > limit:
            chunks.append(paragraph[:limit])
            paragraph = paragraph[limit:]
        current = paragraph
    if current:
        chunks.append(current)
    return chunks
