"""
queue.py — Загрузка очереди, скан диска, приоритеты и фильтры (часть 2).

Очередь: {channel}/queue.jsonl (собирает parse_attachments.py). Папки постов:
{channel}/{YYYY}/{YYYY-MM}/{YYYY-MM-DD}_{post_id}. Статусы:
{post_dir}/downloaded.jsonl (контракт — в DATA.md).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from chat_search.queue.models import DOWNLOADED_FILE, KIND_ORDER, QUEUE_FILE, DiskIndex, QueueFile

logger = logging.getLogger(__name__)

# Версионный суффикс в конце имени: X.Y или X.Y.Z (опц. v),
# слева от цифр — не цифра.
_VERSION_RE = re.compile(r"(?<!\d)(v?\d+(?:\.\d+)+)$")


def version_family(filename: str) -> str | None:
    """
    Семейство версий: stem (имя без расширения) без версионного суффикса.

    None, если имя не заканчивается версией или остаток пуст
    (`2.12.4.bin` — не член семейства). Разделитель перед версией
    (один из _ . - пробел) срезается вместе с ней:
    `app_2.12.4.zip` -> `app`, `app_v1.2.dmg` -> `app`, `app2.12.4.bin` -> `app`.
    """
    name = filename.lower()
    stem = name.rsplit(".", 1)[0] if "." in name else name
    match = _VERSION_RE.search(stem)
    if match is None:
        return None
    base = stem[: match.start()]
    if base.endswith(("_", ".", "-", " ")):
        base = base[:-1]
    return base or None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Строки JSONL в список dict (битые строки пропускаются)."""
    records: list[dict[str, Any]] = []
    if not path.is_file():
        return records
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
            if isinstance(rec, dict):
                records.append(rec)
    return records


def discover_channels(attachments_dir: str) -> list[str]:
    """Каналы в корне данных: подкаталоги с queue.jsonl (по алфавиту)."""
    root = Path(attachments_dir)
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and (p / QUEUE_FILE).is_file())


def load_queue(attachments_dir: str, channels: list[str] | None = None) -> list[QueueFile]:
    """Прочитать queue.jsonl всех (или указанных) каналов в список файлов."""
    names = discover_channels(attachments_dir)
    if channels is not None:
        wanted = set(channels)
        names = [n for n in names if n in wanted]
    files: list[QueueFile] = []
    root = Path(attachments_dir)
    for name in names:
        for rec in _read_jsonl(root / name / QUEUE_FILE):
            post_id = rec.get("post_id")
            if not isinstance(post_id, int):
                logger.warning("%s/%s: пропускаю запись без post_id", name, QUEUE_FILE)
                continue
            pub_time = rec.get("pub_time")
            post_url = rec.get("url") or ""
            for f in rec.get("files", []):
                file_id = f.get("file_id")
                if not isinstance(file_id, int):
                    continue
                files.append(
                    QueueFile(
                        channel=name,
                        post_id=post_id,
                        post_url=post_url,
                        pub_time=pub_time if isinstance(pub_time, int) else None,
                        file_id=file_id,
                        file_type=f.get("type", "external_url"),
                        url=f.get("url"),
                        filename=f.get("filename") or f"file_{file_id}",
                        ext=f.get("ext"),
                        mime_type=f.get("mime_type"),
                        size=f.get("size") if isinstance(f.get("size"), int) else None,
                        kind=f.get("kind") or "other",
                        message_id=f.get("message_id") if isinstance(f.get("message_id"), int) else None,
                    )
                )
    return files


def post_dir_for(attachments_dir: str, channel: str, pub_time: int | None, post_id: int) -> str:
    """Папка поста {channel}/{YYYY}/{YYYY-MM}/{YYYY-MM-DD}_{post_id} (UTC)."""
    dt = datetime.fromtimestamp(pub_time, tz=UTC) if pub_time else datetime(1970, 1, 1, tzinfo=UTC)
    return str(
        Path(attachments_dir)
        / channel
        / f"{dt.year:04d}"
        / f"{dt.year:04d}-{dt.month:02d}"
        / f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}_{post_id}"
    )


def _load_downloaded(path: Path, channel: str, last_status: dict[tuple[str, int], str]) -> None:
    """downloaded.jsonl: последний статус по (channel, file_id) побеждает."""
    for rec in _read_jsonl(path):
        file_id = rec.get("file_id")
        status = rec.get("status")
        if isinstance(file_id, int) and isinstance(status, str):
            last_status[(channel, file_id)] = status


def _iter_post_dirs(attachments_dir: str) -> Iterable[tuple[str, Path]]:
    """Все папки постов {channel}/{YYYY}/{YYYY-MM}/{...} как (channel, dir)."""
    root = Path(attachments_dir)
    if not root.is_dir():
        return
    for channel_dir in sorted(root.iterdir()):
        if not channel_dir.is_dir():
            continue
        for year_dir in sorted(channel_dir.iterdir()):
            if not year_dir.is_dir():
                continue
            for month_dir in sorted(year_dir.iterdir()):
                if not month_dir.is_dir():
                    continue
                for post_dir in sorted(month_dir.iterdir()):
                    if post_dir.is_dir():
                        yield channel_dir.name, post_dir


def scan_disk(attachments_dir: str) -> DiskIndex:
    """Обход всех папок постов: размеры файлов + статусы из downloaded.jsonl."""
    path_sizes: dict[str, int] = {}
    by_name_size: dict[tuple[str, int], list[str]] = {}
    by_name: dict[str, list[str]] = {}
    last_status: dict[tuple[str, int], str] = {}
    skip = {QUEUE_FILE, DOWNLOADED_FILE, "post.json", "files.jsonl"}
    for channel, post_dir in _iter_post_dirs(attachments_dir):
        _load_downloaded(post_dir / DOWNLOADED_FILE, channel, last_status)
        for f in post_dir.iterdir():
            if not f.is_file() or f.name in skip:
                continue
            try:
                size = f.stat().st_size
            except OSError:
                continue
            path_sizes[str(f)] = size
            by_name_size.setdefault((f.name.lower(), size), []).append(str(f))
            by_name.setdefault(f.name.lower(), []).append(str(f))
    return DiskIndex(path_sizes, by_name_size, by_name, last_status)


def _unique_name(name: str, used: set[str]) -> str:
    """Уникальное имя в папке поста (суффикс _1, _2 перед расширением)."""
    if name.lower() not in used:
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    for i in range(1, 10_000):
        candidate = f"{stem}_{i}.{ext}" if dot else f"{stem}_{i}"
        if candidate.lower() not in used:
            return candidate
    msg = f"Не удалось подобрать уникальное имя для {name}"
    raise ValueError(msg)


def _assign_target_paths(files: list[QueueFile]) -> None:
    """Целевой путь каждого файла: {post_dir}/{filename}, коллизии — суффиксы."""
    used: dict[str, set[str]] = {}
    ordered = sorted(files, key=lambda f: (f.channel, f.post_id, f.file_id))
    for f in ordered:
        if f.post_dir is None:
            continue
        names = used.setdefault(f.post_dir, set())
        name = _unique_name(f.filename, names)
        names.add(name.lower())
        f.target_path = str(Path(f.post_dir) / name)


def _freshness(f: QueueFile) -> tuple[int, int, int]:
    """Свежесть: pub_time, затем post_id, затем file_id (для детерминизма)."""
    return (f.pub_time or 0, f.post_id, f.file_id)


def _compute_priorities(files: list[QueueFile]) -> None:
    """
    Группы дублей (точное имя+размер) и семейства версий — тиры.

    T1 — уникальные; T2 — самый свежий в каждой группе/семействе; T3 — остальные.
    Файл может входить в несколько групп — берётся худший (максимальный) тир.
    """
    exact: dict[tuple[str, int], list[QueueFile]] = {}
    for f in files:
        if f.size is None:
            continue
        exact.setdefault((f.filename.lower(), f.size), []).append(f)
    families: dict[str, list[QueueFile]] = {}
    for f in files:
        if f.family is not None:
            families.setdefault(f.family, []).append(f)

    for group in exact.values():
        if len(group) < 2:
            continue
        newest = max(group, key=_freshness)
        for f in group:
            f.group_size = len(group)
            f.tier = max(f.tier, 2 if f is newest else 3)
    for fam in families.values():
        if len(fam) < 2:
            continue
        newest = max(fam, key=_freshness)
        for f in fam:
            f.family_size = len(fam)
            f.tier = max(f.tier, 2 if f is newest else 3)


def _apply_disk_state(files: list[QueueFile], disk: DiskIndex) -> None:
    """Отметить скачанные (done) и уже лежащие на диске (on_disk → T3)."""
    for f in files:
        if disk.is_done(f.channel, f.file_id, f.target_path, f.size):
            f.done = True
            continue
        path = disk.on_disk_path(f.filename, f.size)
        if path is not None and path != f.target_path:
            f.on_disk = True
            f.tier = max(f.tier, 3)


def prepare_queue(
    attachments_dir: str,
    files: list[QueueFile],
    disk: DiskIndex,
    dead: dict[tuple[str, int], dict[str, Any]] | None = None,
) -> None:
    """Посчитать post_dir, target_path, family, тиры, done/on_disk, dead для файлов."""
    for f in files:
        f.family = version_family(f.filename)
        f.post_dir = post_dir_for(attachments_dir, f.channel, f.pub_time, f.post_id)
        if dead is not None:
            rec = dead.get((f.channel, f.file_id))
            if rec is not None:
                f.dead = True
                reason = rec.get("reason")
                f.dead_reason = str(reason) if reason is not None else None
    _assign_target_paths(files)
    _compute_priorities(files)
    _apply_disk_state(files, disk)


def sort_key(f: QueueFile) -> tuple[int, int, str, int]:
    """Сортировка: тир -> kind -> имя -> дата (свежие раньше)."""
    return (f.tier, KIND_ORDER.get(f.kind, len(KIND_ORDER)), f.filename.lower(), -(f.pub_time or 0))


def select_for_download(
    files: list[QueueFile], force_on_disk: bool = False, include_dead: bool = False
) -> list[QueueFile]:
    """Файлы к скачиванию (не done, не dead), в порядке приоритетов."""
    selected = [
        f
        for f in files
        if not f.done and (force_on_disk or not f.on_disk) and (include_dead or not f.dead)
    ]
    return sorted(selected, key=sort_key)


def parse_csv(value: str | None) -> list[str] | None:
    """CSV-список "a,b" -> ['a', 'b']; None/пусто -> None (фильтр не задан)."""
    if value is None or not value.strip():
        return None
    return [part.strip() for part in value.split(",") if part.strip()]


def filter_files(files: list[QueueFile], kinds: list[str] | None, exts: list[str] | None) -> list[QueueFile]:
    """Фильтр по kind и/или расширению (AND, регистронезависимо)."""
    result = files
    if kinds:
        wanted = {k.strip().lower() for k in kinds}
        result = [f for f in result if f.kind in wanted]
    if exts:
        wanted = {e.strip().lstrip(".").lower() for e in exts}
        result = [f for f in result if (f.ext or "").lstrip(".").lower() in wanted]
    return result
