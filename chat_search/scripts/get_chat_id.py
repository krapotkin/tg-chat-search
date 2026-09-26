"""
get_chat_id.py — Получить числовой ID Telegram-канала или чата
по юзернейму или ссылке.
"""

from __future__ import annotations

import asyncio
import logging

import fire
from dotenv import load_dotenv

from chat_search import tg_client

logger = logging.getLogger(__name__)


async def run(channel: str) -> None:
    """
    Получить числовой ID телеграм-канала или чата.

    Args:
        channel: Имя канала, юзернейм или ссылка (например, 'some_channel' или 'https://t.me/some_channel')
    """
    api_id, api_hash = tg_client.get_credentials()
    client = tg_client.make_client(api_id, api_hash)
    await client.start()

    try:
        chat = await client.get_entity(channel)
        print(f"Chat ID: {chat.id}")
        print(f"Chat title: {chat.title}")
    finally:
        await client.disconnect()


def main(channel: str) -> None:
    """Entry point — загружает .env и запускает get_chat_id."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )
    load_dotenv()
    asyncio.run(run(channel))


if __name__ == "__main__":
    fire.Fire(main)
