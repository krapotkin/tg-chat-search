"""
dead.py — Мёртвые файлы в очереди: пометка, повторная проверка, зонд URL (часть 2).

Мёртвые файлы (HTTP 404/410, удалённое сообщение Telegram) исключаются из
списка и выборки скачивания по умолчанию. Пометка хранится в отдельном
{channel}/.dead.jsonl (строки queue.jsonl не изменяются — их «владеет» часть 1).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from chat_search.queue.models import DEAD_FILE, USER_AGENT, QueueFile

logger = logging.getLogger(__name__)

# Коды ошибок, при которых файл помечается мёртвым: HTTP 404/410 и
# удалённое сообщение Telegram (навсегда).
DEAD_ERROR_CODES = frozenset({"http_404", "http_410", "message_not_found"})

PROBE_TIMEOUT = 15


def _dead_path(attachments_dir: str, channel: str) -> Path:
    return Path(attachments_dir) / channel / DEAD_FILE


def load_dead(
    attachments_dir: str, channels: list[str] | None = None
) -> dict[tuple[str, int], dict[str, Any]]:
    """Прочитать все {channel}/.dead.jsonl: последняя запись по (channel, file_id) побеждает."""
    root = Path(attachments_dir)
    dead: dict[tuple[str, int], dict[str, Any]] = {}
    if not root.is_dir():
        return dead
    names = sorted(p.name for p in root.iterdir() if p.is_dir())
    if channels is not None:
        wanted = set(channels)
        names = [n for n in names if n in wanted]
    for name in names:
        path = root / name / DEAD_FILE
        if not path.is_file():
            continue
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Пропускаю битую строку в %s", path)
                    continue
                if not isinstance(rec, dict):
                    continue
                file_id = rec.get("file_id")
                if isinstance(file_id, int) and not isinstance(file_id, bool):
                    dead[(name, file_id)] = rec
    return dead


def mark_dead(
    attachments_dir: str,
    channel: str,
    file_id: int,
    reason: str,
    url: str | None = None,
    filename: str | None = None,
) -> None:
    """Дописать строку пометки мёртвого файла в {channel}/.dead.jsonl."""
    rec: dict[str, Any] = {
        "file_id": file_id,
        "reason": reason,
        "checked_at": datetime.now(tz=UTC).isoformat(timespec="seconds"),
    }
    if url is not None:
        rec["url"] = url
    if filename is not None:
        rec["filename"] = filename
    line = json.dumps(rec, ensure_ascii=False)
    path = _dead_path(attachments_dir, channel)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def unmark_dead(attachments_dir: str, channel: str, file_id: int) -> None:
    """Переписать .dead.jsonl канала без этой записи (если пусто — удалить файл)."""
    path = _dead_path(attachments_dir, channel)
    if not path.is_file():
        return
    keep: list[str] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                rec = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            if rec.get("file_id") == file_id:
                continue
            keep.append(json.dumps(rec, ensure_ascii=False) + "\n")
    if keep:
        with open(path, "w", encoding="utf-8") as fh:
            fh.writelines(keep)
    else:
        path.unlink()


def probe_url(session: requests.Session, url: str) -> str:
    """
    Зонд доступности URL: HEAD (фолбэк GET stream, если сервер запрещает HEAD).

    Возврат: "ok" (статус < 400), "http_404", "http_410", "http_{code}"
    (прочие), "error" (таймаут/сеть).
    """
    headers = {"User-Agent": USER_AGENT}
    try:
        resp = session.head(url, timeout=PROBE_TIMEOUT, headers=headers, allow_redirects=True)
        if resp.status_code in (403, 405):
            with session.get(url, stream=True, timeout=PROBE_TIMEOUT, headers=headers) as stream:
                code = stream.status_code
        else:
            code = resp.status_code
    except requests.exceptions.RequestException:
        return "error"
    if code < 400:
        return "ok"
    return f"http_{code}"


async def check_urls(
    files: list[QueueFile],
    concurrency: int = 8,
    session: requests.Session | None = None,
) -> list[tuple[QueueFile, str]]:
    """
    Проверить доступность URL параллельно (семафор + run_in_executor).

    Файлы обязаны иметь url (кандидаты фильтруются до вызова). Возврат —
    пары (file, результат зонда) в том же порядке, что files.
    """
    sess = session or requests.Session()
    sem = asyncio.Semaphore(max(1, concurrency))
    loop = asyncio.get_running_loop()

    async def _check(f: QueueFile) -> tuple[QueueFile, str]:
        assert f.url is not None
        async with sem:
            return f, await loop.run_in_executor(None, probe_url, sess, f.url)

    return list(await asyncio.gather(*(_check(f) for f in files)))
