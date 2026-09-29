"""Подсказки для менеджера: товары с сайта, акции, статьи блога, новости из RSS."""

from __future__ import annotations

import asyncio
import html as html_lib
import logging
import math
import random
import re
from datetime import UTC, datetime, timedelta

from app import texts
from app.models import Material, Suggestion
from app.services.html_tools import strip_tags
from app.services.rss import FeedItem, parse_feed
from app.services.site_api import PAGE_SIZE, SiteApi, SiteApiError

log = logging.getLogger(__name__)

PRODUCT_ATTEMPTS = 12
DESCRIPTION_LIMIT = 1500
ARTICLE_LIMIT = 4000
PAGE_TEXT_LIMIT = 6000
MAX_PHOTOS = 3


def _num(value) -> str:
    """Число без лишних нулей: 222.0 → 222, 5.7 → 5,7."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def _price_line(item: dict) -> str:
    price = item.get("price")
    measure = item.get("measure") or "шт."
    discount = item.get("discount")
    if not price:
        return "цена по запросу"
    line = f"{_num(price)} ₽ / {measure}"
    if discount:
        line += f", скидка {_num(discount)}%"
    return line


def _in_stock(item: dict) -> bool:
    return (item.get("count") or 0) > 0


def _stock_line(item: dict) -> str:
    count = item.get("count")
    if count is None:
        return ""
    if count <= 0:
        return "нет в наличии"
    return f"в наличии: {_num(count)} {item.get('measure') or 'шт.'}"


BLOCK_TAG_RE = re.compile(r"<(?:/?(?:p|div|li|h[1-6]|tr|blockquote)|br\s*/?)[^>]*>", re.I)


def _clean_text(raw: str, limit: int) -> str:
    text = strip_tags(BLOCK_TAG_RE.sub("\n", raw or ""))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "…"
    return text


def extract_page_text(html: str) -> tuple[str, str | None]:
    """Грубо вытаскивает основной текст страницы и og:image."""
    og = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', html, re.I)
    og_image = html_lib.unescape(og.group(1)) if og else None
    if not og_image:
        og = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', html, re.I)
        og_image = html_lib.unescape(og.group(1)) if og else None
    body = re.sub(r"<(script|style|noscript|svg|nav|footer|header|form)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    article = re.search(r"<article[^>]*>(.*?)</article>", body, re.S | re.I)
    if article:
        body = article.group(1)
    body = re.sub(r"<(p|div|br|li|h[1-6]|tr)[^>]*>", "\n", body, flags=re.I)
    text = _clean_text(body, PAGE_TEXT_LIMIT)
    return text, og_image


class Suggester:
    def __init__(self, site: SiteApi, feeds: list[str], news_max_age_days: int = 14, rng: random.Random | None = None):
        self.site = site
        self.feeds = feeds
        self.news_max_age = timedelta(days=news_max_age_days)
        self.rng = rng or random.Random()

    # ------------------------------------------------------------------ выбор

    async def for_theme(
        self, theme_key: str, exclude: set[str], n: int = 3, exclude_categories: set[str] | None = None
    ) -> list[Suggestion]:
        if theme_key == "product":
            return await self._products(exclude, n, exclude_categories or set())
        if theme_key == "promo":
            return await self._promo(exclude, n)
        if theme_key == "news":
            return await self._news(exclude, n)
        return []

    async def _products(self, exclude: set[str], n: int, exclude_categories: set[str]) -> list[Suggestion]:
        leaves = await self.site.leaf_categories()
        picked: list[Suggestion] = []
        used_categories: set[str] = set(exclude_categories)
        attempts = 0
        while len(picked) < n and attempts < PRODUCT_ATTEMPTS:
            attempts += 1
            leaf = self.rng.choice(leaves)
            if leaf["uuid"] in used_categories:
                continue
            try:
                count, results = await self.site.category_products(leaf["uuid"], page=1)
                pages = max(1, math.ceil(count / PAGE_SIZE))
                if pages > 1:
                    page = self.rng.randint(1, pages)
                    if page != 1:
                        _, results = await self.site.category_products(leaf["uuid"], page=page)
            except SiteApiError as exc:
                log.warning("Категория %s не загрузилась: %s", leaf["name"], exc)
                continue
            candidates = [
                r for r in results
                if r.get("image") and (r.get("price") or 0) > 0 and _in_stock(r)
                and r["uuid"] not in exclude and r["uuid"] not in {p.ref for p in picked}
            ]
            if not candidates:
                continue
            item = self.rng.choice(candidates)
            used_categories.add(leaf["uuid"])
            picked.append(self._product_suggestion(item, "product", " › ".join(leaf["path"][-2:]), leaf["uuid"]))
        return picked

    async def _promo(self, exclude: set[str], n: int) -> list[Suggestion]:
        items = await self.site.promoted_products()
        result: list[Suggestion] = []
        for item in items:
            if item["uuid"] in exclude or not item.get("image") or not _in_stock(item):
                continue
            result.append(self._product_suggestion(item, "promo", "акция"))
            if len(result) >= n:
                break
        return result

    def _product_suggestion(self, item: dict, kind: str, category: str, category_uuid: str | None = None) -> Suggestion:
        parts = [category, _price_line(item)]
        stock = _stock_line(item)
        if stock:
            parts.append(stock)
        return Suggestion(
            kind=kind,
            ref=item["uuid"],
            title=item.get("name", "").strip(),
            line=", ".join(p for p in parts if p),
            url=self.site.product_url(item["uuid"]),
            image_url=item.get("image"),
            payload={"category": category, "category_uuid": category_uuid},
        )

    async def _news(self, exclude: set[str], n: int) -> list[Suggestion]:
        blog: list[Suggestion] = []
        try:
            for article in await self.site.blog_articles():
                if article["uuid"] in exclude:
                    continue
                blog.append(Suggestion(
                    kind="blog",
                    ref=article["uuid"],
                    title=article.get("title", "").strip(),
                    line=f"блог as-lesa.ru, рубрика «{article.get('category_name', '')}»",
                    url=self.site.blog_url(article["uuid"]),
                    image_url=(article.get("image") or None),
                    payload={"subtitle": article.get("subtitle") or "", "text": article.get("text") or ""},
                ))
        except SiteApiError as exc:
            log.warning("Блог не загрузился: %s", exc)
        self.rng.shuffle(blog)

        rss = await self._rss_items(exclude)
        result: list[Suggestion] = []
        if blog:
            result.append(blog.pop(0))
        for item in rss:
            if len(result) >= n:
                break
            result.append(item)
        for item in blog:
            if len(result) >= n:
                break
            result.append(item)
        return result[:n]

    async def _rss_items(self, exclude: set[str]) -> list[Suggestion]:
        if not self.feeds:
            return []
        fetched = await asyncio.gather(*(self._fetch_feed(url) for url in self.feeds), return_exceptions=True)
        items: list[FeedItem] = []
        for url, result in zip(self.feeds, fetched, strict=True):
            if isinstance(result, BaseException):
                log.warning("Лента %s не загрузилась: %s", url, result)
                continue
            items.extend(result)
        cutoff = datetime.now(UTC) - self.news_max_age
        fresh = [i for i in items if i.link not in exclude and (i.published is None or i.published >= cutoff)]
        fresh.sort(key=lambda i: i.published or datetime.min.replace(tzinfo=UTC), reverse=True)
        seen: set[str] = set()
        result: list[Suggestion] = []
        for item in fresh:
            if item.link in seen:
                continue
            seen.add(item.link)
            date = item.published.astimezone().strftime("%d.%m") if item.published else ""
            result.append(Suggestion(
                kind="rss",
                ref=item.link,
                title=item.title,
                line=", ".join(p for p in (item.source, date) if p),
                url=item.link,
                payload={"summary": item.summary, "source": item.source},
            ))
        return result

    async def _fetch_feed(self, url: str) -> list[FeedItem]:
        xml = await self.site.get_text(url)
        return parse_feed(xml, url)

    # ------------------------------------------------------------------ материал

    async def materialize(self, s: Suggestion) -> Material:
        if s.kind in ("product", "promo"):
            return await self._product_material(s)
        if s.kind == "blog":
            return self._blog_material(s)
        if s.kind == "rss":
            return await self._rss_material(s)
        raise ValueError(f"неизвестный вид подсказки {s.kind}")

    async def _product_material(self, s: Suggestion) -> Material:
        card = await self.site.product(s.ref)
        header = texts.MATERIAL_PROMO_HEADER if s.kind == "promo" else texts.MATERIAL_PRODUCT_HEADER
        lines = [header, "", f"Название: {card.get('name', s.title)}"]
        category = (card.get("category") or {}).get("name") or s.payload.get("category")
        if category:
            lines.append(f"Категория: {category}")
        lines.append(f"Цена: {_price_line(card)}")
        stock = _stock_line(card)
        if stock:
            lines.append(f"Наличие: {stock}")
        features = card.get("feature") or []
        if features:
            lines.append("Характеристики:")
            for f in features:
                value = f"{f.get('value', '')} {f.get('measure') or ''}".strip()
                lines.append(f"- {f.get('name', '')}: {value}")
        description = _clean_text(card.get("description") or "", DESCRIPTION_LIMIT)
        if description:
            lines += ["", "Описание с сайта:", description]
        lines += ["", f"Ссылка на товар: {s.url}"]
        photos = [u for u in (card.get("image") or []) if u][:MAX_PHOTOS] or ([s.image_url] if s.image_url else [])
        return Material("\n".join(lines), photos)

    def _blog_material(self, s: Suggestion) -> Material:
        lines = [texts.MATERIAL_BLOG_HEADER, "", f"Заголовок: {s.title}"]
        if s.payload.get("subtitle"):
            lines.append(f"Подзаголовок: {s.payload['subtitle']}")
        text = _clean_text(s.payload.get("text") or "", ARTICLE_LIMIT)
        if text:
            lines += ["", "Текст статьи:", text]
        lines += ["", f"Ссылка на статью: {s.url}"]
        return Material("\n".join(lines), [s.image_url] if s.image_url else [])

    async def _rss_material(self, s: Suggestion) -> Material:
        source = s.payload.get("source") or s.url
        lines = [texts.MATERIAL_SOURCE_HEADER.format(source=source), "", f"Заголовок: {s.title}"]
        summary = _clean_text(s.payload.get("summary") or "", 1000)
        if summary:
            lines.append(f"Анонс: {summary}")
        photo: str | None = None
        try:
            page = await self.site.get_text(s.url)
            text, og_image = extract_page_text(page)
            photo = og_image
            if text:
                lines += ["", "Текст со страницы источника:", text]
        except SiteApiError as exc:
            log.warning("Страница новости %s не загрузилась: %s", s.url, exc)
        lines += ["", f"Ссылка на источник: {s.url}"]
        return Material("\n".join(lines), [photo] if photo else [])
