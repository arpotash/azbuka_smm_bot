"""Разбор RSS 2.0 и Atom стандартной библиотекой."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

from app.services.html_tools import strip_tags

log = logging.getLogger(__name__)

ATOM = "{http://www.w3.org/2005/Atom}"


@dataclass
class FeedItem:
    title: str
    link: str
    summary: str
    published: datetime | None
    source: str   # домен источника


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _text(el: ET.Element | None) -> str:
    if el is None:
        return ""
    return strip_tags(el.text or "").strip()


def parse_feed(xml_text: str, feed_url: str) -> list[FeedItem]:
    feed_source = urlparse(feed_url).netloc or feed_url

    def source_of(link: str) -> str:
        return urlparse(link).netloc or feed_source

    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError as exc:
        log.warning("Лента %s не разобрана: %s", feed_url, exc)
        return []

    items: list[FeedItem] = []
    if root.tag == f"{ATOM}feed":
        for entry in root.findall(f"{ATOM}entry"):
            link = ""
            for link_el in entry.findall(f"{ATOM}link"):
                rel = link_el.get("rel", "alternate")
                if rel == "alternate" and link_el.get("href"):
                    link = link_el.get("href", "")
                    break
            summary = _text(entry.find(f"{ATOM}summary")) or _text(entry.find(f"{ATOM}content"))
            published = _parse_date(
                (entry.findtext(f"{ATOM}published") or entry.findtext(f"{ATOM}updated"))
            )
            title = _text(entry.find(f"{ATOM}title"))
            if title and link:
                items.append(FeedItem(title, link, summary, published, source_of(link)))
        return items

    channel = root.find("channel") if root.tag == "rss" else root
    if channel is None:
        return []
    for item in channel.findall("item"):
        title = _text(item.find("title"))
        link = (item.findtext("link") or "").strip()
        if not link:
            guid = item.find("guid")
            if guid is not None and guid.get("isPermaLink", "true") != "false":
                link = (guid.text or "").strip()
        summary = _text(item.find("description"))
        published = _parse_date(item.findtext("pubDate") or item.findtext("{http://purl.org/dc/elements/1.1/}date"))
        if title and link:
            items.append(FeedItem(title, link, summary, published, source_of(link)))
    return items
