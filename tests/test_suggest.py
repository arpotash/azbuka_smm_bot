import random

import pytest

from app.models import Suggestion
from app.services.site_api import SiteApiError
from app.services.suggest import Suggester, extract_page_text
from tests.fakes import FakeSiteApi


@pytest.fixture
def site():
    return FakeSiteApi()


@pytest.fixture
def suggester(site):
    return Suggester(site, feeds=["https://feed.example/rss"], rng=random.Random(1))


async def test_products_filters_and_distinct_categories(suggester, site):
    result = await suggester.for_theme("product", exclude=set(), n=3)
    assert len(result) == 3
    refs = [s.ref for s in result]
    assert len(set(refs)) == 3
    # без фото, без цены и без остатка не предлагаются
    assert not {"nophoto", "noprice", "nostock"} & set(refs)
    # категории не повторяются
    cats = [s.payload["category_uuid"] for s in result]
    assert len(set(cats)) == 3
    assert all(s.url.startswith("https://as-lesa.ru/catalog/product/") for s in result)
    assert all("₽" in s.line for s in result)


async def test_products_respect_exclusions(suggester):
    first = await suggester.for_theme("product", exclude=set(), n=3)
    exclude = {s.ref for s in first}
    second = await suggester.for_theme("product", exclude=exclude, n=3)
    assert not exclude & {s.ref for s in second}


async def test_products_exclude_categories(suggester):
    result = await suggester.for_theme("product", exclude=set(), n=3, exclude_categories={"cat_a", "cat_b"})
    assert all(s.payload["category_uuid"] not in {"cat_a", "cat_b"} for s in result)


async def test_promo(suggester, site):
    result = await suggester.for_theme("promo", exclude=set(), n=3)
    assert [s.ref for s in result] == ["promo1"]
    assert result[0].kind == "promo" and "скидка 30%" in result[0].line
    assert await suggester.for_theme("promo", exclude={"promo1"}, n=3) == []
    site.promoted[0]["count"] = 0  # акция на товар, которого нет, не предлагается
    assert await suggester.for_theme("promo", exclude=set(), n=3) == []


def test_number_and_stock_formatting():
    from app.services.suggest import _price_line, _stock_line
    assert _price_line({"price": 168, "measure": "пог.м."}) == "168 ₽ / пог.м."
    assert _price_line({"price": 99.5, "measure": "шт.", "discount": 30}) == "99,5 ₽ / шт., скидка 30%"
    assert _stock_line({"count": 222.0, "measure": "пог.м."}) == "в наличии: 222 пог.м."
    assert _stock_line({"count": 5.7, "measure": "пог.м."}) == "в наличии: 5,7 пог.м."
    assert _stock_line({"count": 0}) == "нет в наличии"


def test_clean_text_keeps_paragraph_breaks():
    from app.services.suggest import _clean_text
    text = _clean_text("<p>Первый абзац.</p><p>Второй абзац.</p><br>Третий", 500)
    assert "абзац.Второй" not in text
    assert text.split("\n")[0] == "Первый абзац."


async def test_news_mixes_blog_and_rss(suggester):
    result = await suggester.for_theme("news", exclude=set(), n=3)
    kinds = [s.kind for s in result]
    assert kinds[0] == "blog"
    assert "rss" in kinds
    rss = next(s for s in result if s.kind == "rss")
    assert rss.ref == "https://news.example/fresh"  # свежая, старая (60 дней) отфильтрована
    assert "news.example" in rss.line


async def test_news_without_feeds_only_blog(site):
    s = Suggester(site, feeds=[], rng=random.Random(2))
    result = await s.for_theme("news", exclude=set(), n=3)
    assert result and all(x.kind == "blog" for x in result)


async def test_free_theme_no_suggestions(suggester):
    assert await suggester.for_theme("free", exclude=set(), n=3) == []


async def test_materialize_product(suggester):
    s = Suggestion(kind="product", ref="p_full", title="Полок", url="https://as-lesa.ru/catalog/product/p_full",
                   image_url="https://storage/p_full_list.jpeg", payload={"category": "Полковая доска"})
    material = await suggester.materialize(s)
    assert material.text.startswith("Карточка товара с сайта")
    assert "Название: Полок Липа А (90*27) 2,0 м" in material.text
    assert "Цена: 320 ₽ / м.п." in material.text
    assert "- Порода: липа" in material.text
    assert "Ссылка на товар: https://as-lesa.ru/catalog/product/p_full" in material.text
    assert material.photo_urls == ["https://storage/p_full_1.jpeg", "https://storage/p_full_2.jpeg"]


async def test_materialize_blog(suggester):
    s = Suggestion(kind="blog", ref="a1", title="Как выбрать дверь", url="https://as-lesa.ru/blog/a1",
                   image_url="https://storage/a1.jpeg", payload={"subtitle": "Залог комфорта", "text": "<p>Дверь <b>важна</b>.</p><p>Второй абзац.</p>"})
    material = await suggester.materialize(s)
    assert "Источник: статья из блога" in material.text
    assert "Дверь важна." in material.text and "<b>" not in material.text
    assert material.photo_urls == ["https://storage/a1.jpeg"]


async def test_materialize_rss_fetches_page(suggester, site):
    s = Suggestion(kind="rss", ref="https://news.example/fresh", title="Новость", url="https://news.example/fresh",
                   payload={"summary": "Анонс", "source": "news.example"})
    material = await suggester.materialize(s)
    assert "Источник: news.example" in material.text
    assert "Текст со страницы источника:" in material.text and "основной текст новости" in material.text
    assert "меню сайта" not in material.text
    assert material.photo_urls == ["https://news.example/og.jpg"]


async def test_materialize_rss_page_unavailable(suggester, site):
    site.fail_urls.add("https://news.example/broken")
    s = Suggestion(kind="rss", ref="https://news.example/broken", title="Новость", url="https://news.example/broken",
                   payload={"summary": "Анонс", "source": "news.example"})
    material = await suggester.materialize(s)
    assert "Анонс" in material.text and material.photo_urls == []


async def test_products_site_down_raises(site):
    site.fail_categories = True
    s = Suggester(site, feeds=[])
    with pytest.raises(SiteApiError):
        await s.for_theme("product", exclude=set(), n=3)


def test_extract_page_text():
    html = """<html><head><meta property="og:image" content="https://x/og.jpg"></head>
    <body><nav>меню</nav><article><h1>Заголовок</h1><p>Первый абзац.</p><script>var a=1</script><p>Второй.</p></article>
    <footer>подвал</footer></body></html>"""
    text, og = extract_page_text(html)
    assert og == "https://x/og.jpg"
    assert "Первый абзац." in text and "Второй." in text
    assert "меню" not in text and "var a" not in text and "подвал" not in text
