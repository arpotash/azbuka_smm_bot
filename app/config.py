"""Настройки приложения. Все значения берутся из .env или переменных окружения."""

from __future__ import annotations

from functools import cached_property
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.themes import ScheduleEntry, parse_schedule_plan


def _parse_ids(raw: str) -> set[int]:
    return {int(part) for part in raw.replace(";", ",").split(",") if part.strip()}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Telegram
    bot_token: str = Field(alias="BOT_TOKEN")
    channel_id: int = Field(alias="CHANNEL_ID")
    channel_username: str = Field(default="", alias="CHANNEL_USERNAME")
    manager_ids_raw: str = Field(alias="MANAGER_IDS")
    admin_ids_raw: str = Field(default="", alias="ADMIN_IDS")

    # Расписание и темы
    timezone: str = Field(default="Europe/Moscow", alias="TIMEZONE")
    schedule_plan: str = Field(
        default="mon 12:00 promo; wed 12:00 product; fri 12:00 news", alias="SCHEDULE_PLAN"
    )
    catch_up_on_start: bool = Field(default=True, alias="CATCH_UP_ON_START")
    debounce_sec: float = Field(default=1.5, alias="DEBOUNCE_SEC")

    # Подсказки с сайта и из новостей
    site_api_url: str = Field(default="https://as-lesa.ru/api", alias="SITE_API_URL")
    news_feeds_raw: str = Field(default="", alias="NEWS_FEEDS")
    news_max_age_days: int = Field(default=14, alias="NEWS_MAX_AGE_DAYS")
    suggest_count: int = Field(default=3, alias="SUGGEST_COUNT")
    suggest_product_repeat_days: int = Field(default=180, alias="SUGGEST_PRODUCT_REPEAT_DAYS")
    suggest_shown_repeat_days: int = Field(default=30, alias="SUGGEST_SHOWN_REPEAT_DAYS")
    suggest_category_repeat_days: int = Field(default=45, alias="SUGGEST_CATEGORY_REPEAT_DAYS")

    # LLM
    llm_provider: str = Field(default="anthropic", alias="LLM_PROVIDER")  # anthropic | fake
    llm_fake_reply_file: str = Field(default="", alias="LLM_FAKE_REPLY_FILE")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    anthropic_base_url: str = Field(default="", alias="ANTHROPIC_BASE_URL")
    anthropic_model: str = Field(default="claude-opus-5", alias="ANTHROPIC_MODEL")
    llm_effort: str = Field(default="", alias="LLM_EFFORT")
    llm_max_tokens: int = Field(default=8000, alias="LLM_MAX_TOKENS")
    llm_timeout: float = Field(default=120.0, alias="LLM_TIMEOUT")
    llm_max_retries: int = Field(default=2, alias="LLM_MAX_RETRIES")
    llm_prompt_cache: bool = Field(default=True, alias="LLM_PROMPT_CACHE")
    llm_server_fallback: bool = Field(default=False, alias="LLM_SERVER_FALLBACK")
    llm_error_alert_threshold: int = Field(default=3, alias="LLM_ERROR_ALERT_THRESHOLD")
    last_posts_n: int = Field(default=10, alias="LAST_POSTS_N")

    # Пути
    data_dir: str = Field(default="./data", alias="DATA_DIR")
    prompt_path: str = Field(default="prompts/system.md", alias="PROMPT_PATH")
    site_url: str = Field(default="https://as-lesa.ru", alias="SITE_URL")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @cached_property
    def manager_ids(self) -> set[int]:
        return _parse_ids(self.manager_ids_raw)

    @cached_property
    def admin_ids(self) -> set[int]:
        ids = _parse_ids(self.admin_ids_raw)
        return ids or self.manager_ids

    @cached_property
    def schedule(self) -> list[ScheduleEntry]:
        return parse_schedule_plan(self.schedule_plan)

    @cached_property
    def news_feeds(self) -> list[str]:
        return [u.strip() for u in self.news_feeds_raw.replace(";", ",").split(",") if u.strip()]

    @cached_property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @cached_property
    def data_path(self) -> Path:
        path = Path(self.data_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def db_path(self) -> Path:
        return self.data_path / "bot.db"

    @property
    def media_dir(self) -> Path:
        path = self.data_path / "media"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def load_system_prompt(self) -> str:
        return Path(self.prompt_path).read_text(encoding="utf-8")
