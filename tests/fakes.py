"""Заглушки Telegram-бота и модели для тестов без сети."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest

from app.services.llm import BaseLlm, Completion

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


@dataclass
class Sent:
    method: str
    chat_id: int
    kwargs: dict
    message_id: int


class FakeBot:
    def __init__(self, reject_html: bool = False):
        self.sent: list[Sent] = []
        self.edited: list[tuple[int, int]] = []
        self.reject_html = reject_html
        self._next_id = 100

    def _msg(self, method: str, chat_id: int, **kwargs) -> SimpleNamespace:
        self._next_id += 1
        self.sent.append(Sent(method, chat_id, kwargs, self._next_id))
        return SimpleNamespace(message_id=self._next_id, chat=SimpleNamespace(id=chat_id))

    def _check_html(self, text: str | None, parse_mode) -> None:
        if self.reject_html and text and parse_mode is not None and "<" in text:
            raise TelegramBadRequest(
                method=SimpleNamespace(), message="Bad Request: can't parse entities: unsupported start tag"
            )

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None, **kw):
        self._check_html(text, parse_mode)
        return self._msg("send_message", chat_id, text=text, parse_mode=parse_mode, reply_markup=reply_markup)

    async def send_photo(self, chat_id, photo, caption=None, parse_mode=None, reply_markup=None, **kw):
        self._check_html(caption, parse_mode)
        return self._msg(
            "send_photo", chat_id, photo=photo, caption=caption, parse_mode=parse_mode, reply_markup=reply_markup
        )

    async def send_media_group(self, chat_id, media, **kw):
        for item in media:
            self._check_html(item.caption, item.parse_mode)
        return [self._msg("send_media_group", chat_id, media=item) for item in media]

    async def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None, **kw):
        self.edited.append((chat_id, message_id))

    async def send_chat_action(self, chat_id, action, **kw):
        return True

    async def download(self, file_id, **kw):
        return io.BytesIO(JPEG)

    def by_method(self, method: str, chat_id: int | None = None) -> list[Sent]:
        return [s for s in self.sent if s.method == method and (chat_id is None or s.chat_id == chat_id)]


class ScriptedLlm(BaseLlm):
    """Отдаёт ответы по списку. Последний ответ повторяется, если список кончился."""

    def __init__(self, settings, replies: list[str]):
        super().__init__(settings, "system prompt for tests")
        self.replies = list(replies)
        self.calls: list[tuple[list[dict], list[dict]]] = []

    async def _complete(self, system, messages) -> Completion:
        self.calls.append((system, messages))
        idx = min(len(self.calls) - 1, len(self.replies) - 1)
        return Completion(text=self.replies[idx], stop_reason="end_turn", request_id=f"r{len(self.calls)}")


def make_message(
    user_id: int,
    text: str | None = None,
    *,
    message_id: int,
    photo: bool = False,
    caption: str | None = None,
    media_group_id: str | None = None,
    file_id: str | None = None,
) -> SimpleNamespace:
    photo_sizes = None
    if photo:
        fid = file_id or f"file{message_id}"
        photo_sizes = [SimpleNamespace(file_id=fid, file_unique_id=f"u{fid}", width=1280, height=960)]
    return SimpleNamespace(
        message_id=message_id,
        from_user=SimpleNamespace(id=user_id, full_name=f"Менеджер {user_id}"),
        text=text,
        caption=caption,
        photo=photo_sizes,
        media_group_id=media_group_id,
        answer=AsyncMock(),
    )


class FakeSiteApi:
    """Заглушка API сайта с небольшим каталогом."""

    def __init__(self):
        from app.services.site_api import SiteApiError  # noqa: F401
        self.site_url = "https://as-lesa.ru"
        self.fail_categories = False
        self.fail_urls: set[str] = set()
        self.calls: list[tuple] = []
        self.categories = [
            {"uuid": "cat_a", "name": "Полковая доска из липы", "path": ["ПОГОНАЖНЫЕ ИЗДЕЛИЯ", "Полковая доска", "Полковая доска из липы"]},
            {"uuid": "cat_b", "name": "Ковши", "path": ["Бондарные изделия из дуба", "Ковши"]},
            {"uuid": "cat_c", "name": "Шапки", "path": ["Банный текстиль", "Шапки"]},
            {"uuid": "cat_d", "name": "Веники", "path": ["Красота и здоровье", "Веники"]},
            {"uuid": "cat_empty", "name": "Пустая", "path": ["Пустая"]},
        ]
        def prod(uuid, name, price=100, image="img", count=5, discount=None, measure="шт."):
            return {"uuid": uuid, "name": name, "price": price, "image": f"https://storage/{uuid}.jpeg" if image else None,
                    "description": "desc", "discount": discount, "count": count, "measure": measure, "is_novelty": False}
        self.products = {
            "cat_a": [prod("a1", "Полок Липа А 2,0 м", 320, measure="м.п."), prod("nophoto", "Без фото", image=None),
                      prod("noprice", "Без цены", price=0), prod("nostock", "Нет в наличии", count=0)],
            "cat_b": [prod("b1", "Ковш дубовый 0,5 л", 900), prod("b2", "Ковш дубовый 1 л", 1200)],
            "cat_c": [prod("c1", "Шапка банная", 450)],
            "cat_d": [prod("d1", "Веник берёзовый", 250)],
            "cat_empty": [],
        }
        self.promoted = [prod("promo1", "Полок Осина С 1,3 м", 100, discount=30)]
        self.blog = [
            {"uuid": "a1", "title": "Как выбрать дверь для бани", "subtitle": "Залог комфорта", "text": "<p>Дверь…</p>",
             "image": "https://storage/a1.jpeg", "category_name": "Бани"},
            {"uuid": "a2", "title": "Камни для бани", "subtitle": "", "text": "<p>Камни…</p>", "image": None, "category_name": "Камни"},
        ]
        self.texts = {
            "https://feed.example/rss": """<rss version="2.0"><channel>
                <item><title>Свежая новость</title><link>https://news.example/fresh</link><description>Анонс</description>
                <pubDate>{fresh}</pubDate></item>
                <item><title>Старая новость</title><link>https://news.example/old</link><pubDate>{old}</pubDate></item>
                </channel></rss>""",
            "https://news.example/fresh": '<html><head><meta property="og:image" content="https://news.example/og.jpg"></head>'
                                          '<body><nav>меню сайта</nav><article><p>Это основной текст новости.</p></article></body></html>',
        }

    async def leaf_categories(self):
        from app.services.site_api import SiteApiError
        if self.fail_categories:
            raise SiteApiError("site down")
        return self.categories

    async def category_products(self, uuid, page=1):
        self.calls.append(("category_products", uuid, page))
        items = self.products[uuid]
        return len(items), items if page == 1 else []

    async def promoted_products(self):
        return self.promoted

    async def product(self, uuid):
        return {"uuid": uuid, "name": "Полок Липа А (90*27) 2,0 м", "price": 320, "measure": "м.п.", "count": 118,
                "discount": None, "description": "Единица измерения: м.п.\nЛипа не нагревается.",
                "image": ["https://storage/p_full_1.jpeg", "https://storage/p_full_2.jpeg"],
                "feature": [{"name": "Порода", "value": "липа", "measure": None}, {"name": "Длина", "value": "2.0", "measure": "м"}],
                "category": {"uuid": "cat_a", "name": "Полковая доска из липы"}}

    async def blog_articles(self):
        return self.blog

    async def get_text(self, url):
        from datetime import UTC, datetime, timedelta
        from email.utils import format_datetime
        from app.services.site_api import SiteApiError
        if url in self.fail_urls or url not in self.texts:
            raise SiteApiError(f"{url}: HTTP 404")
        now = datetime.now(UTC)
        return self.texts[url].format(fresh=format_datetime(now - timedelta(days=1)), old=format_datetime(now - timedelta(days=60)))

    async def get_bytes(self, url):
        return JPEG

    def product_url(self, uuid):
        return f"{self.site_url}/catalog/product/{uuid}"

    def blog_url(self, uuid):
        return f"{self.site_url}/blog/{uuid}"
