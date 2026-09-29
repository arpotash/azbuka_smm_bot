from __future__ import annotations

import pytest

from app.config import Settings
from app.db import Database


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        BOT_TOKEN="123:test",
        CHANNEL_ID=-1001234567890,
        MANAGER_IDS="1,2",
        ADMIN_IDS="1",
        DATA_DIR=str(tmp_path / "data"),
        LLM_PROVIDER="fake",
        DEBOUNCE_SEC=0.05,
        LLM_ERROR_ALERT_THRESHOLD=2,
        SUGGEST_COUNT=3,
    )


@pytest.fixture
async def db(settings) -> Database:
    database = Database(settings.db_path)
    await database.connect()
    yield database
    await database.close()
