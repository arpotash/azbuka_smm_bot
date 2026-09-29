"""Работа с моделью: сборка диалога, вызов API, парсинг <post>, проверки и ретраи."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal

from app import texts
from app.config import Settings
from app.models import Draft, FallbackMode, MsgKind, Role, SlotMessage
from app.services import html_tools
from app.services.media import MediaStore
from app.themes import Theme

log = logging.getLogger(__name__)

POST_RE = re.compile(r"<post>(.*?)</post>", re.DOTALL | re.IGNORECASE)
POST_OPEN_RE = re.compile(r"<post>", re.IGNORECASE)
FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*\n(.*?)\n```\s*$", re.DOTALL)

MONTHS_RU = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]
WEEKDAYS_RU = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


def format_date_ru(d: date) -> str:
    return f"{d.day} {MONTHS_RU[d.month - 1]} {d.year}, {WEEKDAYS_RU[d.weekday()]}"


class LlmError(Exception):
    """Ошибка модели с текстом для менеджера."""

    def __init__(self, user_message: str, detail: str = "", retryable: bool = True):
        super().__init__(detail or user_message)
        self.user_message = user_message
        self.detail = detail
        self.retryable = retryable


class LlmFormatError(LlmError):
    def __init__(self, detail: str):
        super().__init__(texts.LLM_FORMAT, detail)


@dataclass
class Completion:
    text: str
    stop_reason: str | None = None
    request_id: str | None = None
    usage: dict = field(default_factory=dict)


@dataclass
class LlmReply:
    kind: Literal["question", "draft"]
    text: str                 # текст вопроса или <post>…</post> принятого черновика
    draft: Draft | None = None


@dataclass
class ParsedReply:
    kind: Literal["question", "draft"]
    body: str


def parse_reply(raw: str) -> ParsedReply:
    text = raw.strip()
    fence = FENCE_RE.match(text)
    if fence:
        text = fence.group(1).strip()
    match = POST_RE.search(text)
    if match:
        return ParsedReply("draft", match.group(1).strip())
    if POST_OPEN_RE.search(text):
        raise LlmFormatError("в ответе есть <post> без закрывающего </post>")
    return ParsedReply("question", text)


async def build_messages(rows: list[SlotMessage], media: MediaStore) -> list[dict]:
    """Собирает messages[] для API из переписки слота. Подряд идущие user-строки сливаются в один ход."""
    messages: list[dict] = []
    pending_user: list[SlotMessage] = []

    async def flush_user() -> None:
        if not pending_user:
            return
        photos = sorted(
            (m for m in pending_user if m.kind == MsgKind.PHOTO),
            key=lambda m: (m.tg_message_id or 0, m.id),
        )
        content: list[dict] = []
        for photo in photos:
            content.append(await media.image_block(photo.file_id, photo.file_unique_id))
        parts: list[str] = []
        photo_n = 0
        for m in pending_user:
            if m.kind == MsgKind.PHOTO:
                photo_n += 1
                if m.text:
                    parts.append(texts.PHOTO_CAPTION_PREFIX.format(n=photo_n) + m.text)
            elif m.kind == MsgKind.EDIT_REQUEST:
                parts.append(texts.EDIT_REQUEST_PREFIX + (m.text or ""))
            elif m.text:
                parts.append(m.text)
        text = "\n\n".join(p for p in parts if p.strip())
        if not text:
            text = texts.PHOTOS_ONLY_NOTE
        content.append({"type": "text", "text": text})
        messages.append({"role": "user", "content": content})
        pending_user.clear()

    for row in rows:
        if row.role == Role.USER:
            pending_user.append(row)
        else:
            await flush_user()
            if messages and messages[-1]["role"] == "assistant":
                messages[-1]["content"] += "\n\n" + (row.text or "")
            elif messages:
                messages.append({"role": "assistant", "content": row.text or ""})
            # ответ ассистента без предшествующего user-хода игнорируем
    await flush_user()
    return messages


class BaseLlm:
    def __init__(self, settings: Settings, system_prompt: str):
        self.settings = settings
        self.system_prompt = system_prompt

    async def _complete(self, system: list[dict], messages: list[dict]) -> Completion:
        raise NotImplementedError

    async def ping(self) -> Completion:
        return await self._complete(
            [{"type": "text", "text": "Отвечай одним словом."}],
            [{"role": "user", "content": "Скажи «ок»."}],
        )

    def build_system(self, last_posts: list[str], today: date, theme: Theme | None = None) -> list[dict]:
        static: dict = {"type": "text", "text": self.system_prompt}
        if self.settings.llm_prompt_cache:
            static["cache_control"] = {"type": "ephemeral"}
        if last_posts:
            joined = "\n".join(
                f"<post_{i + 1}>\n{p.strip()}\n</post_{i + 1}>" for i, p in enumerate(last_posts)
            )
            posts_block = f"<last_posts>\n{joined}\n</last_posts>"
        else:
            posts_block = "<last_posts>\n(пока ничего не публиковалось)\n</last_posts>"
        theme_block = f"Тема поста: {theme.title}. {theme.prompt_hint}\n\n" if theme else ""
        dynamic = (
            f"Сегодня: {format_date_ru(today)}.\n\n{theme_block}"
            f"Последние опубликованные посты, новые первыми:\n{posts_block}"
        )
        return [static, {"type": "text", "text": dynamic}]

    async def _complete_checked(self, system: list[dict], messages: list[dict]) -> Completion:
        completion = await self._complete(system, messages)
        if completion.stop_reason == "max_tokens":
            log.warning("Ответ обрезан по max_tokens, повторяю")
            completion = await self._complete(system, messages)
            if completion.stop_reason == "max_tokens":
                raise LlmFormatError("ответ дважды обрезан по max_tokens")
        if completion.stop_reason == "refusal":
            raise LlmError(texts.LLM_REFUSAL, "stop_reason=refusal", retryable=False)
        return completion

    async def _parsed(self, system: list[dict], messages: list[dict]) -> tuple[ParsedReply, str]:
        try:
            completion = await self._complete_checked(system, messages)
            return parse_reply(completion.text), completion.text
        except LlmFormatError as exc:
            log.warning("Неверный формат ответа (%s), повторяю", exc.detail)
            completion = await self._complete_checked(system, messages)
            return parse_reply(completion.text), completion.text

    async def generate(
        self,
        messages: list[dict],
        *,
        last_posts: list[str],
        has_photos: bool,
        today: date,
        theme: Theme | None = None,
    ) -> LlmReply:
        system = self.build_system(last_posts, today, theme)
        parsed, raw = await self._parsed(system, messages)
        if parsed.kind == "question":
            return LlmReply("question", parsed.body)

        notes: list[str] = []
        fallback = FallbackMode.NORMAL

        # 1. Валидность HTML
        html, plain = await self._normalize_or_fix(parsed.body, raw, system, messages, notes)
        if html is None:
            html, plain = html_tools.to_plain_html(parsed.body)
            fallback = FallbackMode.PLAIN
            notes.append(texts.PREVIEW_NOTE_PLAIN)

        # 2. Длина
        limit = html_tools.CAPTION_LIMIT if has_photos else html_tools.TEXT_LIMIT
        if html_tools.utf16_len(plain) > limit:
            shortened = await self._shorten(html, plain, limit, system, messages)
            if shortened is not None:
                html, plain, short_fallback = shortened
                if short_fallback == FallbackMode.PLAIN and fallback != FallbackMode.PLAIN:
                    fallback = FallbackMode.PLAIN
                    notes.append(texts.PREVIEW_NOTE_PLAIN)
            if html_tools.utf16_len(plain) > limit:
                # html к этому моменту либо валиден, либо уже экранированный plain, split безопасен
                fallback = FallbackMode.SPLIT
                if has_photos:
                    notes.append(texts.PREVIEW_NOTE_SPLIT)

        draft = Draft(html=html, plain=plain, fallback=fallback, notes=notes)
        return LlmReply("draft", f"<post>\n{html}\n</post>", draft)

    async def _normalize_or_fix(
        self, body: str, raw: str, system: list[dict], messages: list[dict], notes: list[str]
    ) -> tuple[str | None, str | None]:
        try:
            return html_tools.normalize(body)
        except html_tools.HtmlInvalid as exc:
            reason = str(exc)
            log.warning("Невалидный HTML от модели: %s. Прошу исправить", reason)
        retry_messages = messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": texts.FIX_HTML_PROMPT.format(reason=reason)},
        ]
        try:
            parsed, _ = await self._parsed(system, retry_messages)
            if parsed.kind == "draft":
                return html_tools.normalize(parsed.body)
            log.warning("На запрос исправить HTML модель ответила вопросом, снимаю разметку")
        except html_tools.HtmlInvalid as exc2:
            log.warning("HTML невалиден и после исправления: %s. Снимаю разметку", exc2)
        except LlmError as exc2:
            log.warning("Не удалось получить исправленный HTML: %s. Снимаю разметку", exc2.detail)
        return None, None

    async def _shorten(
        self, html: str, plain: str, limit: int, system: list[dict], messages: list[dict]
    ) -> tuple[str, str, FallbackMode] | None:
        length = html_tools.utf16_len(plain)
        target = max(limit - 50, 200)
        log.info("Пост длиннее лимита (%d > %d), прошу сократить до %d", length, limit, target)
        retry_messages = messages + [
            {"role": "assistant", "content": f"<post>\n{html}\n</post>"},
            {"role": "user", "content": texts.SHORTEN_PROMPT.format(length=length, limit=limit, target=target)},
        ]
        try:
            parsed, _ = await self._parsed(system, retry_messages)
        except LlmError as exc:
            log.warning("Не удалось сократить пост: %s", exc.detail)
            return None
        if parsed.kind != "draft":
            log.warning("На запрос сократить модель ответила вопросом")
            return None
        try:
            new_html, new_plain = html_tools.normalize(parsed.body)
            return new_html, new_plain, FallbackMode.NORMAL
        except html_tools.HtmlInvalid as exc:
            log.warning("Сокращённый пост с невалидным HTML: %s. Снимаю разметку", exc)
            new_html, new_plain = html_tools.to_plain_html(parsed.body)
            return new_html, new_plain, FallbackMode.PLAIN


class AnthropicLlm(BaseLlm):
    def __init__(self, settings: Settings, system_prompt: str):
        super().__init__(settings, system_prompt)
        from anthropic import AsyncAnthropic

        kwargs: dict = {
            "api_key": settings.anthropic_api_key or None,
            "timeout": settings.llm_timeout,
            "max_retries": settings.llm_max_retries,
        }
        if settings.anthropic_base_url:
            kwargs["base_url"] = settings.anthropic_base_url
        self.client = AsyncAnthropic(**kwargs)

    async def _complete(self, system: list[dict], messages: list[dict]) -> Completion:
        import anthropic

        params: dict = {
            "model": self.settings.anthropic_model,
            "max_tokens": self.settings.llm_max_tokens,
            "system": system,
            "messages": messages,
        }
        if self.settings.llm_effort:
            params["output_config"] = {"effort": self.settings.llm_effort}
        try:
            if self.settings.llm_server_fallback:
                response = await self.client.beta.messages.create(
                    **params,
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                )
            else:
                response = await self.client.messages.create(**params)
        except anthropic.RateLimitError as exc:
            raise LlmError(texts.LLM_RATE_LIMIT, f"429: {exc.message}") from exc
        except anthropic.APIStatusError as exc:
            detail = f"{exc.status_code}: {exc.message} (request_id={getattr(exc, 'request_id', None)})"
            log.error("Ошибка API модели: %s", detail)
            raise LlmError(texts.LLM_API_ERROR, detail, retryable=exc.status_code >= 500) from exc
        except anthropic.APITimeoutError as exc:
            raise LlmError(texts.LLM_CONNECTION, f"timeout: {exc}") from exc
        except anthropic.APIConnectionError as exc:
            raise LlmError(texts.LLM_CONNECTION, f"connection: {exc}") from exc

        text = "".join(block.text for block in response.content if block.type == "text")
        usage = {}
        if response.usage is not None:
            usage = {
                "input": response.usage.input_tokens,
                "output": response.usage.output_tokens,
                "cache_read": getattr(response.usage, "cache_read_input_tokens", None),
            }
        request_id = getattr(response, "_request_id", None)
        log.info(
            "Ответ модели: stop=%s request_id=%s usage=%s", response.stop_reason, request_id, usage
        )
        return Completion(text=text, stop_reason=response.stop_reason, request_id=request_id, usage=usage)


class FakeLlm(BaseLlm):
    """Читает ответ из файла при каждом вызове. Для тестов и отладки без API."""

    DEFAULT_REPLY = "<post>\n<b>Тестовый пост</b>\nЭто ответ фейковой модели.\n</post>"

    def __init__(self, settings: Settings, system_prompt: str, reply_file: str = ""):
        super().__init__(settings, system_prompt)
        self.reply_file = reply_file or settings.llm_fake_reply_file
        self.calls: list[tuple[list[dict], list[dict]]] = []

    async def _complete(self, system: list[dict], messages: list[dict]) -> Completion:
        self.calls.append((system, messages))
        if self.reply_file and Path(self.reply_file).exists():
            text = Path(self.reply_file).read_text(encoding="utf-8")
        else:
            text = self.DEFAULT_REPLY
        return Completion(text=text, stop_reason="end_turn", request_id="fake")


def make_llm(settings: Settings, system_prompt: str) -> BaseLlm:
    if settings.llm_provider == "fake":
        return FakeLlm(settings, system_prompt)
    return AnthropicLlm(settings, system_prompt)
