"""
reporting.py — Сводки, форматирование и отчёты (JSON/CSV/TSV) по очереди вложений.

Используется консольными командами (download_cli, download_queue) и GUI:
сводка по типам/каналам/статусам, строка таблицы в порядке приоритетов,
карточка файла, полный отчёт (JSON — для машин, CSV/TSV — для просмотра).
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from chat_search.queue.models import KIND_ORDER, QueueFile


def format_size(n: int | None) -> str:
    """Байты -> человекочитательно (двоичные КБ/МБ/ГБ)."""
    if n is None:
        return "?"
    value = float(n)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if value < 1024 or unit == "ТБ":
            return f"{value:.0f} {unit}" if unit == "Б" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} ТБ"


def _date_str(pub_time: int | None) -> str:
    if pub_time is None:
        return "—"
    return datetime.fromtimestamp(pub_time, tz=UTC).strftime("%Y-%m-%d")


def status_label(f: QueueFile) -> str:
    """Статус для вывода: downloading / done / dead / error / on_disk / pending."""
    if f.downloading:
        return "downloading"
    if f.done:
        return "done"
    if f.dead:
        return "dead"
    if f.last_error:
        return f"error: {f.last_error}"
    if f.on_disk:
        return "on_disk"
    return "pending"


def _count_duplicates(files: list[QueueFile]) -> int:
    """Файлы, дублирующиеся с другими (в точных группах или семействах)."""
    return sum(1 for f in files if f.group_size > 1 or f.family_size > 1)


@dataclass
class Summary:
    """Сводка для list: по типам / каналам / статусам."""

    total: int
    by_kind: dict[str, int]
    by_channel: dict[str, int]
    by_status: dict[str, int]
    duplicates: int
    total_size: int


def build_summary(files: list[QueueFile]) -> Summary:
    by_kind: dict[str, int] = {}
    by_channel: dict[str, int] = {}
    by_status: dict[str, int] = {}
    total_size = 0
    for f in files:
        by_kind[f.kind] = by_kind.get(f.kind, 0) + 1
        by_channel[f.channel] = by_channel.get(f.channel, 0) + 1
        label = status_label(f)
        by_status[label] = by_status.get(label, 0) + 1
        if f.size is not None:
            total_size += f.size
    return Summary(
        total=len(files),
        by_kind=dict(sorted(by_kind.items(), key=lambda kv: KIND_ORDER.get(kv[0], 99))),
        by_channel=dict(sorted(by_channel.items())),
        by_status=dict(sorted(by_status.items())),
        duplicates=_count_duplicates(files),
        total_size=total_size,
    )


def format_summary(s: Summary) -> str:
    lines = [
        f"Всего: {s.total} файлов, {format_size(s.total_size)}; дубликаты/версии: {s.duplicates}",
        "По типам:",
    ]
    for kind, count in s.by_kind.items():
        lines.append(f"  {kind:<10} {count:>6}")
    lines.append("По каналам:")
    for channel, count in s.by_channel.items():
        lines.append(f"  {channel:<20} {count:>6}")
    lines.append("По статусам:")
    for status, count in s.by_status.items():
        lines.append(f"  {status:<20} {count:>6}")
    return "\n".join(lines)


def format_row(f: QueueFile) -> str:
    """Строка таблицы в порядке приоритетов (list)."""
    ext = f.ext or "-"
    return (
        f"T{f.tier} {f.kind:<9} {ext:<6} {format_size(f.size):>9}  "
        f"{_date_str(f.pub_time)}  {f.channel}/{f.post_id}  {f.filename}  [{status_label(f)}]"
    )


def file_to_dict(f: QueueFile) -> dict[str, Any]:
    """Файл как dict для JSON-отчёта (все поля)."""
    return {
        "channel": f.channel,
        "post_id": f.post_id,
        "post_url": f.post_url,
        "pub_time": f.pub_time,
        "file_id": f.file_id,
        "type": f.file_type,
        "url": f.url,
        "filename": f.filename,
        "ext": f.ext,
        "mime_type": f.mime_type,
        "size": f.size,
        "kind": f.kind,
        "message_id": f.message_id,
        "family": f.family,
        "group_size": f.group_size,
        "family_size": f.family_size,
        "tier": f.tier,
        "status": status_label(f),
        "post_dir": f.post_dir,
        "target_path": f.target_path,
    }


def format_details(f: QueueFile) -> str:
    """Карточка файла (детали в GUI / list --details)."""
    lines = [
        f"{f.filename}  [{f.kind}]",
        f"  канал: {f.channel}  пост: {f.post_id}  file_id: {f.file_id}",
        f"  дата: {_date_str(f.pub_time)}  размер: {format_size(f.size)}",
        f"  статус: {status_label(f)}  приоритет: T{f.tier}",
    ]
    if f.group_size > 1:
        lines.append(f"  группа дублей (имя+размер): {f.group_size} копии")
    if f.family_size > 1:
        lines.append(f"  семейство версий: {f.family_size} версии")
    if f.url:
        lines.append(f"  url: {f.url}")
    if f.message_id is not None:
        lines.append(f"  message_id: {f.message_id}")
    if f.target_path:
        lines.append(f"  целевой путь: {f.target_path}")
    if f.on_disk:
        lines.append("  (уже лежит на диске в другом канале — по умолчанию не скачивается)")
    if f.dead:
        reason = f.dead_reason or "неизвестно"
        lines.append(f"  помечен мёртвым: {reason} (по умолчанию не показывается/не скачивается)")
    return "\n".join(lines)


REPORT_FIELDS: tuple[str, ...] = (
    "channel",
    "post_id",
    "post_url",
    "pub_time",
    "file_id",
    "type",
    "url",
    "filename",
    "ext",
    "mime_type",
    "size",
    "kind",
    "message_id",
    "family",
    "group_size",
    "family_size",
    "tier",
    "status",
    "post_dir",
    "target_path",
)


def write_json_report(path: str, files: list[QueueFile]) -> None:
    """Полный отчёт по файлам (после фильтров) в JSON-файл."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump([file_to_dict(f) for f in files], fh, ensure_ascii=False, indent=2)


def write_csv_report(path: str, files: list[QueueFile]) -> None:
    """Полный отчёт по файлам (после фильтров) в CSV/TSV-файл.

    Разделитель: табуляция для .tsv, запятая иначе; кавычки для имён с
    разделителем/кавычками/переносами ставит csv-модуль.
    """
    delimiter = "\t" if path.lower().endswith(".tsv") else ","
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter=delimiter)
        writer.writerow(REPORT_FIELDS)
        for f in files:
            d = file_to_dict(f)
            writer.writerow(["" if d.get(k) is None else d[k] for k in REPORT_FIELDS])


def _read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return fh.read()
    except OSError as e:
        msg = f"Не удалось прочитать {path}: {e}"
        raise ValueError(msg) from e


def _parse_json_report(path: str, text: str) -> list[dict[str, Any]]:
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError as e:
        msg = f"{path} не является валидным JSON: {e}"
        raise ValueError(msg) from e
    if not isinstance(data, list) or not all(isinstance(d, dict) for d in data):
        msg = f"{path}: ожидался JSON-массив объектов (отчёт `list --json`)"
        raise ValueError(msg)
    return data


def read_json_report(path: str) -> list[dict[str, Any]]:
    """Читает JSON-отчёт (write_json_report): список dict, по одному на файл."""
    return _parse_json_report(path, _read_text(path))


def read_report(path: str) -> list[dict[str, Any]]:
    """Читает отчёт для `download --list`: JSON или CSV/TSV (автоопределение).

    JSON, если файл начинается с [ или {; иначе CSV/TSV — табуляция, если в
    заголовке есть табы, иначе запятая. Файл без колонки channel — ошибка.
    """
    text = _read_text(path)
    if text.lstrip()[:1] in ("[", "{"):
        return _parse_json_report(path, text)
    first = next((line for line in text.splitlines() if line.strip()), "")
    if not first:
        msg = f"{path}: пустой файл (ожидался отчёт `list --json`/`list --csv`)"
        raise ValueError(msg)
    delimiter = "\t" if "\t" in first else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if not reader.fieldnames or "channel" not in reader.fieldnames:
        msg = f"{path}: не отчёт (ожидался заголовок с колонкой channel)"
        raise ValueError(msg)
    return list(reader)


def report_file_ids(report: list[Any]) -> set[tuple[str, int]]:
    """(channel, file_id) из записей отчёта; не-dict и записи без них пропускаются.

    file_id — int (JSON) либо числовая строка (CSV/TSV).
    """
    ids: set[tuple[str, int]] = set()
    for d in report:
        if not isinstance(d, dict):
            continue
        channel = d.get("channel")
        file_id: Any = d.get("file_id")
        if isinstance(file_id, str):
            file_id = int(file_id) if file_id.strip().isdigit() else None
        if not isinstance(channel, str) or not channel:
            continue
        if isinstance(file_id, bool) or not isinstance(file_id, int):
            continue
        ids.add((channel, file_id))
    return ids
