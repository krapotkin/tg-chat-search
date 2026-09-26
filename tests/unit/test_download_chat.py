"""Тесты download_chat.py: загрузка сообщений из чатов через Telethon.

Покрытые риски:
- load_existing_message_ids: отсутствие файла, битые строки (не должны ронять).
- message_record: медиа-only/пустой текст -> None, reply_to (есть/нет/битый).
- download_chat: логика быстрого resume (остановка на первом известном ID),
  пропуск сообщений без текста, full_scan (не останавливается рано).

Telethon-клиент и Message заменены фейками — сеть и авторизация не нужны.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from chat_search.scripts.download_chat import (
    download_chat,
    load_existing_message_ids,
    message_record,
)

CHAT_NAME = "test_chat"
CHAT_ID = -1001234567890
DATE = datetime(2024, 1, 1, tzinfo=UTC)


# --- Фейки Telethon ---------------------------------------------------------


class FakeReplyTo:
    def __init__(self, reply_to_msg_id: int) -> None:
        self.reply_to_msg_id = reply_to_msg_id


class BrokenReplyTo:
    @property
    def reply_to_msg_id(self) -> int:
        raise ValueError("повреждённый reply_to")


class FakeMessage:
    """Минимальная имитация telethon Message для message_record/download_chat."""

    def __init__(self, msg_id: int, text: str | None, reply_to: object | None = None) -> None:
        self.id = msg_id
        self.message = text
        self.date = DATE
        self.reply_to = reply_to


class FakeClient:
    """Имитация TelegramClient.iter_messages: отдаёт список сообщений."""

    def __init__(self, messages: list[FakeMessage]) -> None:
        self._messages = messages

    def iter_messages(self, chat_id: int, limit: int | None = None):
        msgs = self._messages[:limit] if limit is not None else self._messages

        async def _gen():
            for m in msgs:
                yield m

        return _gen()


def _write_jsonl(path: Path, ids: list[int]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for i in ids:
            f.write(json.dumps({"id": i, "text": f"t{i}"}) + "\n")


def _read_ids(path: Path) -> list[int]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line)["id"] for line in f if line.strip()]


def _run(coro) -> int:
    return asyncio.run(coro)


# --- load_existing_message_ids ---------------------------------------------


def test_load_existing_ids_no_file(tmp_path: Path) -> None:
    assert load_existing_message_ids(str(tmp_path / "нет.jsonl")) == set()


def test_load_existing_ids_valid(tmp_path: Path) -> None:
    fp = tmp_path / "ch.jsonl"
    _write_jsonl(fp, [10, 20, 30])
    assert load_existing_message_ids(str(fp)) == {10, 20, 30}


def test_load_existing_ids_skips_corrupt(tmp_path: Path) -> None:
    """Битые строки и строки без 'id' пропускаются, валидные читаются."""
    fp = tmp_path / "ch.jsonl"
    with open(fp, "w", encoding="utf-8") as f:
        f.write(json.dumps({"id": 10}) + "\n")
        f.write("это не json\n")
        f.write(json.dumps({"text": "без id"}) + "\n")
        f.write(json.dumps({"id": 20}) + "\n")
    assert load_existing_message_ids(str(fp)) == {10, 20}


# --- message_record ---------------------------------------------------------


def test_message_record_with_text() -> None:
    rec = message_record(FakeMessage(5, "привет"), CHAT_NAME, CHAT_ID)
    assert rec is not None
    assert rec["id"] == 5
    assert rec["text"] == "привет"
    assert rec["url"] == f"https://t.me/{CHAT_NAME}/5"
    assert rec["chat_id"] == CHAT_ID
    assert rec["source"] == CHAT_NAME
    assert rec["type"] == "message"
    assert rec["reply_to_message_id"] is None
    assert rec["pub_time"] == int(DATE.timestamp())


def test_message_record_no_text_returns_none() -> None:
    """Медиа-only / сервисное сообщение (без текста) -> None."""
    assert message_record(FakeMessage(5, None), CHAT_NAME, CHAT_ID) is None


def test_message_record_whitespace_only_returns_none() -> None:
    assert message_record(FakeMessage(5, "   \n  "), CHAT_NAME, CHAT_ID) is None


def test_message_record_reply_to_present() -> None:
    rec = message_record(FakeMessage(5, "ответ", reply_to=FakeReplyTo(42)), CHAT_NAME, CHAT_ID)
    assert rec is not None
    assert rec["reply_to_message_id"] == 42


def test_message_record_reply_to_broken() -> None:
    """Повреждённый reply_to не роняет сборку записи."""
    rec = message_record(FakeMessage(5, "текст", reply_to=BrokenReplyTo()), CHAT_NAME, CHAT_ID)
    assert rec is not None
    assert rec["reply_to_message_id"] is None


# --- download_chat: resume --------------------------------------------------


def test_download_resume_stops_at_known_id(tmp_path: Path) -> None:
    """Быстрый resume: новые (200, 150) добавляются, на известном 102 стоп."""
    fp = tmp_path / "ch.jsonl"
    _write_jsonl(fp, [100, 101, 102])
    client = FakeClient([
        FakeMessage(200, "новый"),
        FakeMessage(150, "новый"),
        FakeMessage(102, "старый"),   # первый известный -> стоп
        FakeMessage(101, "старый"),
        FakeMessage(100, "старый"),
    ])
    new_count = _run(download_chat(client, CHAT_NAME, CHAT_ID, str(fp)))
    assert new_count == 2
    assert set(_read_ids(fp)) == {100, 101, 102, 150, 200}


def test_download_resume_no_new_messages(tmp_path: Path) -> None:
    """Если первое сообщение уже известно — стоп сразу, ничего не добавляется."""
    fp = tmp_path / "ch.jsonl"
    _write_jsonl(fp, [100, 101, 102])
    client = FakeClient([FakeMessage(102, "x"), FakeMessage(101, "x"), FakeMessage(100, "x")])
    new_count = _run(download_chat(client, CHAT_NAME, CHAT_ID, str(fp)))
    assert new_count == 0
    assert _read_ids(fp) == [100, 101, 102]


def test_download_skips_no_text_messages(tmp_path: Path) -> None:
    """Сообщения без текста пропускаются, но не останавливают обход."""
    fp = tmp_path / "ch.jsonl"
    _write_jsonl(fp, [100])
    client = FakeClient([
        FakeMessage(200, None),        # медиа-only -> пропуск
        FakeMessage(150, "текст"),     # добавляется
        FakeMessage(100, "старый"),    # известный -> стоп
    ])
    new_count = _run(download_chat(client, CHAT_NAME, CHAT_ID, str(fp)))
    assert new_count == 1
    assert set(_read_ids(fp)) == {100, 150}


def test_download_full_scan_reaches_old_messages(tmp_path: Path) -> None:
    """full_scan: не останавливается на известном ID, доходит до старых."""
    fp = tmp_path / "ch.jsonl"
    _write_jsonl(fp, [100])
    client = FakeClient([
        FakeMessage(200, "новый"),
        FakeMessage(100, "старый"),    # в resume здесь был бы стоп
        FakeMessage(50, "очень старый"),
    ])
    new_count = _run(download_chat(client, CHAT_NAME, CHAT_ID, str(fp), full_scan=True))
    # дошёл до 50 (прошёл мимо известного 100)
    assert 50 in _read_ids(fp)
    assert new_count >= 2


def test_download_full_scan_no_duplicates_on_rerun(tmp_path: Path) -> None:
    """Повторный прогон full_scan не создаёт дубликаты уже скачанных ID.

    Сценарий: первый прогон уже положил в файл 100/150/200. Повторный full_scan
    видит те же сообщения (клиент отдаёт всю историю) + новое 250. Ожидаем, что
    новое добавится, а старые — нет (иначе в файле появятся дубли).
    """
    fp = tmp_path / "ch.jsonl"
    _write_jsonl(fp, [100, 150, 200])
    client = FakeClient([
        FakeMessage(250, "новое"),
        FakeMessage(200, "старое"),
        FakeMessage(150, "старое"),
        FakeMessage(100, "старое"),
    ])
    new_count = _run(download_chat(client, CHAT_NAME, CHAT_ID, str(fp), full_scan=True))
    ids = _read_ids(fp)
    assert len(ids) == len(set(ids)), f"появились дубликаты: {sorted(ids)}"
    assert set(ids) == {100, 150, 200, 250}
    assert new_count == 1, "добавлено должно быть только новое сообщение"


def test_download_preserves_existing_content(tmp_path: Path) -> None:
    """Повторный прогон не перезаписывает содержимое уже скачанного сообщения.

    Клиент отдаёт то же сообщение 100, но с ДРУГИМ текстом (имитация того, что
    при повторной скачивании текст мог бы отличаться). Ожидаем, что в файле
    останется прежний текст, а не новый.
    """
    fp = tmp_path / "ch.jsonl"
    with open(fp, "w", encoding="utf-8") as f:
        f.write(json.dumps({"id": 100, "text": "оригинал"}) + "\n")
    client = FakeClient([
        FakeMessage(200, "новое"),
        FakeMessage(100, "ИЗМЕНЁННЫЙ ТЕКСТ"),  # если бы перезаписывал — стал бы этот
    ])
    _run(download_chat(client, CHAT_NAME, CHAT_ID, str(fp), full_scan=True))
    ids = _read_ids(fp)
    assert ids.count(100) == 1, "появился дубликат id 100"
    with open(fp, encoding="utf-8") as f:
        recs = {json.loads(line)["id"]: json.loads(line) for line in f if line.strip()}
    assert recs[100]["text"] == "оригинал", "существующее сообщение перезаписано"
    assert 200 in recs, "новое сообщение не добавлено"


def test_download_limit(tmp_path: Path) -> None:
    """limit ограничивает число итераций."""
    fp = tmp_path / "ch.jsonl"
    client = FakeClient([FakeMessage(i, f"t{i}") for i in range(10, 20)])
    new_count = _run(download_chat(client, CHAT_NAME, CHAT_ID, str(fp), limit=3))
    assert new_count == 3
    assert len(_read_ids(fp)) == 3
