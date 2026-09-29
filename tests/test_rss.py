from datetime import UTC, datetime

from app.services.rss import parse_feed

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Банный портал</title>
  <item>
    <title>Как выбрать печь для бани &amp; сауны</title>
    <link>https://example.com/pech</link>
    <description><![CDATA[<p>Разбираем <b>дровяные</b> и электрические печи.</p>]]></description>
    <pubDate>Fri, 25 Sep 2026 10:00:00 +0300</pubDate>
  </item>
  <item>
    <title>Без ссылки, но с guid</title>
    <guid>https://example.com/guid-link</guid>
    <pubDate>invalid date</pubDate>
  </item>
  <item><title>Нет ни ссылки, ни guid</title></item>
</channel></rss>"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Лесной журнал</title>
  <entry>
    <title>Цены на пиломатериалы осенью</title>
    <link rel="self" href="https://example.org/self"/>
    <link rel="alternate" href="https://example.org/prices"/>
    <summary>Коротко о ценах.</summary>
    <updated>2026-09-24T08:15:00Z</updated>
  </entry>
</feed>"""


def test_parse_rss2():
    items = parse_feed(RSS, "https://example.com/feed.xml")
    assert len(items) == 2
    first = items[0]
    assert first.title == "Как выбрать печь для бани & сауны"
    assert first.link == "https://example.com/pech"
    assert first.summary == "Разбираем дровяные и электрические печи."
    assert first.published == datetime(2026, 9, 25, 7, 0, tzinfo=UTC)
    assert first.source == "example.com"
    assert items[1].link == "https://example.com/guid-link"
    assert items[1].published is None


def test_parse_atom():
    items = parse_feed(ATOM, "https://example.org/atom")
    assert len(items) == 1
    assert items[0].link == "https://example.org/prices"
    assert items[0].published == datetime(2026, 9, 24, 8, 15, tzinfo=UTC)
    assert items[0].summary == "Коротко о ценах."


def test_parse_garbage_returns_empty():
    assert parse_feed("<html>not a feed", "https://x") == []
    assert parse_feed("", "https://x") == []
