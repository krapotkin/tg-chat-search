"""
models.py — Модели данных очереди вложений (часть 2).

QueueFile — файл из queue.jsonl канала + вычисленные поля (тир, группы,
целевой путь); DiskIndex — индекс файлов на диске для resume и поиска
дублей.
"""

from __future__ import annotations

from dataclasses import dataclass

QUEUE_FILE = "queue.jsonl"
DOWNLOADED_FILE = "downloaded.jsonl"
DEAD_FILE = ".dead.jsonl"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

KIND_ORDER: dict[str, int] = {"photo": 0, "video": 1, "audio": 2, "document": 3, "other": 4}


@dataclass
class QueueFile:
    """Файл из очереди канала + вычисленные поля (тир, группа, целевой путь)."""

    channel: str
    post_id: int
    post_url: str
    pub_time: int | None
    file_id: int
    file_type: str
    url: str | None
    filename: str
    ext: str | None
    mime_type: str | None
    size: int | None
    kind: str
    message_id: int | None

    # Вычисляемые (prepare_queue)
    family: str | None = None
    group_size: int = 1
    family_size: int = 1
    tier: int = 1
    done: bool = False
    on_disk: bool = False
    dead: bool = False
    dead_reason: str | None = None
    last_error: str | None = None
    post_dir: str | None = None
    target_path: str | None = None

    # Служебные (время выполнения)
    downloading: bool = False
    probed_size: int | None = None


@dataclass
class DiskIndex:
    """Индекс файлов на диске: для resume (пропуск) и поиска дублей."""

    path_sizes: dict[str, int]
    by_name_size: dict[tuple[str, int], list[str]]
    by_name: dict[str, list[str]]
    last_status: dict[tuple[str, int], str]

    def is_done(self, channel: str, file_id: int, target_path: str | None, size: int | None) -> bool:
        """Уже скачано: success в downloaded.jsonl или целевой файл на диске."""
        if self.last_status.get((channel, file_id)) == "success":
            return True
        if target_path is not None and target_path in self.path_sizes:
            disk_size = self.path_sizes[target_path]
            return size is None or disk_size == size
        return False

    def on_disk_path(self, filename: str, size: int | None) -> str | None:
        """Путь к файлу с тем же именем (и размером, если известен) в любом канале."""
        if size is not None:
            paths = self.by_name_size.get((filename.lower(), size))
            if paths:
                return paths[0]
        paths = self.by_name.get(filename.lower())
        if paths:
            return paths[0]
        return None
