"""
tg_client.py — Общие хелперы Telethon: создание клиента.

Все Telethon-скрипты проекта используют этот модуль:
- make_client() — создание TelegramClient, сессии хранятся в <project>/sessions/
- get_credentials() — чтение TG_API_ID / TG_API_HASH из окружения
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from telethon import TelegramClient

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
SESSIONS_DIR: Path = PROJECT_ROOT / "sessions"


def get_credentials() -> tuple[int, str]:
    """Прочитать TG_API_ID / TG_API_HASH из окружения (.env)."""
    api_id = os.getenv("TG_API_ID")
    api_hash = os.getenv("TG_API_HASH")

    if not api_id or not api_hash:
        raise RuntimeError("Установите TG_API_ID и TG_API_HASH в .env файле")

    return int(api_id), api_hash


def make_client(
    api_id: int,
    api_hash: str,
    session_name: str = "get_chat",
) -> TelegramClient:
    """
    Создать TelegramClient.

    Сессия хранится в <project>/sessions/<session_name>.session
    (директория игнорируется git).
    """
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    kwargs: dict[str, Any] = {
        "session": str(SESSIONS_DIR / session_name),
        "api_id": api_id,
        "api_hash": api_hash,
    }

    return TelegramClient(**kwargs)
