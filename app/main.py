"""Точка входа: настройки → БД → бот → планировщик → polling."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand

from app.config import Settings
from app.db import Database
from app.handlers import build_router
from app.middlewares import WhitelistMiddleware
from app.scheduler import build_scheduler, catch_up
from app.services.llm import make_llm
from app.services.media import MediaStore
from app.services.site_api import SiteApi
from app.services.slots import SlotService
from app.services.suggest import Suggester

log = logging.getLogger("app")

COMMANDS = [
    BotCommand(command="status", description="Что сейчас в работе"),
    BotCommand(command="cancel", description="Отменить текущий пост"),
    BotCommand(command="new", description="Открыть пост вручную: /new product|promo|news (админ)"),
    BotCommand(command="help", description="Справка"),
]


async def run() -> None:
    settings = Settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

    db = Database(settings.db_path)
    await db.connect()

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=None))
    site = SiteApi(settings.site_api_url, settings.site_url)
    media = MediaStore(bot, settings.media_dir, fetch_url=site.get_bytes)
    llm = make_llm(settings, settings.load_system_prompt())
    suggester = Suggester(site, settings.news_feeds, settings.news_max_age_days)
    slot_service = SlotService(bot, db, llm, media, settings, suggester=suggester)
    scheduler = build_scheduler(settings, slot_service)

    dp = Dispatcher()
    whitelist = WhitelistMiddleware(settings.manager_ids | settings.admin_ids)
    dp.message.outer_middleware(whitelist)
    dp.callback_query.outer_middleware(whitelist)
    dp.include_router(build_router())
    dp["slot_service"] = slot_service
    dp["settings"] = settings

    @dp.startup()
    async def on_startup() -> None:
        await bot.set_my_commands(COMMANDS)
        scheduler.start()
        await catch_up(settings, slot_service)
        me = await bot.get_me()
        plan = "; ".join(f"{e.day_code} {e.hour:02d}:{e.minute:02d} {e.theme}" for e in settings.schedule)
        log.info(
            "Бот @%s запущен. Менеджеры: %s, модель: %s через %s, расписание: %s, RSS-лент: %d",
            me.username, sorted(settings.manager_ids), settings.anthropic_model,
            settings.anthropic_base_url or "api.anthropic.com", plan, len(settings.news_feeds),
        )

    @dp.shutdown()
    async def on_shutdown() -> None:
        scheduler.shutdown(wait=False)
        await site.close()
        await db.close()

    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        await bot.session.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
