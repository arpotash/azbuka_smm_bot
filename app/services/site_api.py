"""Клиент открытого JSON API сайта as-lesa.ru и загрузка внешних страниц."""

from __future__ import annotations

import logging
import time
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
PAGE_SIZE = 8
CATEGORY_CACHE_TTL = 24 * 3600


class SiteApiError(Exception):
    pass


def flatten_leaf_categories(tree: list[dict], path: tuple[str, ...] = ()) -> list[dict]:
    """Листья дерева категорий с путём до корня."""
    leaves: list[dict] = []
    for node in tree:
        name = node.get("name", "").strip()
        subs = node.get("sub_categories") or []
        if subs:
            leaves.extend(flatten_leaf_categories(subs, path + (name,)))
        else:
            leaves.append({"uuid": node["uuid"], "name": name, "path": list(path) + [name]})
    return leaves


def parse_products_page(payload: dict) -> tuple[int, list[dict]]:
    products = payload.get("products") or {}
    return int(products.get("count") or 0), list(products.get("results") or [])


def flatten_blog(payload: list[dict]) -> list[dict]:
    articles: list[dict] = []
    for category in payload or []:
        for article in category.get("articles") or []:
            item = dict(article)
            item["category_name"] = category.get("name", "")
            articles.append(item)
    return articles


class SiteApi:
    def __init__(self, base_url: str, site_url: str, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.site_url = site_url.rstrip("/")
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None
        self._leaves_cache: tuple[float, list[dict]] | None = None

    # ------------------------------------------------------------------ HTTP

    async def _session_get(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self.timeout, headers={"User-Agent": USER_AGENT, "Accept": "*/*"}
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def get_json(self, path: str, params: dict | None = None) -> Any:
        url = path if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"
        try:
            session = await self._session_get()
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    raise SiteApiError(f"{url}: HTTP {resp.status}")
                return await resp.json(content_type=None)
        except aiohttp.ClientError as exc:
            raise SiteApiError(f"{url}: {exc}") from exc
        except TimeoutError as exc:
            raise SiteApiError(f"{url}: timeout") from exc

    async def get_text(self, url: str, max_bytes: int = 2_000_000) -> str:
        try:
            session = await self._session_get()
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise SiteApiError(f"{url}: HTTP {resp.status}")
                data = await resp.content.read(max_bytes)
                encoding = resp.charset or "utf-8"
                return data.decode(encoding, errors="replace")
        except aiohttp.ClientError as exc:
            raise SiteApiError(f"{url}: {exc}") from exc
        except TimeoutError as exc:
            raise SiteApiError(f"{url}: timeout") from exc

    async def get_bytes(self, url: str, max_bytes: int = 10_000_000) -> bytes:
        try:
            session = await self._session_get()
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise SiteApiError(f"{url}: HTTP {resp.status}")
                return await resp.content.read(max_bytes)
        except aiohttp.ClientError as exc:
            raise SiteApiError(f"{url}: {exc}") from exc
        except TimeoutError as exc:
            raise SiteApiError(f"{url}: timeout") from exc

    # ------------------------------------------------------------------ каталог

    async def leaf_categories(self) -> list[dict]:
        now = time.monotonic()
        if self._leaves_cache and now - self._leaves_cache[0] < CATEGORY_CACHE_TTL:
            return self._leaves_cache[1]
        tree = await self.get_json("/product/category")
        leaves = flatten_leaf_categories(tree)
        if not leaves:
            raise SiteApiError("дерево категорий пустое")
        self._leaves_cache = (now, leaves)
        return leaves

    async def category_products(self, category_uuid: str, page: int = 1) -> tuple[int, list[dict]]:
        payload = await self.get_json(
            "/product/category/products", {"category_uuid": category_uuid, "page": page}
        )
        return parse_products_page(payload)

    async def promoted_products(self, max_pages: int = 2) -> list[dict]:
        items: list[dict] = []
        for page in range(1, max_pages + 1):
            payload = await self.get_json("/product/category/products", {"is_promoted": "true", "page": page})
            count, results = parse_products_page(payload)
            items.extend(results)
            if page * PAGE_SIZE >= count or not results:
                break
        return items

    async def product(self, uuid: str) -> dict:
        return await self.get_json(f"/product/{uuid}")

    async def blog_articles(self) -> list[dict]:
        payload = await self.get_json("/blog/category/article")
        return flatten_blog(payload)

    # ------------------------------------------------------------------ ссылки

    def product_url(self, uuid: str) -> str:
        return f"{self.site_url}/catalog/product/{uuid}"

    def blog_url(self, uuid: str) -> str:
        return f"{self.site_url}/blog/{uuid}"
