"""
download_chat.py — Скачать сообщения из Telegram-чатов через Telethon.

Поддерживает быструю докачку: ID сообщений растут со временем, поэтому
при повторном запуске итерируем от новых к старым и останавливаемся на
первом уже скачанном сообщении (всё более старое уже в файле).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import fire
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.types import Message
from tqdm.asyncio import tqdm

from chat_search import tg_client
from chat_search.config import DEFAULT_CHATS_FILE, load_chats

logger = logging.getLogger(__name__)

URL_TEMPLATE = "https://t.me/{}/{}"
BATCH_SIZE = 1000
DELAY_AFTER_BATCH = 0.5


def load_existing_message_ids(file_path: str) -> set[int]:
    """Загрузить ID уже скачанных сообщений из JSONL файла."""
    existing_ids: set[int] = set()
    if not os.path.exists(file_path):
        return existing_ids

    with open(file_path, encoding="utf-8") as f:
        for line in f:
            try:
                record = json.loads(line.strip())
                existing_ids.add(record["id"])
            except (json.JSONDecodeError, KeyError):
                continue

    return existing_ids


def message_record(message: Message, chat_name: str, chat_id: int) -> dict[str, Any] | None:
    """
    Собрать запись из сообщения.

    Возвращает None, если у сообщения нет текста (медиа-only, сервисные и т.п.) —
    такие сообщения в датасет не попадают.
    """
    text = (message.message or "").strip()
    if not text:
        return None

    try:
        reply_to_message_id = (
            message.reply_to.reply_to_msg_id if message.reply_to else None
        )
    except Exception:
        reply_to_message_id = None

    return {
        "id": message.id,
        "url": URL_TEMPLATE.format(chat_name, message.id),
        "type": "message",
        "text": text,
        "reply_to_message_id": reply_to_message_id,
        "chat_id": chat_id,
        "pub_time": int(message.date.timestamp()),
        "source": chat_name,
    }


async def download_chat(
    client: TelegramClient,
    chat_name: str,
    chat_id: int,
    output_file: str,
    full_scan: bool = False,
    limit: int | None = None,
) -> int:
    """
    Скачать (докачать) сообщения одного чата в JSONL.

    Args:
        client: Подключённый Telethon-клиент
        chat_name: Имя чата (используется в имени файла и поле source)
        chat_id: Числовой ID чата
        output_file: Путь к JSONL файлу
        full_scan: Если True — пройти всю историю (как в старой версии).
            По умолчанию (False) останавливаемся на первом уже скачанном ID.
        limit: Ограничить число итераций (для тестов)

    Returns:
        Число добавленных новых сообщений.
    """
    existing_ids = load_existing_message_ids(output_file)
    logger.info(
        "Чат %s: уже скачано %d сообщений (полный проход: %s)",
        chat_name, len(existing_ids), full_scan,
    )

    new_count = 0
    processed = 0

    with open(output_file, "a", encoding="utf-8") as f:
        async for message in tqdm(
            client.iter_messages(chat_id, limit=limit), desc=chat_name
        ):
            processed += 1

            if message.id in existing_ids:
                if not full_scan:
                    # ID монотонно растут: дошли до известного хвоста —
                    # всё более старое уже скачано в предыдущих прогонах.
                    logger.info("Чат %s: достигнут известный хвост (id=%d), остановка", chat_name, message.id)
                    break
                # full_scan: пропускаем уже скачанное, чтобы не создавать дубликаты
                continue

            record = message_record(message, chat_name, chat_id)
            if record is None:
                continue

            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            new_count += 1

            if processed % BATCH_SIZE == 0:
                f.flush()
                await asyncio.sleep(DELAY_AFTER_BATCH)

    logger.info("Чат %s: добавлено новых сообщений: %d", chat_name, new_count)
    return new_count


async def run(
    output_dir: str,
    chats_file: str = DEFAULT_CHATS_FILE,
    only: list[str] | None = None,
    full_scan: bool = False,
    limit: int | None = None,
) -> None:
    """Скачать сообщения из чатов (список — из YAML-конфига) через Telethon API."""
    # Конфиг читаем до подключения: сломанный/отсутствующий файл — ошибка без сети
    targets = load_chats(chats_file)

    api_id, api_hash = tg_client.get_credentials()
    client = tg_client.make_client(api_id, api_hash)
    await client.start()

    try:
        if only:
            unknown = [name for name in only if name not in targets]
            if unknown:
                raise ValueError(
                    f"Неизвестные чаты: {unknown}. Доступны: {sorted(targets)}"
                )
            targets = {name: targets[name] for name in only}

        os.makedirs(output_dir, exist_ok=True)

        for chat_name, chat_id in targets.items():
            logger.info("=" * 60)
            logger.info("Обработка чата: %s (id=%s)", chat_name, chat_id)
            output_file = os.path.join(output_dir, f"{chat_name}.jsonl")
            await download_chat(client, chat_name, chat_id, output_file, full_scan, limit)
    finally:
        await client.disconnect()


def main(
    output_dir: str,
    chats_file: str = DEFAULT_CHATS_FILE,
    only: list[str] | None = None,
    full_scan: bool = False,
    limit: int | None = None,
) -> None:
    """
    Скачать сообщения из Telegram-чатов (Telethon, JSONL, докачка).

    Args:
        output_dir: Директория для JSONL файлов
        chats_file: YAML-конфиг {имя: chat_id} (по умолчанию configs/chats.yaml)
        only: Скачать только перечисленные чаты из конфига
        full_scan: Пройти всю историю (по умолчанию — быстрый resume)
        limit: Ограничить число сообщений (для тестов)
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )
    load_dotenv()
    asyncio.run(run(output_dir, chats_file, only, full_scan, limit))


if __name__ == "__main__":
    fire.Fire(main)
