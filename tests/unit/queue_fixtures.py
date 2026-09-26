"""Общие фикстуры для тестов chat_search.queue и консольных клиентов скачивания.

make_fixture — два канала с дублями и семейством версий:
    ch_a/100 (T1): report.pdf (1000), app_1.0.zip (2000)
    ch_b/200 (T2): report.pdf (1000), app_2.0.zip (2000)
    ch_b/300 (T3): photo.jpg (500), app_2.0.zip (2000)
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from chat_search.queue import QueueFile, load_dead, load_queue, prepare_queue, scan_disk

T1 = 1_700_000_000  # 2023-11-14 22:13:20 UTC
T2 = T1 + 3600
T3 = T1 + 7200


def _file(
    file_id: int,
    filename: str,
    size: int | None,
    kind: str,
    file_type: str = "external_url",
    url: str | None = "http://example.com/x",
    message_id: int | None = None,
) -> dict[str, Any]:
    ext = filename.rsplit(".", 1)[-1] if "." in filename else None
    return {
        "type": file_type,
        "url": url,
        "filename": filename,
        "ext": ext,
        "mime_type": None,
        "size": size,
        "kind": kind,
        "message_id": message_id,
        "file_id": file_id,
    }


def _post(post_id: int, channel: str, pub_time: int, files: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "post_id": post_id,
        "channel": channel,
        "url": f"https://t.me/{channel}/{post_id}",
        "pub_time": pub_time,
        "message_ids": [post_id],
        "files": files,
    }


def make_fixture(root: Path) -> None:
    """Стандартная очередь: ch_a (2 файла), ch_b (4 файла, дубли и версии)."""
    (root / "ch_a").mkdir(parents=True, exist_ok=True)
    (root / "ch_a" / "queue.jsonl").write_text(
        json.dumps(
            _post(
                100,
                "ch_a",
                T1,
                [
                    _file(1, "report.pdf", 1000, "document", url="http://example.com/report.pdf"),
                    _file(2, "app_1.0.zip", 2000, "document", url="http://example.com/app_1.0.zip"),
                ],
            )
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "ch_b").mkdir(parents=True, exist_ok=True)
    (root / "ch_b" / "queue.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    _post(
                        200,
                        "ch_b",
                        T2,
                        [
                            _file(1, "report.pdf", 1000, "document", url="http://example.com/r2.pdf"),
                            _file(2, "app_2.0.zip", 2000, "document", url="http://example.com/a2.zip"),
                        ],
                    )
                ),
                json.dumps(
                    _post(
                        300,
                        "ch_b",
                        T3,
                        [
                            _file(3, "photo.jpg", 500, "photo", url="http://example.com/p.jpg"),
                            _file(4, "app_2.0.zip", 2000, "document", url="http://example.com/a2b.zip"),
                        ],
                    )
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def load_prepared(root: Path) -> list[QueueFile]:
    """load_queue + scan_disk + load_dead + prepare_queue по каталогу."""
    files = load_queue(str(root))
    disk = scan_disk(str(root))
    dead = load_dead(str(root))
    prepare_queue(str(root), files, disk, dead=dead)
    return files


def get_file(files: list[QueueFile], channel: str, file_id: int) -> QueueFile:
    return next(f for f in files if f.channel == channel and f.file_id == file_id)


def _make_file(
    tmp_path: Path,
    file_id: int,
    file_type: str,
    size: int | None,
    url: str | None = "http://example.com/x",
    message_id: int | None = None,
) -> QueueFile:
    """Готовый QueueFile с post_dir/target_path в tmp_path/post (для движка)."""
    f = QueueFile(
        channel="ch_a",
        post_id=100,
        post_url="",
        pub_time=T1,
        file_id=file_id,
        file_type=file_type,
        url=url,
        filename=f"file_{file_id}.bin",
        ext="bin",
        mime_type=None,
        size=size,
        kind="other",
        message_id=message_id,
    )
    f.post_dir = str(tmp_path / "post")
    f.target_path = str(tmp_path / "post" / f.filename)
    (tmp_path / "post").mkdir(parents=True, exist_ok=True)
    return f


def _status_lines(post_dir: Path) -> list[dict[str, Any]]:
    text = (post_dir / "downloaded.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.strip().splitlines()]


class _FakeResp:
    def __init__(self, status_code: int, headers: dict[str, str]) -> None:
        self.status_code = status_code
        self.headers = headers

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *args: object) -> bool:
        return False


class FakeHTTPResponse:
    def __init__(self, status_code: int, content: bytes) -> None:
        self.status_code = status_code
        self._content = content

    def __enter__(self) -> FakeHTTPResponse:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def iter_content(self, chunk_size: int = 1) -> Any:
        for i in range(0, len(self._content), chunk_size):
            yield self._content[i : i + chunk_size]


class FakeHTTPSession:
    def __init__(
        self,
        status: int = 200,
        content: bytes = b"",
        head_status: int | None = None,
        head_raises: Exception | None = None,
        head_delay: float = 0.0,
    ) -> None:
        self.status = status
        self.content = content
        self.head_status = head_status if head_status is not None else status
        self.head_raises = head_raises
        self.head_delay = head_delay
        self.head_calls = 0
        self.get_calls = 0
        self._lock = threading.Lock()
        self._inflight = 0
        self.max_inflight = 0

    def head(self, url: str, **kwargs: Any) -> _FakeResp:
        with self._lock:
            self.head_calls += 1
            self._inflight += 1
            self.max_inflight = max(self.max_inflight, self._inflight)
        try:
            if self.head_delay:
                time.sleep(self.head_delay)
            if self.head_raises is not None:
                raise self.head_raises
            return _FakeResp(self.head_status, {})
        finally:
            with self._lock:
                self._inflight -= 1

    def get(self, url: str, **kwargs: Any) -> FakeHTTPResponse:
        self.get_calls += 1
        return FakeHTTPResponse(self.status, self.content)


_FAKE_MEDIA = object()


class FakeMessage:
    def __init__(self, message_id: int, media: Any = _FAKE_MEDIA) -> None:
        self.message_id = message_id
        self.media = media


class FakeTGClient:
    def __init__(self, fail_with: Exception | None = None) -> None:
        self.connected = False
        self.authorized = True
        self.downloads: list[tuple[Any, str]] = []
        self.fail_with = fail_with

    def is_connected(self) -> bool:
        return self.connected

    async def connect(self) -> None:
        self.connected = True

    async def is_user_authorized(self) -> bool:
        return self.authorized

    async def get_entity(self, username: str) -> Any:
        return ("entity", username)

    async def get_messages(self, entity: Any, ids: int | None = None) -> Any:
        return FakeMessage(ids)

    async def download_media(self, message: Any, file: str) -> None:
        self.downloads.append((message, file))
        if self.fail_with is not None:
            raise self.fail_with
        Path(file).write_bytes(b"tg-bytes")
