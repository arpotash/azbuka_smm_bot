"""Загрузка фото из Telegram или по URL с кэшем на диске и подготовка к отправке в модель."""

from __future__ import annotations

import base64
import hashlib
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from aiogram.types import BufferedInputFile

log = logging.getLogger(__name__)


def detect_media_type(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/jpeg"


def is_url(file_id: str) -> bool:
    return file_id.startswith(("http://", "https://"))


def url_unique_id(url: str) -> str:
    return "url_" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:24]


class MediaStore:
    def __init__(self, bot, cache_dir: Path, fetch_url: Callable[[str], Awaitable[bytes]] | None = None):
        self.bot = bot
        self.cache_dir = cache_dir
        self.fetch_url = fetch_url

    def _path(self, file_unique_id: str) -> Path:
        return self.cache_dir / f"{file_unique_id}.bin"

    async def get_bytes(self, file_id: str, file_unique_id: str) -> bytes:
        path = self._path(file_unique_id)
        if path.exists():
            return path.read_bytes()
        if is_url(file_id):
            if self.fetch_url is None:
                raise RuntimeError("MediaStore: загрузка по URL не настроена")
            data = await self.fetch_url(file_id)
        else:
            buffer = await self.bot.download(file_id)
            data = buffer.read() if hasattr(buffer, "read") else bytes(buffer)
        path.write_bytes(data)
        log.debug("Фото %s загружено, %d байт", file_unique_id, len(data))
        return data

    async def image_block(self, file_id: str, file_unique_id: str) -> dict:
        data = await self.get_bytes(file_id, file_unique_id)
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": detect_media_type(data),
                "data": base64.standard_b64encode(data).decode("ascii"),
            },
        }

    async def telegram_input(self, file_id: str, file_unique_id: str) -> str | BufferedInputFile:
        """Что передать в send_photo: file_id как есть, фото по URL — байтами, чтобы Telegram сам не качал."""
        if not is_url(file_id):
            return file_id
        data = await self.get_bytes(file_id, file_unique_id)
        ext = detect_media_type(data).split("/")[-1]
        return BufferedInputFile(data, filename=f"{file_unique_id}.{ext}")
