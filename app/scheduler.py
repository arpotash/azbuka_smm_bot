"""Тематическое расписание напоминаний и догон пропущенного запуска после рестарта."""

from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import Settings
from app.models import SlotKind
from app.services.slots import SlotService
from app.themes import ScheduleEntry

log = logging.getLogger(__name__)


async def open_scheduled_slot(slot_service: SlotService, settings: Settings, theme: str) -> None:
    today = datetime.now(settings.tz).date().isoformat()
    slot = await slot_service.open_slot(SlotKind.SCHEDULED, theme=theme, scheduled_for=today)
    if slot is None:
        log.info("Плановый слот на %s уже есть", today)
    else:
        log.info("Плановый слот #%d на %s (тема %s) создан", slot.id, today, theme)


def build_scheduler(settings: Settings, slot_service: SlotService) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.tz)
    for entry in settings.schedule:
        scheduler.add_job(
            open_scheduled_slot,
            CronTrigger(day_of_week=entry.day_code, hour=entry.hour, minute=entry.minute, timezone=settings.tz),
            args=[slot_service, settings, entry.theme],
            id=f"slot_{entry.day_code}_{entry.hour:02d}{entry.minute:02d}_{entry.theme}",
            misfire_grace_time=3600,
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )
    return scheduler


def due_entries(schedule: list[ScheduleEntry], now: datetime) -> list[ScheduleEntry]:
    """Записи плана на сегодня, время которых уже прошло."""
    return [
        e for e in schedule
        if e.day == now.weekday() and (e.hour, e.minute) <= (now.hour, now.minute)
    ]


async def catch_up(settings: Settings, slot_service: SlotService) -> None:
    """Если процесс был выключен в момент cron, создаёт слот на сегодня один раз."""
    if not settings.catch_up_on_start:
        return
    now = datetime.now(settings.tz)
    due = due_entries(settings.schedule, now)
    if not due:
        return
    today = now.date().isoformat()
    if await slot_service.db.slots.exists_for_date(today):
        return
    entry = max(due, key=lambda e: (e.hour, e.minute))  # самая поздняя из прошедших
    log.info("Догоняю пропущенный запуск планировщика за %s (тема %s)", today, entry.theme)
    await open_scheduled_slot(slot_service, settings, entry.theme)
