"""
check_connection.py — Проверка связи с Telegram и первичная авторизация.

Первый запуск (без сессии): интерактивная авторизация —
нужно ввести номер телефона и код из Telegram (однократно, ~2 минуты).
Повторные запуски: быстрая проверка сессии, аккаунта и доступа к чатам.
"""

from __future__ import annotations

import asyncio
import logging

import fire
from dotenv import load_dotenv

from chat_search import tg_client
from chat_search.config import DEFAULT_CHATS_FILE, load_chats

logger = logging.getLogger(__name__)


async def run(chat_name: str | None = None) -> None:
    """
    Проверить связь с Telegram: авторизация, get_me, доступ к чату.

    Args:
        chat_name: Имя чата из configs/chats.yaml для проверки доступа
            (по умолчанию — первый; без конфига проверка пропускается)
    """
    api_id, api_hash = tg_client.get_credentials()
    logger.info("Подключение...")

    client = tg_client.make_client(api_id, api_hash)
    await client.start()

    try:
        me = await client.get_me()
        logger.info(
            "Аккаунт: %s (username: %s, id: %s)",
            me.first_name or "?", me.username or "-", me.id,
        )

        chats: dict[str, int] | None
        try:
            chats = load_chats(DEFAULT_CHATS_FILE)
        except FileNotFoundError:
            if chat_name is not None:
                raise ValueError(
                    f"Чат '{chat_name}' указан, но конфиг {DEFAULT_CHATS_FILE} "
                    f"не найден. Скопируйте configs/chats.example.yaml в configs/chats.yaml."
                ) from None
            logger.warning(
                "Конфиг %s не найден — проверка доступа к чату пропускается",
                DEFAULT_CHATS_FILE,
            )
            chats = None

        if chats is not None:
            if chat_name is None:
                chat_name = next(iter(chats))
            if chat_name not in chats:
                raise ValueError(f"Неизвестный чат: {chat_name}. Доступны: {sorted(chats)}")

            entity = await client.get_entity(chats[chat_name])
            logger.info("Доступ к чату подтверждён: %s (id: %s)", chat_name, entity.id)

        print("Соединение с Telegram работает.")
    finally:
        await client.disconnect()


def main(chat_name: str | None = None) -> None:
    """Entry point — загружает .env и запускает check_connection."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )
    load_dotenv()
    asyncio.run(run(chat_name))


if __name__ == "__main__":
    fire.Fire(main)
