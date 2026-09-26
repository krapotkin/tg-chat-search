"""
parse_attachments.py — Собрать очередь вложений из Telegram-каналов (часть 1 из 2).

Обходит полную историю каналов через Telethon (список — из файла конфига) и
из каждого сообщения извлекает:
- внешние файловые URL из текста и подписи к медиа
- нативное медиа Telegram (фото, документы)

Альбом (несколько сообщений с общим grouped_id) группируется в одну строку
очереди: post_id — минимальный ID альбома, message_ids — все ID по
возрастанию, у каждого файла message_id (часть 2 скачивает медиа по
сообщению). Альбом дочитывается целиком: лимиты и state — только на
границах групп.

Для каждого поста с хотя бы одним файлом:
- создаёт папку поста {YYYY}/{YYYY-MM}/{YYYY-MM-DD}_{post_id}/ (post.json + files.jsonl)
- добавляет строку в очередь канала {channel}/queue.jsonl

Часть 2 (скачиватель) читает queue.jsonl и скачивает файлы в папки постов,
записывая статусы в {post_dir}/downloaded.jsonl.

Resume: .queue_state.json с max_processed_id (iter_messages с min_id = N + 1).
--full_scan: обход всей истории заново, известные post_id пропускаются
(ренумерации file_id нет).
--date YYYY-MM-DD: только сообщения этого дня (UTC); state не читается
и не пишется, известные post_id пропускаются.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import unquote

import fire
import requests
from dotenv import load_dotenv

from chat_search import tg_client
from chat_search.config import DEFAULT_UAV_CHANNELS_FILE, load_channel_list

logger = logging.getLogger(__name__)

# Расширения файлов, которые попадают в очередь (URL без расширения,
# например Google Drive, не берутся — ограничение).
FILE_EXTENSIONS: tuple[str, ...] = (
    ".pdf", ".zip", ".rar", ".7z", ".xls", ".xlsx", ".doc", ".docx",
    ".ppt", ".pptx", ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp",
    ".mp4", ".avi", ".mov", ".mkv", ".webm", ".mp3", ".wav", ".ogg",
    ".txt", ".csv", ".json", ".xml", ".epub", ".mobi", ".ipynb",
    ".parquet", ".h5", ".hdf5", ".onnx", ".safetensors", ".npz", ".bin",
    ".tar", ".gz", ".md",
)

IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp")
)
VIDEO_EXTENSIONS: frozenset[str] = frozenset(
    (".mp4", ".avi", ".mov", ".mkv", ".webm")
)
AUDIO_EXTENSIONS: frozenset[str] = frozenset((".mp3", ".wav", ".ogg"))

# Расширение из MIME-типа — для нативного медиа без file_name (видео, PDF и т.п.)
MIME_TO_EXT: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/bmp": "bmp",
    "image/webp": "webp",
    "video/mp4": "mp4",
    "video/quicktime": "mov",
    "video/x-matroska": "mkv",
    "video/webm": "webm",
    "video/x-msvideo": "avi",
    "audio/mpeg": "mp3",
    "audio/ogg": "ogg",
    "audio/wav": "wav",
    "application/pdf": "pdf",
    "application/zip": "zip",
    "application/x-rar-rar": "rar",
    "application/gzip": "gz",
    "application/x-7z-compressed": "7z",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.ms-excel": "xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/x-ebu-stl": "stl",
    "text/markdown": "md",
    "text/x-web-markdown": "md",
    "text/plain": "txt",
    "text/csv": "csv",
    "application/json": "json",
    "application/octet-stream": "bin",
}

URL_RE = re.compile(r"https?://[^\s]+")
TRAILING_PUNCT = ".,;:!?)"

QUEUE_FILE = "queue.jsonl"
STATE_FILE = ".queue_state.json"
STATE_SAVE_EVERY = 500


def date_range_utc(date_str: str) -> tuple[datetime, datetime]:
    """
    День YYYY-MM-DD (UTC) → (start, end) для iter_messages (end — exclusive).

    ValueError при неверном формате.
    """
    day = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)
    return day, day + timedelta(days=1)


def file_extension(url: str) -> str | None:
    """
    Расширение файла из URL (нижний регистр, без точки).

    Учитывает только последний сегмент пути, query/fragment отбрасываются.
    None, если расширения нет или оно не из FILE_EXTENSIONS.
    """
    base = url.split("?")[0].split("#")[0]
    name = base.rsplit("/", 1)[-1]
    if "." not in name:
        return None
    ext = "." + name.rsplit(".", 1)[-1].lower()
    return ext[1:] if ext in FILE_EXTENSIONS else None


def clean_url(url: str) -> str:
    """Обрезать хвостовую пунктуацию, попавшую в URL при скачивании с текста."""
    return url.rstrip(TRAILING_PUNCT)


def extract_file_urls(text: str | None) -> list[str]:
    """
    Файловые URL из текста: regex + обрезка пунктуации + фильтр по расширению.

    Порядок — как в тексте, дубли внутри текста убираются.
    """
    if not text:
        return []
    urls: list[str] = []
    seen: set[str] = set()
    for raw in URL_RE.findall(text):
        url = clean_url(raw)
        if not url or not has_file_extension(url):
            continue
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def has_file_extension(url: str) -> bool:
    return file_extension(url) is not None


def url_filename(url: str) -> str | None:
    """Лучшее предположение имени файла из URL (basename, URL-decode). None, если пусто."""
    base = url.split("?")[0].split("#")[0]
    name = base.rsplit("/", 1)[-1]
    if not name:
        return None
    return unquote(name)


def classify_kind(ext: str | None, mime: str | None = None) -> str:
    """Тип файла: photo | video | audio | document | other (ext важнее mime)."""
    if ext:
        if ("." + ext.lower()) in IMAGE_EXTENSIONS:
            return "photo"
        if ("." + ext.lower()) in VIDEO_EXTENSIONS:
            return "video"
        if ("." + ext.lower()) in AUDIO_EXTENSIONS:
            return "audio"
        return "document"
    if mime:
        if mime.startswith("image/"):
            return "photo"
        if mime.startswith("video/"):
            return "video"
        if mime.startswith("audio/"):
            return "audio"
        return "document"
    return "other"


def url_to_entry(url: str) -> dict[str, Any]:
    """Запись очереди для внешнего URL (file_id присваивается при добавлении)."""
    ext = file_extension(url)
    return {
        "type": "external_url",
        "url": url,
        "filename": url_filename(url),
        "ext": ext,
        "size": None,
        "kind": classify_kind(ext),
    }


def _document_file_name(document: Any) -> str | None:
    """
    Имя файла документа: из DocumentAttributeFilename.

    У TL-объекта Document самого атрибута file_name нет (и в Telethon 1.44
    нет и media.file_name) — имя лежит в attributes. None, если не указано.
    """
    for attr in getattr(document, "attributes", None) or []:
        name = getattr(attr, "file_name", None)
        if isinstance(name, str) and name:
            return name
    return None


def mime_to_ext(mime: str | None) -> str | None:
    """Расширение из MIME-типа (без точки). None, если тип неизвестен."""
    if not mime:
        return None
    return MIME_TO_EXT.get(mime.lower())


def media_to_entry(media: Any, post_id: int) -> dict[str, Any] | None:
    """
    Запись очереди для нативного медиа Telegram (duck-typing: photo/document).

    Photo → kind=photo, ext=jpg, size = max по sizes (None, если размеров нет).
    Document → file_name/mime_type/size из объекта, kind из ext/mime.
    Медиа без file_name (видео, PDF без имени) → имя генерируется:
    file_{post_id}.{ext из mime}.
    Прочие типы медиа (WebPage, Geo, VideoNote и т.п.) → None.
    """
    photo = getattr(media, "photo", None)
    if photo is not None:
        sizes = getattr(photo, "sizes", None) or []
        size = None
        if sizes:
            size = max((getattr(s, "size", 0) or 0 for s in sizes), default=0) or None
        return {
            "type": "telegram_media",
            "url": None,
            "filename": f"photo_{post_id}.jpg",
            "ext": "jpg",
            "mime_type": "image/jpeg",
            "size": size,
            "kind": "photo",
        }

    document = getattr(media, "document", None)
    if document is not None:
        file_name = _document_file_name(document)
        mime = getattr(document, "mime_type", None)
        size = getattr(document, "size", None)
        ext = None
        if file_name and "." in file_name:
            ext = file_name.rsplit(".", 1)[-1].lower()
        if file_name is None:
            # Telegram отдаёт видео/PDF без имени: генерируем из mime
            ext = ext or mime_to_ext(mime)
            file_name = f"file_{post_id}.{ext}" if ext else f"file_{post_id}"
        return {
            "type": "telegram_media",
            "url": None,
            "filename": file_name,
            "ext": ext,
            "mime_type": mime,
            "size": size,
            "kind": classify_kind(ext, mime),
        }

    return None


def extract_message_urls(msg: Any) -> list[str]:
    """Файловые URL из сообщения (текст + подпись к медиа), dedup внутри поста."""
    media = getattr(msg, "media", None)
    caption = getattr(media, "caption", None) if media is not None else None
    urls: list[str] = []
    seen: set[str] = set()
    for text in (getattr(msg, "text", None), caption):
        for url in extract_file_urls(text):
            if url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


def _peer_id(peer: Any) -> int | None:
    """Числовой ID из TL-объекта peer (PeerChannel/PeerChat/PeerUser). None, если нет."""
    for attr in ("channel_id", "chat_id", "user_id"):
        value = getattr(peer, attr, None)
        if value is not None:
            return int(value)
    return None


def _to_timestamp(dt: datetime | None) -> int | None:
    """Datetime в unix timestamp. Naive считается UTC (Telethon 1.44+ отдаёт aware UTC)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp())


def _media_summary(media: Any) -> dict[str, Any] | None:
    """Сводка медиа для post.json. None, если медиа нет."""
    if media is None:
        return None
    photo = getattr(media, "photo", None)
    if photo is not None:
        return {"type": "photo"}
    document = getattr(media, "document", None)
    if document is not None:
        return {
            "type": "document",
            "file_name": _document_file_name(document),
            "mime_type": getattr(document, "mime_type", None),
            "size": getattr(document, "size", None),
        }
    return {"type": type(media).__name__}


def build_post_record(msg: Any, channel: str) -> dict[str, Any]:
    """Исходник сообщения для post.json (что извлёк парсер)."""
    media = getattr(msg, "media", None)
    forwarded: dict[str, Any] | None = None
    fwd = getattr(msg, "forward", None)
    if fwd is not None:
        forwarded = {
            "from_id": _peer_id(getattr(fwd, "from_id", None)),
            "name": getattr(fwd, "from_name", None) or getattr(fwd, "name", None),
        }

    return {
        "post_id": msg.id,
        "channel": channel,
        "url": f"https://t.me/{channel}/{msg.id}",
        "pub_time": _to_timestamp(getattr(msg, "date", None)),
        "text": getattr(msg, "text", None),
        "caption": getattr(media, "caption", None) if media is not None else None,
        "media": _media_summary(media),
        "reply_to_message_id": getattr(msg, "reply_to_msg_id", None),
        "forwarded_from": forwarded,
        "parse_time": int(datetime.now(UTC).timestamp()),
    }


def _album_representative(members: list[Any]) -> Any:
    """
    Сообщение альбома для post.json: первое (по возрастанию ID) с текстом или
    подписью, иначе — с минимальным ID.
    """
    for msg in members:
        if getattr(msg, "text", None) or getattr(getattr(msg, "media", None), "caption", None):
            return msg
    return members[0]


def build_album_post_record(members: list[Any], channel: str) -> dict[str, Any]:
    """
    post.json для альбома: исходник сообщения с текстом/подписью;
    post_id/url/pub_time — от минимального ID; media — список сводок (по
    возрастанию ID); message_ids — все ID сообщений альбома.
    """
    members = sorted(members, key=lambda m: m.id)
    representative = _album_representative(members)
    first = members[0]
    record = build_post_record(representative, channel)
    record["post_id"] = first.id
    record["url"] = f"https://t.me/{channel}/{first.id}"
    record["pub_time"] = _to_timestamp(getattr(first, "date", None))
    record["media"] = [
        summary
        for summary in (_media_summary(getattr(msg, "media", None)) for msg in members)
        if summary is not None
    ]
    record["message_ids"] = [msg.id for msg in members]
    return record


def max_channel_file_id(queue: list[dict[str, Any]]) -> int:
    """Максимальный file_id по всем строкам очереди (0, если очередь пуста)."""
    return max(
        (f["file_id"] for rec in queue for f in rec.get("files", [])),
        default=0,
    )


def merge_queue(
    existing: list[dict[str, Any]],
    new: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Объединить очереди по post_id (setdefault — существующая строка не
    перезаписывается) и отсортировать по post_id.
    """
    by_id: dict[int, dict[str, Any]] = {}
    for rec in existing:
        by_id[rec["post_id"]] = rec
    for rec in new:
        by_id.setdefault(rec["post_id"], rec)
    return [by_id[post_id] for post_id in sorted(by_id)]


class AttachmentQueueParser:
    """Сборка очереди вложений из каналов (парсер, часть 1 из 2)."""

    def __init__(
        self,
        channels: list[str],
        attachments_dir: str,
        api_id: int,
        api_hash: str,
        max_messages: int | None = None,
        full_scan: bool = False,
        fetch_sizes: bool = False,
        min_date: datetime | None = None,
        max_date: datetime | None = None,
    ) -> None:
        self.channels = channels
        self.attachments_dir = attachments_dir
        self.max_messages = max_messages
        self.full_scan = full_scan
        self.fetch_sizes = fetch_sizes
        self.min_date = min_date
        self.max_date = max_date

        self.client = tg_client.make_client(api_id, api_hash)

        self.http_session = requests.Session()
        self.http_session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })

        os.makedirs(attachments_dir, exist_ok=True)

    async def run(self) -> None:
        await self.client.start()
        logger.info("Авторизация успешна")

        for channel in self.channels:
            try:
                await self.parse_channel(channel)
            except Exception as e:
                logger.error("Канал %s: ошибка — %s", channel, e)

        await self.client.disconnect()

    async def parse_channel(self, channel_name: str) -> None:
        logger.info("=" * 60)
        logger.info("Обработка канала: %s", channel_name)

        channel_dir = os.path.join(self.attachments_dir, channel_name)
        os.makedirs(channel_dir, exist_ok=True)

        queue = self._load_queue(channel_dir)
        queued_post_ids = {rec["post_id"] for rec in queue}
        next_file_id = max_channel_file_id(queue) + 1

        # Дата-фильтр не гарантирует "всё <= N обработано", поэтому state
        # не читается и не пишется — resume-прогон после него работает как обычно.
        date_mode = self.min_date is not None or self.max_date is not None
        state = 0 if (self.full_scan or date_mode) else self._load_state(channel_dir)

        try:
            entity = await self.client.get_entity(channel_name)
        except Exception as e:
            logger.error("Не удалось получить канал %s: %s", channel_name, e)
            return

        if date_mode:
            logger.info(
                "Фильтр по дате: %s .. %s (state не изменяется)", self.min_date, self.max_date
            )
            # iter_messages не знает min_date/max_date: offset_date (exclusive)
            # задаёт точку старта, нижнюю границу режем в цикле (обход от свежих к старым).
            if self.max_date is not None:
                iterator = self.client.iter_messages(
                    entity, offset_date=self.max_date, wait_time=0.5
                )
            else:
                iterator = self.client.iter_messages(entity, wait_time=0.5)
        elif state:
            logger.info("Resume: поиск сообщений с ID > %d", state)
            iterator = self.client.iter_messages(entity, min_id=state + 1, wait_time=0.5)
        else:
            logger.info("Полный обход истории канала")
            iterator = self.client.iter_messages(entity, wait_time=0.5)

        processed = 0
        new_posts = 0
        new_files = 0
        max_processed_id = state
        new_records: list[dict[str, Any]] = []
        pending_sizes: list[dict[str, Any]] = []
        pending_group: list[Any] = []
        current_group_id: int | None = None
        next_state_save_at = STATE_SAVE_EVERY

        def flush_group() -> None:
            """Обработать законченную группу (одно сообщение или дочитанный альбом)."""
            nonlocal pending_group, current_group_id, next_file_id, new_posts, new_files
            if not pending_group:
                return
            next_file_id, added_posts, added_files = self._process_group(
                channel_name,
                channel_dir,
                queued_post_ids,
                pending_group,
                next_file_id,
                new_records,
                pending_sizes,
            )
            new_posts += added_posts
            new_files += added_files
            pending_group = []
            current_group_id = None

        async for msg in iterator:
            # Обход от свежих к старым: вышли за начало окна — дальше нечего
            # в этом окне, останавливаемся (без date-фильтра min_date None).
            if self.min_date is not None and msg.date < self.min_date:
                break

            grouped_id = getattr(msg, "grouped_id", None)
            continues_group = (
                grouped_id is not None and bool(pending_group) and grouped_id == current_group_id
            )

            if not continues_group:
                # Лимит проверяется на границе групп: альбом дочитывается целиком.
                if self.max_messages is not None and processed >= self.max_messages:
                    logger.info("Достигнут лимит (%d), остановка", self.max_messages)
                    break
                # Предыдущая группа дочитана — обрабатываем её. State пишем только
                # на границах групп: после сбоя посреди альбома на его сообщения
                # ничего не записано, при resume альбом пересоберётся целиком.
                flush_group()
                if not date_mode and processed >= next_state_save_at:
                    self._save_state(channel_dir, max_processed_id)
                    next_state_save_at += STATE_SAVE_EVERY

            processed += 1
            max_processed_id = max(max_processed_id, msg.id)
            pending_group.append(msg)
            current_group_id = grouped_id

        flush_group()

        if self.fetch_sizes and pending_sizes:
            # Размеры впишутся в те же dict, что уже в new_records — очередь
            # перепишется с актуальными значениями.
            self._fill_external_sizes(pending_sizes)

        self._save_queue(channel_dir, queue, new_records)
        if not date_mode:
            self._save_state(channel_dir, max_processed_id)

        logger.info(
            "Канал %s: обработано %d сообщений, добавлено %d постов (%d файлов)",
            channel_name,
            processed,
            new_posts,
            new_files,
        )

    def _process_group(
        self,
        channel_name: str,
        channel_dir: str,
        queued_post_ids: set[int],
        group: list[Any],
        next_file_id: int,
        new_records: list[dict[str, Any]],
        pending_sizes: list[dict[str, Any]],
    ) -> tuple[int, int, int]:
        """Обработать группу (одно сообщение или альбом).

        Возвращает (новое next_file_id, добавлено постов, добавлено файлов).
        """
        if len(group) == 1:
            return self._process_single(
                channel_name, channel_dir, queued_post_ids, group[0],
                next_file_id, new_records, pending_sizes,
            )
        return self._process_album(
            channel_name, channel_dir, queued_post_ids, group,
            next_file_id, new_records, pending_sizes,
        )

    def _process_single(
        self,
        channel_name: str,
        channel_dir: str,
        queued_post_ids: set[int],
        msg: Any,
        next_file_id: int,
        new_records: list[dict[str, Any]],
        pending_sizes: list[dict[str, Any]],
    ) -> tuple[int, int, int]:
        if msg.id in queued_post_ids:
            # Пост уже в очереди (например, при full_scan): не пересобираем
            # папку и не перезаписываем строку, state продвигает цикл.
            return next_file_id, 0, 0

        files = self._extract_files(msg)
        if not files:
            return next_file_id, 0, 0

        # file_id — порядковый номер в пределах канала (max + 1, без ренумерации)
        for i, f in enumerate(files):
            f["file_id"] = next_file_id + i
        next_file_id += len(files)

        record: dict[str, Any] = {
            "post_id": msg.id,
            "channel": channel_name,
            "url": f"https://t.me/{channel_name}/{msg.id}",
            "pub_time": _to_timestamp(getattr(msg, "date", None)),
            "message_ids": [msg.id],
            "files": files,
        }

        self._write_post_folder(
            channel_name, msg.date, msg.id, build_post_record(msg, channel_name), files
        )
        self._append_queue(channel_dir, [record])
        new_records.append(record)
        queued_post_ids.add(msg.id)
        pending_sizes.extend(
            f for f in files if f["type"] == "external_url" and f["size"] is None
        )
        return next_file_id, 1, len(files)

    def _process_album(
        self,
        channel_name: str,
        channel_dir: str,
        queued_post_ids: set[int],
        group: list[Any],
        next_file_id: int,
        new_records: list[dict[str, Any]],
        pending_sizes: list[dict[str, Any]],
    ) -> tuple[int, int, int]:
        members = sorted(group, key=lambda m: m.id)
        member_ids = {m.id for m in members}
        if member_ids & queued_post_ids:
            # Хоть один участник уже в очереди (старый формат): альбом не
            # пересобираем, участников отмечаем обработанными.
            queued_post_ids.update(member_ids)
            return next_file_id, 0, 0

        files = self._album_files(members)
        if not files:
            queued_post_ids.update(member_ids)
            return next_file_id, 0, 0

        for i, f in enumerate(files):
            f["file_id"] = next_file_id + i
        next_file_id += len(files)

        first = members[0]
        record: dict[str, Any] = {
            "post_id": first.id,
            "channel": channel_name,
            "url": f"https://t.me/{channel_name}/{first.id}",
            "pub_time": _to_timestamp(getattr(first, "date", None)),
            "message_ids": [m.id for m in members],
            "files": files,
        }

        self._write_post_folder(
            channel_name, first.date, first.id, build_album_post_record(members, channel_name), files
        )
        self._append_queue(channel_dir, [record])
        new_records.append(record)
        queued_post_ids.update(member_ids)
        pending_sizes.extend(
            f for f in files if f["type"] == "external_url" and f["size"] is None
        )
        return next_file_id, 1, len(files)

    def _extract_files(self, msg: Any) -> list[dict[str, Any]]:
        """
        Файлы из сообщения: сначала нативное медиа, затем внешние URL (без file_id).

        У каждой записи message_id — часть 2 скачивает медиа по сообщению.
        """
        files: list[dict[str, Any]] = []

        media = getattr(msg, "media", None)
        if media is not None:
            entry = media_to_entry(media, msg.id)
            if entry is not None:
                entry["message_id"] = msg.id
                files.append(entry)
            else:
                logger.debug(
                    "Пост %d: медиа типа %s не в очереди", msg.id, type(media).__name__
                )

        for url in extract_message_urls(msg):
            entry = url_to_entry(url)
            entry["message_id"] = msg.id
            files.append(entry)

        return files

    def _album_files(self, members: list[Any]) -> list[dict[str, Any]]:
        """Файлы всех сообщений альбома (по возрастанию ID); URL дедуплицируются на альбом."""
        files: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for msg in members:
            for entry in self._extract_files(msg):
                url = entry.get("url")
                if url is not None:
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                files.append(entry)
        return files

    def _post_dir(self, channel_name: str, post_date: datetime, post_id: int) -> str:
        """Папка поста: {channel}/{YYYY}/{YYYY-MM}/{YYYY-MM-DD}_{post_id} (даты из pub_time, UTC)."""
        year, month, day = (
            post_date.strftime("%Y"),
            post_date.strftime("%m"),
            post_date.strftime("%Y-%m-%d"),
        )
        return os.path.join(
            self.attachments_dir, channel_name, year, f"{year}-{month}", f"{day}_{post_id}"
        )

    def _write_post_folder(
        self,
        channel_name: str,
        post_date: datetime,
        post_id: int,
        post_record: dict[str, Any],
        files: list[dict[str, Any]],
    ) -> None:
        """Папка поста: post.json (исходник) + files.jsonl (список файлов)."""
        post_dir = self._post_dir(channel_name, post_date, post_id)
        os.makedirs(post_dir, exist_ok=True)

        post_file = os.path.join(post_dir, "post.json")
        if not os.path.exists(post_file):
            # Сериализуем в память до записи: при ошибке не оставляем битый файл
            # (существующий post.json при повторном прогоне не перезаписывается).
            post_data = json.dumps(post_record, ensure_ascii=False, indent=2)
            with open(post_file, "w", encoding="utf-8") as f:
                f.write(post_data)

        files_file = os.path.join(post_dir, "files.jsonl")
        if not os.path.exists(files_file):
            with open(files_file, "w", encoding="utf-8") as f:
                for file_rec in files:
                    f.write(json.dumps(file_rec, ensure_ascii=False) + "\n")

    def _load_queue(self, channel_dir: str) -> list[dict[str, Any]]:
        """Загрузить очередь канала (все строки, в любом порядке)."""
        queue_file = os.path.join(channel_dir, QUEUE_FILE)
        records: list[dict[str, Any]] = []
        if os.path.exists(queue_file):
            with open(queue_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        logger.warning("Пропускаю битую строку в %s", queue_file)
        return records

    def _append_queue(self, channel_dir: str, records: list[dict[str, Any]]) -> None:
        """Дописать строки в очередь (инкрементально — без потери при сбое)."""
        if not records:
            return
        queue_file = os.path.join(channel_dir, QUEUE_FILE)
        with open(queue_file, "a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _save_queue(
        self,
        channel_dir: str,
        existing: list[dict[str, Any]],
        new: list[dict[str, Any]],
    ) -> None:
        """
        Финальная запись очереди: мерж существующих и новых записей по post_id
        (существующее не перезаписывается), сортировка по post_id.
        """
        queue_file = os.path.join(channel_dir, QUEUE_FILE)
        with open(queue_file, "w", encoding="utf-8") as f:
            for rec in merge_queue(existing, new):
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _load_state(self, channel_dir: str) -> int:
        """max_processed_id из state-файла (0, если файла нет)."""
        state_file = os.path.join(channel_dir, STATE_FILE)
        if os.path.exists(state_file):
            try:
                with open(state_file, encoding="utf-8") as f:
                    return int(json.load(f).get("max_processed_id", 0))
            except (json.JSONDecodeError, OSError, ValueError):
                logger.warning("Не удалось прочитать state-файл %s", state_file)
        return 0

    def _save_state(self, channel_dir: str, max_processed_id: int) -> None:
        state_file = os.path.join(channel_dir, STATE_FILE)
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump({"max_processed_id": max_processed_id}, f)

    def _fill_external_sizes(self, entries: list[dict[str, Any]]) -> None:
        """Размеры внешних URL (только новые записи): HEAD, при 403/405 — GET stream."""
        if not entries:
            return
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {
                pool.submit(self._probe_url_size, entry["url"]): entry for entry in entries
            }
            for future, entry in futures.items():
                try:
                    entry["size"] = future.result()
                except Exception:
                    entry["size"] = None

    def _probe_url_size(self, url: str) -> int | None:
        """Размер файла по URL (Content-Length). None, если не удалось."""
        try:
            r = self.http_session.head(url, timeout=15, allow_redirects=True)
            if r.status_code in (403, 405):
                return self._probe_get_size(url)
            if r.status_code >= 400:
                return None
            length = r.headers.get("Content-Length")
            return int(length) if length else None
        except Exception:
            return self._probe_get_size(url)

    def _probe_get_size(self, url: str) -> int | None:
        """Размер через GET stream (для серверов без HEAD): только Content-Length."""
        try:
            with self.http_session.get(url, stream=True, timeout=15) as r:
                if r.status_code >= 400:
                    return None
                length = r.headers.get("Content-Length")
                return int(length) if length else None
        except Exception:
            return None


def parse_attachments(
    attachments_dir: str,
    channels_file: str = DEFAULT_UAV_CHANNELS_FILE,
    max_messages: int | None = None,
    full_scan: bool = False,
    fetch_sizes: bool = False,
    date: str | None = None,
) -> None:
    """
    Собрать очередь вложений из Telegram-каналов (парсер, часть 1 из 2).

    Args:
        attachments_dir: Корень данных (tg_channels_attachments)
        channels_file: YAML-конфиг со списком каналов
            (по умолчанию configs/uav_channels.yaml)
        max_messages: Лимит сообщений на канал за запуск (None = все)
        full_scan: Игнорировать state (полный обход истории; известные post_id
            пропускаются, ренумерации file_id нет)
        fetch_sizes: Заполнять размеры внешних URL (HEAD/GET, по умолчанию выкл)
        date: День YYYY-MM-DD (UTC) — только сообщения этого дня; state не
            читается и не пишется (приоритет над resume/full_scan)
    """
    channels = load_channel_list(channels_file)
    api_id, api_hash = tg_client.get_credentials()

    min_date: datetime | None = None
    max_date: datetime | None = None
    if date is not None:
        min_date, max_date = date_range_utc(date)

    parser = AttachmentQueueParser(
        channels=channels,
        attachments_dir=attachments_dir,
        api_id=api_id,
        api_hash=api_hash,
        max_messages=max_messages,
        full_scan=full_scan,
        fetch_sizes=fetch_sizes,
        min_date=min_date,
        max_date=max_date,
    )
    asyncio.run(parser.run())


def main(
    attachments_dir: str,
    channels_file: str = DEFAULT_UAV_CHANNELS_FILE,
    max_messages: int | None = None,
    full_scan: bool = False,
    fetch_sizes: bool = False,
    date: str | None = None,
) -> None:
    """Entry point — загружает .env и запускает parse_attachments."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )
    load_dotenv()
    parse_attachments(
        attachments_dir,
        channels_file=channels_file,
        max_messages=max_messages,
        full_scan=full_scan,
        fetch_sizes=fetch_sizes,
        date=date,
    )


if __name__ == "__main__":
    fire.Fire(main)
