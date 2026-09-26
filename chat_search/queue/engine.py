"""
engine.py — Асинхронный движок скачивания очереди вложений (часть 2).

Очередь + N воркеров, Telegram и HTTP параллельно. telegram_media — через
Telethon (по files[].message_id), external_url — HTTP GET. Скачивание в
{target}.part + атомарный os.replace; если size известен и не совпал —
ошибка size_mismatch. Статусы — в {post_dir}/downloaded.jsonl (контракт
DATA.md).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests
from telethon.errors import FileReferenceExpiredError, MessageIdInvalidError

from chat_search.queue.dead import DEAD_ERROR_CODES, mark_dead
from chat_search.queue.models import DOWNLOADED_FILE, USER_AGENT, QueueFile

logger = logging.getLogger(__name__)

HTTP_TIMEOUT = 60


def probe_size(url: str) -> int | None:
    """Content-Length через HEAD (фолбэк GET stream, если сервер запрещает HEAD)."""
    headers = {"User-Agent": USER_AGENT}
    try:
        resp = requests.head(url, timeout=15, headers=headers, allow_redirects=True)
        if resp.status_code in (403, 405):
            with requests.get(url, stream=True, timeout=15, headers=headers) as stream:
                if stream.status_code >= 400:
                    return None
                length = stream.headers.get("Content-Length")
                return int(length) if length else None
        if resp.status_code >= 400:
            return None
        length = resp.headers.get("Content-Length")
        return int(length) if length else None
    except requests.exceptions.RequestException:
        return None


class DownloadError(Exception):
    """Ошибка скачивания с коротким кодом для лога (downloaded.jsonl)."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass
class DownloadStats:
    """Счётчики выполнения (для CLI-вывода)."""

    total: int = 0
    done: int = 0
    errors: int = 0
    bytes_done: int = 0


class DownloadQueueEngine:
    """
    Асинхронный движок скачивания: очередь + N воркеров (TG и HTTP параллельно).

    События (event_callback(event, file, ok, error)):
      "started"  — файл начал скачиваться
      "finished" — файл завершён (ok=True либо error: код, при наличии —
                   с деталями; в downloaded.jsonl пишется только код)

    stop() — не брать новые файлы, активные завершить; run() затем выходит.
    """

    def __init__(
        self,
        client: Any,
        concurrency: int = 4,
        event_callback: Callable[[str, QueueFile, bool, str | None], None] | None = None,
        http_session: requests.Session | None = None,
        attachments_dir: str | None = None,
    ) -> None:
        self._client = client
        self._concurrency = max(1, concurrency)
        self._event_callback = event_callback
        self._session = http_session or requests.Session()
        self._attachments_dir = attachments_dir
        self._queue: deque[QueueFile] = deque()
        self._cond = asyncio.Condition()
        self._gate = asyncio.Event()
        self._gate.set()
        self._stopped = False
        # QueueFile не хэшируется (dataclass с __eq__) — держим id()
        self._active: set[int] = set()
        self._tg_sem = asyncio.Semaphore(self._concurrency)
        self._http_sem = asyncio.Semaphore(self._concurrency)
        self._status_lock = threading.Lock()
        self._entities: dict[str, Any] = {}
        self._tg_ready = False
        self._tg_broken = False
        self._tg_broken_code = "telegram_not_authorized"
        self._tg_broken_reason = ""
        self.stats = DownloadStats()

    async def submit(self, files: Iterable[QueueFile]) -> int:
        """Добавить файлы в очередь; вернуть число добавленных."""
        added = 0
        async with self._cond:
            for f in files:
                if f.downloading or f.done or self._stopped:
                    continue
                self._queue.append(f)
                added += 1
            self.stats.total += added
            self._cond.notify_all()
        return added

    def pause(self) -> None:
        """Пауза: активные файлы завершатся, новые не берутся."""
        self._gate.clear()

    def resume(self) -> None:
        self._gate.set()

    def stop(self) -> None:
        """Стоп после завершения активных файлов."""
        self._stopped = True
        self._gate.set()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # нет loop — run() увидит флаг в своём цикле
        loop.create_task(self._wake_all())

    async def _wake_all(self) -> None:
        async with self._cond:
            self._cond.notify_all()

    async def run(self, finish_on_empty: bool) -> None:
        """
        Работать до stop() либо (finish_on_empty=True) до пустой очереди.

        finish_on_empty=False — режим GUI: движок живёт до stop().
        """
        workers = [asyncio.create_task(self._worker(i)) for i in range(self._concurrency)]
        try:
            async with self._cond:
                while not self._stopped:
                    if finish_on_empty and not self._queue and not self._active:
                        break
                    await self._cond.wait()
        finally:
            self._stopped = True
            self._gate.set()
            async with self._cond:
                self._cond.notify_all()
            await asyncio.gather(*workers)

    async def _worker(self, index: int) -> None:
        while True:
            f = await self._next_task()
            if f is None:
                return
            await self._process(f)

    async def _next_task(self) -> QueueFile | None:
        async with self._cond:
            while not self._queue:
                if self._stopped:
                    return None
                await self._cond.wait()
            if self._stopped:
                return None
            f = self._queue.popleft()
            f.downloading = True
            self._active.add(id(f))
        await self._gate.wait()
        self._emit("started", f, True, None)
        return f

    async def _process(self, f: QueueFile) -> None:
        try:
            if f.file_type == "telegram_media":
                async with self._tg_sem:
                    await self._download_tg(f)
            else:
                async with self._http_sem:
                    await self._download_http(f)
            size = self._commit(f)
            self._record_status(f, "success", size, None)
            f.done = True
            self.stats.done += 1
            self.stats.bytes_done += size or 0
            self._emit("finished", f, True, None)
        except DownloadError as e:
            self._cleanup_tmp(f)
            self._record_status(f, "error", None, e.code)
            self._mark_dead_if_fatal(f, e.code)
            f.last_error = e.code
            self.stats.errors += 1
            self._emit("finished", f, False, str(e))
        except Exception as e:  # noqa: BLE001
            code = f"download_error: {e}"
            self._cleanup_tmp(f)
            self._record_status(f, "error", None, code)
            f.last_error = code
            self.stats.errors += 1
            self._emit("finished", f, False, code)
        finally:
            f.downloading = False
            async with self._cond:
                self._active.discard(id(f))
                self._cond.notify_all()

    def _cleanup_tmp(self, f: QueueFile) -> None:
        if f.target_path is None:
            return
        tmp = f"{f.target_path}.part"
        try:
            os.remove(tmp)
        except FileNotFoundError:
            pass

    async def _ensure_tg(self) -> None:
        if self._tg_broken:
            raise DownloadError(self._tg_broken_code, self._tg_broken_reason)
        if self._tg_ready:
            return
        if self._client is None:
            raise DownloadError("telegram_client_not_ready")
        try:
            if not self._client.is_connected():
                await self._client.connect()
            if not await self._client.is_user_authorized():
                self._tg_broken = True
                self._tg_broken_reason = "Сессия не авторизована — запустите check_connection"
                raise DownloadError("telegram_not_authorized", self._tg_broken_reason)
        except DownloadError:
            raise
        except Exception as e:  # noqa: BLE001
            self._tg_broken = True
            self._tg_broken_code = "telegram_client_error"
            self._tg_broken_reason = str(e)
            raise DownloadError("telegram_client_error", str(e)) from e
        self._tg_ready = True

    async def _get_entity(self, channel: str) -> Any:
        if channel not in self._entities:
            self._entities[channel] = await self._client.get_entity(f"@{channel}")
        return self._entities[channel]

    async def _download_tg(self, f: QueueFile) -> None:
        await self._ensure_tg()
        if f.message_id is None:
            raise DownloadError("no_message_id")
        if f.target_path is None:
            raise DownloadError("no_target_path")
        entity = await self._get_entity(f.channel)
        tmp = f"{f.target_path}.part"
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        try:
            msg = await self._client.get_messages(entity, ids=f.message_id)
            if msg is None:
                raise DownloadError("message_not_found")
            if msg.media is None:
                raise DownloadError("no_media")
            await self._client.download_media(msg, file=tmp)
        except FileReferenceExpiredError as e:
            raise DownloadError("file_reference_expired") from e
        except MessageIdInvalidError as e:
            raise DownloadError("message_not_found") from e
        except DownloadError:
            raise
        except Exception as e:  # noqa: BLE001
            raise DownloadError("telegram_download_error", str(e)) from e

    async def _download_http(self, f: QueueFile) -> None:
        url = f.url
        if not url:
            raise DownloadError("no_url")
        if f.target_path is None:
            raise DownloadError("no_target_path")
        tmp = f"{f.target_path}.part"
        loop = asyncio.get_running_loop()

        def _get() -> None:
            os.makedirs(os.path.dirname(tmp), exist_ok=True)
            with self._session.get(
                url, stream=True, timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}
            ) as resp:
                if resp.status_code >= 400:
                    raise DownloadError(f"http_{resp.status_code}")
                with open(tmp, "wb") as out:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        out.write(chunk)

        try:
            await loop.run_in_executor(None, _get)
        except DownloadError:
            raise
        except requests.exceptions.Timeout as e:
            raise DownloadError("timeout") from e
        except requests.exceptions.RequestException as e:
            raise DownloadError("connection_error", str(e)) from e

    def _commit(self, f: QueueFile) -> int | None:
        """Перенести .part в целевой путь, сверяя размер (если известен)."""
        if f.target_path is None:
            raise DownloadError("no_target_path")
        tmp = f"{f.target_path}.part"
        size = os.path.getsize(tmp)
        if f.size is not None and size != f.size:
            os.remove(tmp)
            raise DownloadError("size_mismatch", f"ожидалось {f.size}, получено {size}")
        Path(f.target_path).parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp, f.target_path)
        return size

    def _record_status(self, f: QueueFile, status: str, size: int | None, error: str | None) -> None:
        """Строка статуса в {post_dir}/downloaded.jsonl (контракт DATA.md)."""
        if f.post_dir is None:
            return
        rec: dict[str, Any] = {
            "file_id": f.file_id,
            "type": f.file_type,
            "filename": f.filename,
            "status": status,
            "size": size,
            "downloaded_at": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        }
        if error is not None:
            rec["error"] = error
        line = json.dumps(rec, ensure_ascii=False)
        with self._status_lock:
            post_dir = Path(f.post_dir)
            post_dir.mkdir(parents=True, exist_ok=True)
            with open(post_dir / DOWNLOADED_FILE, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def _mark_dead_if_fatal(self, f: QueueFile, code: str) -> None:
        """Авто-пометка мёртвым при фатальной ошибке (404/410, удалённое сообщение)."""
        if self._attachments_dir is None or code not in DEAD_ERROR_CODES:
            return
        mark_dead(
            self._attachments_dir,
            f.channel,
            f.file_id,
            code,
            url=f.url,
            filename=f.filename,
        )

    def _emit(self, event: str, f: QueueFile, ok: bool, error: str | None) -> None:
        if self._event_callback is None:
            return
        try:
            self._event_callback(event, f, ok, error)
        except Exception:  # noqa: BLE001
            logger.exception("Ошибка обработчика событий движка")
