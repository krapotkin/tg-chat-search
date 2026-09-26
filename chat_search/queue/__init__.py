"""
chat_search.queue — Общие модули очереди вложений (часть 2).

Переиспользуются скриптами chat_search.scripts.download_queue (GUI + CLI-
обёртки) и chat_search.scripts.download_cli (консольные команды):
- models    — QueueFile, DiskIndex, константы
- queue     — загрузка очереди, скан диска, приоритеты, фильтры
- engine    — асинхронный движок скачивания (Telegram + HTTP)
- reporting — сводки, форматирование, отчёты (JSON/CSV/TSV)

Без зависимостей от chat_search.scripts и без fire/textual/print:
печать и CLI-обвязка остаются в скриптах.
"""

from chat_search.queue.dead import (
    DEAD_ERROR_CODES,
    check_urls,
    load_dead,
    mark_dead,
    probe_url,
    unmark_dead,
)
from chat_search.queue.engine import (
    DownloadError,
    DownloadQueueEngine,
    DownloadStats,
    probe_size,
)
from chat_search.queue.models import (
    DEAD_FILE,
    DOWNLOADED_FILE,
    KIND_ORDER,
    QUEUE_FILE,
    DiskIndex,
    QueueFile,
)
from chat_search.queue.queue import (
    discover_channels,
    filter_files,
    load_queue,
    parse_csv,
    post_dir_for,
    prepare_queue,
    scan_disk,
    select_for_download,
    sort_key,
    version_family,
)
from chat_search.queue.reporting import (
    REPORT_FIELDS,
    Summary,
    build_summary,
    file_to_dict,
    format_details,
    format_row,
    format_size,
    format_summary,
    read_json_report,
    read_report,
    report_file_ids,
    status_label,
    write_csv_report,
    write_json_report,
)

__all__ = [
    "DEAD_ERROR_CODES",
    "DEAD_FILE",
    "DOWNLOADED_FILE",
    "DownloadError",
    "DownloadQueueEngine",
    "DownloadStats",
    "DiskIndex",
    "KIND_ORDER",
    "QUEUE_FILE",
    "QueueFile",
    "REPORT_FIELDS",
    "Summary",
    "build_summary",
    "check_urls",
    "discover_channels",
    "file_to_dict",
    "filter_files",
    "format_details",
    "format_row",
    "format_size",
    "format_summary",
    "load_dead",
    "load_queue",
    "mark_dead",
    "parse_csv",
    "post_dir_for",
    "prepare_queue",
    "probe_size",
    "probe_url",
    "read_json_report",
    "read_report",
    "report_file_ids",
    "scan_disk",
    "select_for_download",
    "sort_key",
    "status_label",
    "unmark_dead",
    "version_family",
    "write_csv_report",
    "write_json_report",
]
