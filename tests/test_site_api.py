"""Разбор ответов API сайта на записанных образцах (структура снята с as-lesa.ru 2026-09-26)."""

from app.services.site_api import flatten_blog, flatten_leaf_categories, parse_products_page

CATEGORY_TREE = [
    {"uuid": "c1", "name": "ПОГОНАЖНЫЕ ИЗДЕЛИЯ", "sub_categories": [
        {"uuid": "c11", "name": "Полковая доска", "sub_categories": [
            {"uuid": "c111", "name": "Полковая доска из липы", "sub_categories": []},
            {"uuid": "c112", "name": "Полковая доска из осины", "sub_categories": []},
        ]},
        {"uuid": "c12", "name": "Брусок", "sub_categories": []},
    ]},
    {"uuid": "c2", "name": "Канат джутовый"},
]

PRODUCTS_PAGE = {
    "uuid": "c111", "name": "Полковая доска из липы", "image": None,
    "products": {
        "count": 209,
        "next": "http://as-lesa.ru/product/category/products?category_uuid=c111&page=2",
        "previous": None,
        "results": [
            {"uuid": "p1", "name": "Полок Липа А (90*27) 2,0 м", "price": 320, "image": "https://storage/p1.jpeg",
             "description": "Единица измерения: м.п.", "discount": None, "count": 118, "measure": "м.п.", "is_novelty": True},
        ],
    },
}

BLOG = [
    {"name": "Бани", "articles": [
        {"uuid": "a1", "title": "Как выбрать дверь для бани", "subtitle": "Залог комфорта", "text": "<p>Дверь…</p>", "image": None, "tag": []},
    ]},
    {"name": "Камни", "articles": []},
]


def test_flatten_leaf_categories():
    leaves = flatten_leaf_categories(CATEGORY_TREE)
    assert [l["uuid"] for l in leaves] == ["c111", "c112", "c12", "c2"]
    assert leaves[0]["path"] == ["ПОГОНАЖНЫЕ ИЗДЕЛИЯ", "Полковая доска", "Полковая доска из липы"]
    assert leaves[3]["path"] == ["Канат джутовый"]


def test_parse_products_page():
    count, results = parse_products_page(PRODUCTS_PAGE)
    assert count == 209
    assert results[0]["uuid"] == "p1" and results[0]["count"] == 118
    assert parse_products_page({"products": {}}) == (0, [])
    assert parse_products_page({}) == (0, [])


def test_flatten_blog():
    articles = flatten_blog(BLOG)
    assert len(articles) == 1
    assert articles[0]["uuid"] == "a1" and articles[0]["category_name"] == "Бани"
