"""Темы постов и разбор тематического расписания."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    key: str
    title: str
    reminder_hint: str   # что написать менеджеру в напоминании
    prompt_hint: str     # что сказать модели о теме
    suggests: bool       # бот предлагает варианты


THEMES: dict[str, Theme] = {
    "promo": Theme(
        key="promo",
        title="Поставки и акции",
        reminder_hint=(
            "Расскажите, что пришло на склад на этой неделе или что сейчас по акции: "
            "товар, цена, условия, сколько в наличии. Фото со склада приветствуются."
        ),
        prompt_hint=(
            "Пост о поставке или акции. Главное: что именно появилось или подешевело, цена, "
            "сколько есть, где забрать. Цена со скидкой и без только если обе названы. "
            "Срок акции упоминать только если менеджер его назвал."
        ),
        suggests=True,
    ),
    "product": Theme(
        key="product",
        title="Товар или категория",
        reminder_hint=(
            "Пост об одном товаре или подборке из категории. Выберите вариант ниже "
            "или пришлите свой: название, характеристики, ссылку, фото."
        ),
        prompt_hint=(
            "Пост о товаре или категории товаров. Объясни, для чего он, чем отличается от аналогов "
            "(порода дерева, сорт, размеры, обработка), кому подойдёт. Цена и наличие из карточки. "
            "Ссылка на карточку товара обязательна."
        ),
        suggests=True,
    ),
    "news": Theme(
        key="news",
        title="Новости компании, индустрии и бани",
        reminder_hint=(
            "Новость компании, полезная статья или новость индустрии. Выберите вариант ниже "
            "или напишите свою тему."
        ),
        prompt_hint=(
            "Пост-новость или полезная заметка. Если материал пришёл из источника, перескажи его "
            "своими словами, не копируй абзацы, факты бери только из текста источника и обязательно "
            "поставь ссылку на источник. Свяжи тему с баней, деревом или ассортиментом магазина, "
            "если это уместно и не притянуто."
        ),
        suggests=True,
    ),
    "free": Theme(
        key="free",
        title="Свободная тема",
        reminder_hint="Пришлите материал: текст, фото, ссылку на товар.",
        prompt_hint="Тема свободная: ориентируйся на материал менеджера.",
        suggests=False,
    ),
}

DAY_INDEX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
DAY_NAMES_RU = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


@dataclass(frozen=True)
class ScheduleEntry:
    day: int        # 0 = понедельник
    hour: int
    minute: int
    theme: str

    @property
    def day_code(self) -> str:
        return next(code for code, idx in DAY_INDEX.items() if idx == self.day)

    @property
    def day_name(self) -> str:
        return DAY_NAMES_RU[self.day]


_ENTRY_RE = re.compile(r"^\s*([a-z]{3})\s+(\d{1,2}):(\d{2})\s+([a-z_]+)\s*$", re.IGNORECASE)


def parse_schedule_plan(raw: str) -> list[ScheduleEntry]:
    """Разбирает строку вида 'mon 12:00 promo; wed 12:00 product; fri 12:00 news'."""
    entries: list[ScheduleEntry] = []
    for chunk in raw.split(";"):
        if not chunk.strip():
            continue
        match = _ENTRY_RE.match(chunk)
        if not match:
            raise ValueError(f"SCHEDULE_PLAN: не разобрать запись {chunk.strip()!r}, ожидается 'mon 12:00 promo'")
        day_code, hour, minute, theme = match.groups()
        day_code, theme = day_code.lower(), theme.lower()
        if day_code not in DAY_INDEX:
            raise ValueError(f"SCHEDULE_PLAN: неизвестный день {day_code!r}")
        if theme not in THEMES:
            raise ValueError(f"SCHEDULE_PLAN: неизвестная тема {theme!r}, доступны {', '.join(THEMES)}")
        hour_i, minute_i = int(hour), int(minute)
        if not (0 <= hour_i < 24 and 0 <= minute_i < 60):
            raise ValueError(f"SCHEDULE_PLAN: неверное время в записи {chunk.strip()!r}")
        entries.append(ScheduleEntry(DAY_INDEX[day_code], hour_i, minute_i, theme))
    if not entries:
        raise ValueError("SCHEDULE_PLAN пуст")
    return entries


def get_theme(key: str | None) -> Theme:
    return THEMES.get(key or "free", THEMES["free"])
