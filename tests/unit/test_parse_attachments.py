"""Офлайн-тесты parse_attachments.py (парсер очереди вложений).

Без сети и Telethon-клиента: make_client замещён фейком, сообщения —
простые SimpleNamespace.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from chat_search.scripts import parse_attachments as pa

# --- Фейки -----------------------------------------------------------------


def make_msg(
    msg_id: int,
    text: str | None = None,
    media: Any = None,
    date: datetime | None = None,
    forward: Any = None,
    reply_to_msg_id: int | None = None,
    grouped_id: int | None = None,
) -> Any:
    return SimpleNamespace(
        id=msg_id,
        date=date or datetime(2026, 2, 10, 12, 0, 0),
        text=text,
        media=media,
        forward=forward,
        reply_to_msg_id=reply_to_msg_id,
        grouped_id=grouped_id,
    )


def make_photo(sizes: list[int] | None = None) -> Any:
    values = sizes if sizes is not None else [100, 500]
    return SimpleNamespace(sizes=[SimpleNamespace(size=s) for s in values])


def make_document(file_name: str | None, mime_type: str | None, size: int | None) -> Any:
    # Как TL Document: имени на самом объекте нет, только DocumentAttributeFilename
    attributes = [SimpleNamespace(file_name=file_name)] if file_name else []
    return SimpleNamespace(mime_type=mime_type, size=size, attributes=attributes)


def make_media(photo: Any = None, document: Any = None, caption: str | None = None) -> Any:
    return SimpleNamespace(photo=photo, document=document, caption=caption)


class FakeClient:
    """Фейк TelegramClient: iter_messages отдаёт заранее заданные сообщения (новые -> старые)."""

    def __init__(self, messages_by_channel: dict[str, list[Any]]) -> None:
        self.messages_by_channel = messages_by_channel
        self.iter_calls: list[dict[str, Any]] = []

    async def start(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def get_entity(self, name: str) -> str:
        return name

    def iter_messages(
        self,
        entity: Any,
        min_id: int = 0,
        offset_date: datetime | None = None,
        wait_time: float | None = None,
    ) -> Any:
        self.iter_calls.append({
            "entity": entity,
            "min_id": min_id,
            "offset_date": offset_date,
        })
        messages = self.messages_by_channel.get(entity, [])

        async def gen() -> Any:
            # offset_date (exclusive): старт с самого свежего сообщения,
            # старше этой даты; дальше — всё по порядку (свежие -> старые).
            started = offset_date is None
            for msg in messages:
                if not started:
                    if msg.date >= offset_date:
                        continue
                    started = True
                if msg.id < min_id:
                    continue
                yield msg

        return gen()


class CrashingClient(FakeClient):
    """Падает на (crash_after + 1)-м сообщении — имитация сбоя посреди альбома."""

    def __init__(self, messages_by_channel: dict[str, list[Any]], crash_after: int) -> None:
        super().__init__(messages_by_channel)
        self.crash_after = crash_after

    def iter_messages(
        self,
        entity: Any,
        min_id: int = 0,
        offset_date: datetime | None = None,
        wait_time: float | None = None,
    ) -> Any:
        base = super().iter_messages(entity, min_id=min_id, offset_date=offset_date, wait_time=wait_time)
        yielded = 0

        async def gen() -> Any:
            nonlocal yielded
            async for msg in base:
                yielded += 1
                if yielded > self.crash_after:
                    raise RuntimeError("сбой посреди альбома")
                yield msg

        return gen()


@pytest.fixture
def make_parser(tmp_path: Any, monkeypatch: Any) -> Any:
    """Фабрика AttachmentQueueParser с фейковым клиентом (без сети)."""

    def _make(
        messages_by_channel: dict[str, list[Any]],
        **kwargs: Any,
    ) -> tuple[pa.AttachmentQueueParser, FakeClient]:
        fake = FakeClient(messages_by_channel)
        monkeypatch.setattr(pa.tg_client, "make_client", lambda api_id, api_hash: fake)
        parser = pa.AttachmentQueueParser(
            channels=["chan"],
            attachments_dir=str(tmp_path),
            api_id=1,
            api_hash="x",
            **kwargs,
        )
        return parser, fake

    return _make


def read_queue(channel_dir: str) -> list[dict[str, Any]]:
    queue_file = os.path.join(channel_dir, "queue.jsonl")
    if not os.path.exists(queue_file):
        return []
    with open(queue_file, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def read_state(channel_dir: str) -> int:
    state_file = os.path.join(channel_dir, ".queue_state.json")
    with open(state_file, encoding="utf-8") as f:
        return json.load(f)["max_processed_id"]


def write_state(channel_dir: str, max_processed_id: int) -> None:
    os.makedirs(channel_dir, exist_ok=True)
    with open(os.path.join(channel_dir, pa.STATE_FILE), "w", encoding="utf-8") as f:
        json.dump({"max_processed_id": max_processed_id}, f)


def write_queue(channel_dir: str, records: list[dict[str, Any]]) -> None:
    os.makedirs(channel_dir, exist_ok=True)
    with open(os.path.join(channel_dir, "queue.jsonl"), "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# --- date_range_utc ----------------------------------------------------------


def test_date_range_utc() -> None:
    start, end = pa.date_range_utc("2026-02-19")
    assert start == datetime(2026, 2, 19, tzinfo=UTC)
    assert end == datetime(2026, 2, 20, tzinfo=UTC)


def test_date_range_utc_invalid_format() -> None:
    with pytest.raises(ValueError):
        pa.date_range_utc("19.02.2026")
    with pytest.raises(ValueError):
        pa.date_range_utc("2026-13-40")


# --- extract_file_urls -------------------------------------------------------


def test_extract_file_urls_basic() -> None:
    text = "Скачай https://example.com/a.pdf и https://example.com/b.PDF?x=1#frag"
    assert pa.extract_file_urls(text) == [
        "https://example.com/a.pdf",
        "https://example.com/b.PDF?x=1#frag",
    ]


def test_extract_file_urls_trailing_punctuation() -> None:
    text = (
        "Файл: https://example.com/a.pdf. "
        "Ещё (https://example.com/b.zip) и https://example.com/c.jpg!"
    )
    assert pa.extract_file_urls(text) == [
        "https://example.com/a.pdf",
        "https://example.com/b.zip",
        "https://example.com/c.jpg",
    ]


def test_extract_file_urls_without_extension_skipped() -> None:
    text = "https://drive.google.com/file/d/123/view и https://example.com/page"
    assert pa.extract_file_urls(text) == []


def test_extract_file_urls_unknown_extension_skipped() -> None:
    assert pa.extract_file_urls("https://example.com/a.exe") == []
    assert pa.file_extension("https://example.com/a.exe") is None


def test_extract_file_urls_dedup() -> None:
    text = "https://example.com/a.pdf https://example.com/a.pdf"
    assert pa.extract_file_urls(text) == ["https://example.com/a.pdf"]


def test_extract_file_urls_empty() -> None:
    assert pa.extract_file_urls(None) == []
    assert pa.extract_file_urls("") == []
    assert pa.extract_file_urls("без ссылок") == []


# --- file_extension / url_filename / classify_kind ---------------------------


def test_file_extension_query_and_fragment() -> None:
    assert pa.file_extension("https://example.com/a.pdf?x=1#frag") == "pdf"
    assert pa.file_extension("https://example.com/dir/a.PDF") == "pdf"
    assert pa.file_extension("https://example.com/archive.tar.gz") == "gz"
    assert pa.file_extension("https://example.com/noext") is None
    assert pa.file_extension("https://example.com/2024.05/report") is None


def test_url_to_entry() -> None:
    entry = pa.url_to_entry("https://example.com/files/%D0%93%D0%9F%D0%A3.pdf?token=abc")
    assert entry == {
        "type": "external_url",
        "url": "https://example.com/files/%D0%93%D0%9F%D0%A3.pdf?token=abc",
        "filename": "ГПУ.pdf",
        "ext": "pdf",
        "size": None,
        "kind": "document",
    }


def test_url_to_entry_video() -> None:
    entry = pa.url_to_entry("https://example.com/vid.mp4")
    assert entry["ext"] == "mp4"
    assert entry["kind"] == "video"
    assert entry["filename"] == "vid.mp4"


def test_classify_kind() -> None:
    assert pa.classify_kind("png") == "photo"
    assert pa.classify_kind("MP4") == "video"
    assert pa.classify_kind("mp3") == "audio"
    assert pa.classify_kind("pdf") == "document"
    assert pa.classify_kind(None, "image/png") == "photo"
    assert pa.classify_kind(None, "video/mp4") == "video"
    assert pa.classify_kind(None, "application/octet-stream") == "document"
    assert pa.classify_kind(None, None) == "other"
    # ext важнее mime
    assert pa.classify_kind("mp4", "application/pdf") == "video"


# --- media_to_entry ----------------------------------------------------------


def test_media_to_entry_photo() -> None:
    entry = pa.media_to_entry(make_media(photo=make_photo([100, 500])), 42)
    assert entry == {
        "type": "telegram_media",
        "url": None,
        "filename": "photo_42.jpg",
        "ext": "jpg",
        "mime_type": "image/jpeg",
        "size": 500,
        "kind": "photo",
    }


def test_media_to_entry_photo_without_sizes() -> None:
    entry = pa.media_to_entry(make_media(photo=make_photo([])), 7)
    assert entry is not None
    assert entry["size"] is None
    assert entry["kind"] == "photo"


def test_media_to_entry_document() -> None:
    doc = make_document("report.PDF", "application/pdf", 123)
    entry = pa.media_to_entry(make_media(document=doc), 7)
    assert entry == {
        "type": "telegram_media",
        "url": None,
        "filename": "report.PDF",
        "ext": "pdf",
        "mime_type": "application/pdf",
        "size": 123,
        "kind": "document",
    }


def test_media_to_entry_video_document() -> None:
    doc = make_document("a.MOV", "video/mp4", 999)
    entry = pa.media_to_entry(make_media(document=doc), 1)
    assert entry is not None
    assert entry["kind"] == "video"
    assert entry["ext"] == "mov"


def test_media_to_entry_document_without_name_video() -> None:
    # Telegram-видео без file_name: имя генерируется из mime
    doc = make_document(None, "video/mp4", 1000)
    entry = pa.media_to_entry(make_media(document=doc), 7)
    assert entry == {
        "type": "telegram_media",
        "url": None,
        "filename": "file_7.mp4",
        "ext": "mp4",
        "mime_type": "video/mp4",
        "size": 1000,
        "kind": "video",
    }


def test_media_to_entry_document_without_name_pdf() -> None:
    doc = make_document(None, "application/pdf", 55)
    entry = pa.media_to_entry(make_media(document=doc), 42)
    assert entry is not None
    assert entry["filename"] == "file_42.pdf"
    assert entry["ext"] == "pdf"
    assert entry["kind"] == "document"


def test_media_to_entry_document_without_name_unknown_mime() -> None:
    doc = make_document(None, "application/x-unknown", 5)
    entry = pa.media_to_entry(make_media(document=doc), 7)
    assert entry is not None
    assert entry["filename"] == "file_7"
    assert entry["ext"] is None
    assert entry["kind"] == "document"


def test_media_to_entry_document_without_name_image() -> None:
    # Изображение, отправленное документом (без file_name)
    doc = make_document(None, "image/jpeg", 10)
    entry = pa.media_to_entry(make_media(document=doc), 8)
    assert entry is not None
    assert entry["filename"] == "file_8.jpg"
    assert entry["ext"] == "jpg"
    assert entry["kind"] == "photo"


def test_document_file_name_from_attributes_among_others() -> None:
    # Реальный документ: список атрибутов (видео + имя), имя — не первый
    doc = SimpleNamespace(
        mime_type="video/mp4",
        size=10,
        attributes=[
            SimpleNamespace(duration=2.6),
            SimpleNamespace(file_name="a.mp4"),
        ],
    )
    assert pa._document_file_name(doc) == "a.mp4"
    assert pa._document_file_name(SimpleNamespace(attributes=[])) is None


def test_mime_to_ext() -> None:
    assert pa.mime_to_ext("video/mp4") == "mp4"
    assert pa.mime_to_ext("VIDEO/MP4") == "mp4"
    assert pa.mime_to_ext("application/pdf") == "pdf"
    assert pa.mime_to_ext("application/x-unknown") is None
    assert pa.mime_to_ext(None) is None


def test_media_to_entry_other_media() -> None:
    assert pa.media_to_entry(SimpleNamespace(url="x", caption=None), 1) is None


# --- extract_message_urls ----------------------------------------------------


def test_extract_message_urls_text_and_caption_dedup() -> None:
    media = make_media(caption="https://example.com/a.pdf https://example.com/b.zip")
    msg = make_msg(1, text="https://example.com/a.pdf", media=media)
    assert pa.extract_message_urls(msg) == [
        "https://example.com/a.pdf",
        "https://example.com/b.zip",
    ]


def test_extract_message_urls_no_media() -> None:
    msg = make_msg(1, text="https://example.com/a.pdf")
    assert pa.extract_message_urls(msg) == ["https://example.com/a.pdf"]


# --- очередь: file_id / merge ------------------------------------------------


def test_max_channel_file_id() -> None:
    assert pa.max_channel_file_id([]) == 0
    queue = [
        {"post_id": 1, "files": [{"file_id": 1}, {"file_id": 2}]},
        {"post_id": 2, "files": [{"file_id": 5}, {"file_id": 6}]},
    ]
    assert pa.max_channel_file_id(queue) == 6


def test_merge_queue_sorted_and_no_overwrite() -> None:
    existing = [
        {"post_id": 3, "files": [{"file_id": 1}]},
        {"post_id": 1, "files": [{"file_id": 2}]},
    ]
    new = [
        {"post_id": 2, "files": []},
        {"post_id": 3, "files": [{"file_id": 99}]},
    ]
    merged = pa.merge_queue(existing, new)
    assert [r["post_id"] for r in merged] == [1, 2, 3]
    # Существующая строка не перезаписывается
    assert merged[2]["files"] == [{"file_id": 1}]


# --- build_post_record -------------------------------------------------------


def test_build_post_record_document() -> None:
    doc = make_document("a.pdf", "application/pdf", 10)
    date = datetime(2026, 2, 10, 12, 0, 0)
    msg = make_msg(
        356,
        text="текст",
        media=make_media(document=doc, caption="капшн"),
        date=date,
        reply_to_msg_id=100,
    )
    record = pa.build_post_record(msg, "ch_a")
    expected_ts = int((date - datetime(1970, 1, 1)).total_seconds())
    assert record["post_id"] == 356
    assert record["channel"] == "ch_a"
    assert record["url"] == "https://t.me/ch_a/356"
    assert record["pub_time"] == expected_ts
    assert record["text"] == "текст"
    assert record["caption"] == "капшн"
    assert record["media"] == {
        "type": "document",
        "file_name": "a.pdf",
        "mime_type": "application/pdf",
        "size": 10,
    }
    assert record["reply_to_message_id"] == 100
    assert record["forwarded_from"] is None
    assert record["parse_time"] is not None


def test_to_timestamp_naive_and_aware() -> None:
    naive = datetime(2026, 2, 10, 12, 0, 0)
    aware = datetime(2026, 2, 10, 12, 0, 0, tzinfo=UTC)
    expected = int(naive.replace(tzinfo=UTC).timestamp())
    assert pa._to_timestamp(naive) == expected
    assert pa._to_timestamp(aware) == expected
    assert pa._to_timestamp(None) is None


def test_build_post_record_aware_date() -> None:
    # Telethon 1.44+ отдаёт msg.date как aware UTC
    msg = make_msg(1, text="https://example.com/a.pdf", date=datetime(2026, 2, 10, 12, 0, 0, tzinfo=UTC))
    record = pa.build_post_record(msg, "chan")
    assert record["pub_time"] == int(datetime(2026, 2, 10, 12, 0, 0, tzinfo=UTC).timestamp())


def test_build_post_record_forward() -> None:
    # from_id — TL-объект (PeerChannel/PeerChat/PeerUser), а не int
    peer = SimpleNamespace(channel_id=123456)
    fwd = SimpleNamespace(from_id=peer, from_name=None, name="Другой канал")
    msg = make_msg(1, text=None, forward=fwd)
    record = pa.build_post_record(msg, "chan")
    assert record["forwarded_from"] == {"from_id": 123456, "name": "Другой канал"}


def test_build_post_record_forward_peer_user() -> None:
    peer = SimpleNamespace(user_id=42)
    fwd = SimpleNamespace(from_id=peer, from_name="Иван", name=None)
    msg = make_msg(1, text=None, forward=fwd)
    record = pa.build_post_record(msg, "chan")
    assert record["forwarded_from"] == {"from_id": 42, "name": "Иван"}


# --- parse_channel: полный прогон --------------------------------------------


def test_parse_channel_creates_queue_and_folders(tmp_path: Any, make_parser: Any) -> None:
    messages = [
        make_msg(3, text="Скачай https://example.com/a.pdf"),
        make_msg(2, media=make_media(photo=make_photo([100, 500]))),
        make_msg(1, text="обычный пост без файлов"),
    ]
    parser, fake = make_parser({"chan": messages})

    asyncio.run(parser.parse_channel("chan"))

    channel_dir = str(tmp_path / "chan")
    queue = read_queue(channel_dir)

    # Посты без файлов не регистрируются; сортировка по post_id
    assert [r["post_id"] for r in queue] == [2, 3]

    # file_id присваивается в порядке обработки (свежие посты первыми):
    # post 3 (URL) = 1, post 2 (фото) = 2
    assert queue[0]["files"] == [{
        "type": "telegram_media",
        "url": None,
        "filename": "photo_2.jpg",
        "ext": "jpg",
        "mime_type": "image/jpeg",
        "size": 500,
        "kind": "photo",
        "message_id": 2,
        "file_id": 2,
    }]
    assert queue[1]["files"] == [{
        "type": "external_url",
        "url": "https://example.com/a.pdf",
        "filename": "a.pdf",
        "ext": "pdf",
        "size": None,
        "kind": "document",
        "message_id": 3,
        "file_id": 1,
    }]
    # Одиночный пост: message_ids = [post_id]
    assert queue[0]["message_ids"] == [2]
    assert queue[1]["message_ids"] == [3]
    assert queue[1]["channel"] == "chan"
    assert queue[1]["url"] == "https://t.me/chan/3"

    # Папки постов: post.json + files.jsonl
    post_dir_3 = tmp_path / "chan" / "2026" / "2026-02" / "2026-02-10_3"
    post_dir_2 = tmp_path / "chan" / "2026" / "2026-02" / "2026-02-10_2"
    assert (post_dir_3 / "post.json").exists()
    assert (post_dir_3 / "files.jsonl").exists()
    assert (post_dir_2 / "post.json").exists()
    assert (post_dir_2 / "files.jsonl").exists()

    post_record = json.loads((post_dir_3 / "post.json").read_text(encoding="utf-8"))
    assert post_record["post_id"] == 3
    assert post_record["text"] == "Скачай https://example.com/a.pdf"

    files_lines = [
        json.loads(line)
        for line in (post_dir_3 / "files.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(files_lines) == 1
    assert files_lines[0]["file_id"] == 1
    assert files_lines[0]["url"] == "https://example.com/a.pdf"

    # Пост без файлов — без папки
    assert not (tmp_path / "chan" / "2026" / "2026-02" / "2026-02-10_1").exists()

    # State: max обработанного ID; первый прогон — без min_id
    assert read_state(channel_dir) == 3
    assert fake.iter_calls == [
        {"entity": "chan", "min_id": 0, "offset_date": None}
    ]


# --- parse_channel: альбомы (grouped_id) -------------------------------------


def test_parse_channel_album_grouped(tmp_path: Any, make_parser: Any) -> None:
    gid = 1001
    messages = [
        make_msg(106, media=make_media(photo=make_photo([10])), grouped_id=gid),
        make_msg(105, media=make_media(photo=make_photo([10])), grouped_id=gid),
        make_msg(
            104,
            text="капшн на весь альбом",
            media=make_media(photo=make_photo([10])),
            grouped_id=gid,
        ),
        make_msg(103, media=make_media(photo=make_photo([10])), grouped_id=gid),
        make_msg(102, media=make_media(photo=make_photo([10])), grouped_id=gid),
        make_msg(101, media=make_media(photo=make_photo([10])), grouped_id=gid),
    ]
    parser, _ = make_parser({"chan": messages})

    asyncio.run(parser.parse_channel("chan"))

    channel_dir = str(tmp_path / "chan")
    queue = read_queue(channel_dir)
    assert len(queue) == 1
    rec = queue[0]
    assert rec["post_id"] == 101
    assert rec["channel"] == "chan"
    assert rec["url"] == "https://t.me/chan/101"
    assert rec["message_ids"] == [101, 102, 103, 104, 105, 106]
    assert [f["file_id"] for f in rec["files"]] == [1, 2, 3, 4, 5, 6]
    # Файлы по возрастанию ID сообщений, у каждого файла свой message_id
    assert [f["message_id"] for f in rec["files"]] == [101, 102, 103, 104, 105, 106]
    assert [f["filename"] for f in rec["files"]] == [
        f"photo_{i}.jpg" for i in (101, 102, 103, 104, 105, 106)
    ]

    # Одна папка альбома: {date}_{min_id}; отдельных папок на участников нет
    post_dir = tmp_path / "chan" / "2026" / "2026-02" / "2026-02-10_101"
    assert (post_dir / "post.json").exists()
    assert (post_dir / "files.jsonl").exists()
    for i in (102, 103, 104, 105, 106):
        assert not (tmp_path / "chan" / "2026" / "2026-02" / f"2026-02-10_{i}").exists()

    post_record = json.loads((post_dir / "post.json").read_text(encoding="utf-8"))
    assert post_record["post_id"] == 101
    assert post_record["url"] == "https://t.me/chan/101"
    assert post_record["message_ids"] == [101, 102, 103, 104, 105, 106]
    # Текст — из сообщения с текстом (104), post_id/url — от минимального ID
    assert post_record["text"] == "капшн на весь альбом"
    assert [m["type"] for m in post_record["media"]] == ["photo"] * 6

    files_lines = [
        json.loads(line)
        for line in (post_dir / "files.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(files_lines) == 6
    assert [f["file_id"] for f in files_lines] == [1, 2, 3, 4, 5, 6]

    # State — до самого свежего ID альбома
    assert read_state(channel_dir) == 106


def test_parse_channel_album_member_already_queued(tmp_path: Any, make_parser: Any) -> None:
    # Старый формат очереди: фото альбома лежит отдельной строкой
    channel_dir = tmp_path / "chan"
    write_queue(str(channel_dir), [{
        "post_id": 103,
        "channel": "chan",
        "url": "https://t.me/chan/103",
        "pub_time": 0,
        "files": [{
            "file_id": 1, "type": "telegram_media", "url": None,
            "filename": "photo_103.jpg", "ext": "jpg", "mime_type": "image/jpeg",
            "size": 10, "kind": "photo",
        }],
    }])

    gid = 1001
    messages = [
        make_msg(106, media=make_media(photo=make_photo([10])), grouped_id=gid),
        make_msg(105, media=make_media(photo=make_photo([10])), grouped_id=gid),
        make_msg(104, media=make_media(photo=make_photo([10])), grouped_id=gid),
        make_msg(103, media=make_media(photo=make_photo([10])), grouped_id=gid),
        make_msg(102, media=make_media(photo=make_photo([10])), grouped_id=gid),
        make_msg(101, media=make_media(photo=make_photo([10])), grouped_id=gid),
    ]
    parser, _ = make_parser({"chan": messages})

    asyncio.run(parser.parse_channel("chan"))

    # Альбом не пересобран: старая строка на месте, новой строки нет
    queue = read_queue(str(channel_dir))
    assert [r["post_id"] for r in queue] == [103]
    assert queue[0]["files"][0]["file_id"] == 1
    assert not (tmp_path / "chan" / "2026" / "2026-02" / "2026-02-10_101").exists()
    # State продвигается — при следующем resume альбом не будет пересобран
    assert read_state(str(channel_dir)) == 106


def test_parse_channel_max_messages_mid_album(tmp_path: Any, make_parser: Any) -> None:
    # Лимит 1, но первое сообщение — начало альбома: альбом дочитывается целиком
    gid = 1001
    messages = [
        make_msg(103, media=make_media(photo=make_photo([10])), grouped_id=gid),
        make_msg(102, media=make_media(photo=make_photo([10])), grouped_id=gid),
        make_msg(101, text="https://example.com/a.pdf"),
    ]
    parser, _ = make_parser({"chan": messages}, max_messages=1)

    asyncio.run(parser.parse_channel("chan"))

    channel_dir = str(tmp_path / "chan")
    queue = read_queue(channel_dir)
    assert [r["post_id"] for r in queue] == [102]
    rec = queue[0]
    assert rec["message_ids"] == [102, 103]
    assert [f["file_id"] for f in rec["files"]] == [1, 2]
    # State охватывает весь дочитанный альбом
    assert read_state(channel_dir) == 103


def test_parse_channel_crash_mid_album_resume(
    tmp_path: Any, monkeypatch: Any, make_parser: Any
) -> None:
    gid = 1001
    messages = [
        make_msg(103, media=make_media(photo=make_photo([10])), grouped_id=gid),
        make_msg(102, media=make_media(photo=make_photo([10])), grouped_id=gid),
        make_msg(101, media=make_media(photo=make_photo([10])), grouped_id=gid),
    ]

    # Run 1: сбой на 3-м сообщении (посреди альбома) — ничего не записано
    crash_client = CrashingClient({"chan": messages}, crash_after=2)
    monkeypatch.setattr(pa.tg_client, "make_client", lambda api_id, api_hash: crash_client)
    parser = pa.AttachmentQueueParser(
        channels=["chan"],
        attachments_dir=str(tmp_path),
        api_id=1,
        api_hash="x",
    )
    with pytest.raises(RuntimeError, match="сбой посреди альбома"):
        asyncio.run(parser.parse_channel("chan"))

    channel_dir = str(tmp_path / "chan")
    assert read_queue(channel_dir) == []
    assert not os.path.exists(os.path.join(channel_dir, pa.STATE_FILE))

    # Run 2: альбом дочитан с нуля и собран целиком
    parser2, _ = make_parser({"chan": messages})
    asyncio.run(parser2.parse_channel("chan"))

    queue = read_queue(channel_dir)
    assert [r["post_id"] for r in queue] == [101]
    assert queue[0]["message_ids"] == [101, 102, 103]
    assert [f["file_id"] for f in queue[0]["files"]] == [1, 2, 3]
    assert read_state(channel_dir) == 103


def test_parse_channel_album_url_dedup(tmp_path: Any, make_parser: Any) -> None:
    # Один и тот же URL в двух сообщениях альбома — один файл (от первого по ID)
    gid = 1001
    messages = [
        make_msg(102, text="https://example.com/a.pdf", grouped_id=gid),
        make_msg(101, text="https://example.com/a.pdf", grouped_id=gid),
    ]
    parser, _ = make_parser({"chan": messages})

    asyncio.run(parser.parse_channel("chan"))

    queue = read_queue(str(tmp_path / "chan"))
    assert [r["post_id"] for r in queue] == [101]
    files = queue[0]["files"]
    assert len(files) == 1
    assert files[0]["url"] == "https://example.com/a.pdf"
    assert files[0]["message_id"] == 101
    assert files[0]["file_id"] == 1


def test_parse_channel_album_without_files_not_queued(tmp_path: Any, make_parser: Any) -> None:
    gid = 1001
    messages = [
        make_msg(102, text="продолжение", grouped_id=gid),
        make_msg(101, text="текст без файлов", grouped_id=gid),
    ]
    parser, _ = make_parser({"chan": messages})

    asyncio.run(parser.parse_channel("chan"))

    assert read_queue(str(tmp_path / "chan")) == []
    assert not (tmp_path / "chan" / "2026" / "2026-02" / "2026-02-10_101").exists()
    # State продвигается через группу без файлов
    assert read_state(str(tmp_path / "chan")) == 102


# --- parse_channel: resume ---------------------------------------------------


def test_parse_channel_resume_skips_processed(tmp_path: Any, make_parser: Any) -> None:
    channel_dir = tmp_path / "chan"
    write_state(str(channel_dir), 5)
    write_queue(str(channel_dir), [{
        "post_id": 5,
        "channel": "chan",
        "url": "https://t.me/chan/5",
        "pub_time": 0,
        "files": [{
            "file_id": 1, "type": "external_url", "url": "https://example.com/old.pdf",
            "filename": "old.pdf", "ext": "pdf", "size": None, "kind": "document",
        }],
    }])

    messages = [
        make_msg(6, text="https://example.com/new.pdf"),
        make_msg(5, text="https://example.com/old.pdf"),
        make_msg(4, text="https://example.com/skip.pdf"),
    ]
    parser, fake = make_parser({"chan": messages})

    asyncio.run(parser.parse_channel("chan"))

    # Resume: min_id = max_processed_id + 1
    assert fake.iter_calls == [
        {"entity": "chan", "min_id": 6, "offset_date": None}
    ]

    queue = read_queue(str(channel_dir))
    assert [r["post_id"] for r in queue] == [5, 6]
    # Существующая строка не изменилась, новый post получил file_id = max + 1
    assert queue[0]["files"][0]["file_id"] == 1
    assert queue[1]["files"][0]["file_id"] == 2
    assert queue[1]["files"][0]["url"] == "https://example.com/new.pdf"

    assert read_state(str(channel_dir)) == 6


def test_parse_channel_full_scan_reprocesses_without_renumbering(
    tmp_path: Any, make_parser: Any
) -> None:
    channel_dir = tmp_path / "chan"
    write_state(str(channel_dir), 5)
    post_dir_5 = channel_dir / "2026" / "2026-02" / "2026-02-10_5"
    post_dir_5.mkdir(parents=True)
    custom_post = {"post_id": 5, "custom": True}
    (post_dir_5 / "post.json").write_text(
        json.dumps(custom_post, ensure_ascii=False), encoding="utf-8"
    )
    write_queue(str(channel_dir), [{
        "post_id": 5,
        "channel": "chan",
        "url": "https://t.me/chan/5",
        "pub_time": 0,
        "files": [{
            "file_id": 1, "type": "external_url", "url": "https://example.com/old.pdf",
            "filename": "old.pdf", "ext": "pdf", "size": None, "kind": "document",
        }],
    }])

    messages = [
        make_msg(5, text="https://example.com/old.pdf"),
        make_msg(4, text="https://example.com/missed.pdf"),
    ]
    parser, fake = make_parser({"chan": messages}, full_scan=True)

    asyncio.run(parser.parse_channel("chan"))

    # full_scan: обход без min_id
    assert fake.iter_calls == [
        {"entity": "chan", "min_id": 0, "offset_date": None}
    ]

    queue = read_queue(str(channel_dir))
    assert [r["post_id"] for r in queue] == [4, 5]
    # Пост 5 не пересобран: строка и file_id прежние, папка не переписана
    assert queue[1]["files"][0]["file_id"] == 1
    saved_post = json.loads((post_dir_5 / "post.json").read_text(encoding="utf-8"))
    assert saved_post == custom_post
    # Пропущенный пост 4 добавлен с file_id = max + 1
    assert queue[0]["files"][0]["file_id"] == 2
    assert queue[0]["files"][0]["url"] == "https://example.com/missed.pdf"

    # State продвинут до max обработанного
    assert read_state(str(channel_dir)) == 5


# --- parse_channel: фильтр по дате -------------------------------------------


def test_parse_channel_date_filter(tmp_path: Any, make_parser: Any) -> None:
    start = datetime(2026, 2, 19, tzinfo=UTC)
    end = datetime(2026, 2, 20, tzinfo=UTC)
    messages = [
        make_msg(
            3, text="https://example.com/day.pdf", date=datetime(2026, 2, 19, 12, 0, 0, tzinfo=UTC)
        ),
        make_msg(
            2, media=make_media(photo=make_photo([100])), date=datetime(2026, 2, 19, 6, 0, 0, tzinfo=UTC)
        ),
        # Вне окна (до начала дня) — не попадает
        make_msg(
            1, text="https://example.com/other.pdf", date=datetime(2026, 2, 18, 23, 0, 0, tzinfo=UTC)
        ),
    ]
    parser, fake = make_parser({"chan": messages}, min_date=start, max_date=end)

    asyncio.run(parser.parse_channel("chan"))

    # iter_messages с окном дат, без min_id
    assert fake.iter_calls == [
        {"entity": "chan", "min_id": 0, "offset_date": end}
    ]

    queue = read_queue(str(tmp_path / "chan"))
    assert [r["post_id"] for r in queue] == [2, 3]

    # Дата-фильтр не пишет state
    assert not os.path.exists(os.path.join(str(tmp_path / "chan"), pa.STATE_FILE))


def test_parse_channel_date_filter_keeps_state_and_skips_queued(
    tmp_path: Any, make_parser: Any
) -> None:
    channel_dir = tmp_path / "chan"
    write_state(str(channel_dir), 100)
    write_queue(str(channel_dir), [{
        "post_id": 2,
        "channel": "chan",
        "url": "https://t.me/chan/2",
        "pub_time": 0,
        "files": [{
            "file_id": 1, "type": "external_url", "url": "https://example.com/old.pdf",
            "filename": "old.pdf", "ext": "pdf", "size": None, "kind": "document",
        }],
    }])

    start = datetime(2026, 2, 19, tzinfo=UTC)
    end = datetime(2026, 2, 20, tzinfo=UTC)
    messages = [
        make_msg(2, text="https://example.com/old.pdf", date=datetime(2026, 2, 19, 10, 0, 0, tzinfo=UTC)),
        make_msg(3, text="https://example.com/new.pdf", date=datetime(2026, 2, 19, 11, 0, 0, tzinfo=UTC)),
    ]
    parser, fake = make_parser({"chan": messages}, min_date=start, max_date=end)

    asyncio.run(parser.parse_channel("chan"))

    # Дата-режим: без min_id, state не изменён (100, а не 3)
    assert fake.iter_calls == [
        {"entity": "chan", "min_id": 0, "offset_date": end}
    ]
    assert read_state(str(channel_dir)) == 100

    queue = read_queue(str(channel_dir))
    assert [r["post_id"] for r in queue] == [2, 3]
    # Существующий пост не пересобран, новый получил file_id = max + 1
    assert queue[0]["files"][0]["file_id"] == 1
    assert queue[1]["files"][0]["file_id"] == 2
    assert queue[1]["files"][0]["url"] == "https://example.com/new.pdf"


def test_parse_channel_date_filter_with_max_messages(tmp_path: Any, make_parser: Any) -> None:
    start = datetime(2026, 2, 19, tzinfo=UTC)
    end = datetime(2026, 2, 20, tzinfo=UTC)
    messages = [
        make_msg(i, text=f"https://example.com/f{i}.pdf", date=datetime(2026, 2, 19, 10, i, 0, tzinfo=UTC))
        for i in (5, 4, 3, 2, 1)
    ]
    parser, _ = make_parser({"chan": messages}, min_date=start, max_date=end, max_messages=2)

    asyncio.run(parser.parse_channel("chan"))

    queue = read_queue(str(tmp_path / "chan"))
    # Лимит работает и в дата-режиме: только 2 самых свежих в окне
    assert [r["post_id"] for r in queue] == [4, 5]
    assert not os.path.exists(os.path.join(str(tmp_path / "chan"), pa.STATE_FILE))


def test_parse_channel_max_messages_limit(tmp_path: Any, make_parser: Any) -> None:
    messages = [
        make_msg(i, text=f"https://example.com/f{i}.pdf") for i in (5, 4, 3, 2, 1)
    ]
    parser, fake = make_parser({"chan": messages}, max_messages=3)

    asyncio.run(parser.parse_channel("chan"))

    queue = read_queue(str(tmp_path / "chan"))
    # Обработаны только 3 самых свежих (5, 4, 3)
    assert [r["post_id"] for r in queue] == [3, 4, 5]
    assert read_state(str(tmp_path / "chan")) == 5


def test_parse_channel_get_entity_error(tmp_path: Any, monkeypatch: Any) -> None:
    """Недоступный канал — ошибка, не падение."""
    fake = FakeClient({})

    async def failing_get_entity(name: str) -> str:
        raise RuntimeError("нет такого канала")

    monkeypatch.setattr(pa.tg_client, "make_client", lambda api_id, api_hash: fake)
    monkeypatch.setattr(fake, "get_entity", failing_get_entity)
    parser = pa.AttachmentQueueParser(
        channels=["chan"],
        attachments_dir=str(tmp_path),
        api_id=1,
        api_hash="x",
    )

    asyncio.run(parser.parse_channel("chan"))

    assert read_queue(str(tmp_path / "chan")) == []


# --- размеры внешних URL -----------------------------------------------------


def test_fill_external_sizes(monkeypatch: Any, make_parser: Any) -> None:
    parser, _ = make_parser({})
    entries = [
        {"type": "external_url", "url": "https://example.com/a.pdf", "size": None},
        {"type": "external_url", "url": "https://example.com/b.pdf", "size": None},
    ]
    monkeypatch.setattr(
        parser, "_probe_url_size", lambda url: 12345 if url.endswith("a.pdf") else None
    )
    parser._fill_external_sizes(entries)
    assert entries[0]["size"] == 12345
    assert entries[1]["size"] is None


def test_fill_external_sizes_probe_error(monkeypatch: Any, make_parser: Any) -> None:
    parser, _ = make_parser({})
    entries = [{"type": "external_url", "url": "https://example.com/a.pdf", "size": None}]

    def boom(url: str) -> int:
        raise RuntimeError("сбой")

    monkeypatch.setattr(parser, "_probe_url_size", boom)
    parser._fill_external_sizes(entries)
    assert entries[0]["size"] is None


def test_probe_url_size_head(monkeypatch: Any, make_parser: Any) -> None:
    parser, _ = make_parser({})
    resp = SimpleNamespace(status_code=200, headers={"Content-Length": "999"})
    monkeypatch.setattr(parser.http_session, "head", lambda url, **kw: resp)
    assert parser._probe_url_size("https://example.com/a.pdf") == 999


class FakeStreamResponse:
    """Ответ GET stream: контекстный менеджер + статус/заголовки."""

    def __init__(self, status_code: int, headers: dict[str, str]) -> None:
        self.status_code = status_code
        self.headers = headers

    def __enter__(self) -> FakeStreamResponse:
        return self

    def __exit__(self, *args: Any) -> bool:
        return False


def test_probe_url_size_head_405_fallback_to_get(monkeypatch: Any, make_parser: Any) -> None:
    parser, _ = make_parser({})
    head_resp = SimpleNamespace(status_code=405, headers={})
    get_resp = FakeStreamResponse(200, {"Content-Length": "77"})
    monkeypatch.setattr(parser.http_session, "head", lambda url, **kw: head_resp)
    monkeypatch.setattr(parser.http_session, "get", lambda url, **kw: get_resp)
    assert parser._probe_url_size("https://example.com/a.pdf") == 77


def test_probe_url_size_error(monkeypatch: Any, make_parser: Any) -> None:
    parser, _ = make_parser({})

    def head_error(url: str, **kw: Any) -> Any:
        raise ConnectionError("нет сети")

    monkeypatch.setattr(parser.http_session, "head", head_error)
    monkeypatch.setattr(parser.http_session, "get", head_error)
    assert parser._probe_url_size("https://example.com/a.pdf") is None


# --- fetch_sizes в parse_channel ---------------------------------------------


def test_parse_channel_fetch_sizes_updates_queue(
    tmp_path: Any, make_parser: Any, monkeypatch: Any
) -> None:
    messages = [make_msg(1, text="https://example.com/a.pdf")]
    parser, _ = make_parser({"chan": messages}, fetch_sizes=True)
    monkeypatch.setattr(parser, "_probe_url_size", lambda url: 4321)

    asyncio.run(parser.parse_channel("chan"))

    queue = read_queue(str(tmp_path / "chan"))
    assert queue[0]["files"][0]["size"] == 4321
