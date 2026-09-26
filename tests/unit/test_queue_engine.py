"""Тесты chat_search.queue.engine: probe_size, асинхронный движок скачивания."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
import requests
from queue_fixtures import (
    FakeHTTPSession,
    FakeMessage,
    FakeTGClient,
    _FakeResp,
    _make_file,
    _status_lines,
)

from chat_search.queue import DEAD_FILE, load_dead
from chat_search.queue.engine import DownloadQueueEngine, probe_size

# -- probe_size -------------------------------------------------------------


def test_probe_size_head_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(requests, "head", lambda *a, **k: _FakeResp(200, {"Content-Length": "123"}))
    assert probe_size("http://x") == 123


def test_probe_size_fallback_get(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(requests, "head", lambda *a, **k: _FakeResp(405, {}))
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResp(200, {"Content-Length": "55"}))
    assert probe_size("http://x") == 55


def test_probe_size_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*a: object, **k: object) -> _FakeResp:
        raise requests.exceptions.RequestException("nope")

    monkeypatch.setattr(requests, "head", _boom)
    assert probe_size("http://x") is None


# -- движок -----------------------------------------------------------------


def test_engine_http_success(tmp_path: Path) -> None:
    f = _make_file(tmp_path, 1, "external_url", None)
    events: list[tuple[str, int, bool, str | None]] = []
    engine = DownloadQueueEngine(
        None,
        concurrency=2,
        event_callback=lambda e, ff, ok, err: events.append((e, ff.file_id, ok, err)),
        http_session=FakeHTTPSession(content=b"hello"),
    )

    async def run() -> None:
        await engine.submit([f])
        await engine.run(finish_on_empty=True)

    asyncio.run(run())
    assert engine.stats.done == 1 and engine.stats.errors == 0
    assert (tmp_path / "post" / "file_1.bin").read_bytes() == b"hello"
    recs = _status_lines(tmp_path / "post")
    assert [r["status"] for r in recs] == ["success"] and recs[0]["file_id"] == 1
    assert ("finished", 1, True, None) in events


def test_engine_size_mismatch(tmp_path: Path) -> None:
    f = _make_file(tmp_path, 1, "external_url", size=100)
    engine = DownloadQueueEngine(None, http_session=FakeHTTPSession(content=b"hello"))

    async def run() -> None:
        await engine.submit([f])
        await engine.run(finish_on_empty=True)

    asyncio.run(run())
    assert engine.stats.errors == 1
    assert not (tmp_path / "post" / "file_1.bin").exists()
    assert not (tmp_path / "post" / "file_1.bin.part").exists()
    recs = _status_lines(tmp_path / "post")
    assert recs[0]["status"] == "error" and recs[0]["error"].startswith("size_mismatch")


def test_engine_http_404(tmp_path: Path) -> None:
    f = _make_file(tmp_path, 1, "external_url", None)
    engine = DownloadQueueEngine(None, http_session=FakeHTTPSession(status=404))

    async def run() -> None:
        await engine.submit([f])
        await engine.run(finish_on_empty=True)

    asyncio.run(run())
    assert engine.stats.errors == 1
    assert _status_lines(tmp_path / "post")[0]["error"] == "http_404"


def test_engine_http_404_marks_dead(tmp_path: Path) -> None:
    f = _make_file(tmp_path, 1, "external_url", None)
    engine = DownloadQueueEngine(
        None,
        http_session=FakeHTTPSession(status=404),
        attachments_dir=str(tmp_path / "attach"),
    )

    async def run() -> None:
        await engine.submit([f])
        await engine.run(finish_on_empty=True)

    asyncio.run(run())
    dead = load_dead(str(tmp_path / "attach"))
    assert set(dead) == {("ch_a", 1)}
    assert dead[("ch_a", 1)]["reason"] == "http_404"
    assert (tmp_path / "attach" / "ch_a" / DEAD_FILE).is_file()


def test_engine_http_500_not_dead(tmp_path: Path) -> None:
    f = _make_file(tmp_path, 1, "external_url", None)
    engine = DownloadQueueEngine(
        None,
        http_session=FakeHTTPSession(status=500),
        attachments_dir=str(tmp_path / "attach"),
    )

    async def run() -> None:
        await engine.submit([f])
        await engine.run(finish_on_empty=True)

    asyncio.run(run())
    assert engine.stats.errors == 1
    assert load_dead(str(tmp_path / "attach")) == {}


def test_engine_404_without_attachments_dir_no_dead_file(tmp_path: Path) -> None:
    f = _make_file(tmp_path, 1, "external_url", None)
    engine = DownloadQueueEngine(None, http_session=FakeHTTPSession(status=404))

    async def run() -> None:
        await engine.submit([f])
        await engine.run(finish_on_empty=True)

    asyncio.run(run())
    assert engine.stats.errors == 1
    assert not (tmp_path / "attach").exists()


def test_engine_tg_message_not_found_marks_dead(tmp_path: Path) -> None:
    class NotFoundClient(FakeTGClient):
        async def get_messages(self, entity: Any, ids: int | None = None) -> Any:
            return None

    client = NotFoundClient()
    f = _make_file(tmp_path, 7, "telegram_media", None, url=None, message_id=123)
    engine = DownloadQueueEngine(
        client,
        http_session=FakeHTTPSession(),
        attachments_dir=str(tmp_path / "attach"),
    )

    async def run() -> None:
        await engine.submit([f])
        await engine.run(finish_on_empty=True)

    asyncio.run(run())
    dead = load_dead(str(tmp_path / "attach"))
    assert set(dead) == {("ch_a", 7)}
    assert dead[("ch_a", 7)]["reason"] == "message_not_found"


def test_engine_tg_success(tmp_path: Path) -> None:
    client = FakeTGClient()
    f = _make_file(tmp_path, 7, "telegram_media", None, url=None, message_id=123)
    engine = DownloadQueueEngine(client, http_session=FakeHTTPSession())

    async def run() -> None:
        await engine.submit([f])
        await engine.run(finish_on_empty=True)

    asyncio.run(run())
    assert engine.stats.done == 1
    assert (tmp_path / "post" / "file_7.bin").read_bytes() == b"tg-bytes"
    assert client.downloads and client.downloads[0][0].message_id == 123


def test_engine_tg_file_reference_expired(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chat_search.queue import engine as engine_module

    class FakeFileRefError(Exception):
        pass

    monkeypatch.setattr(engine_module, "FileReferenceExpiredError", FakeFileRefError)
    client = FakeTGClient(fail_with=FakeFileRefError())
    f = _make_file(tmp_path, 7, "telegram_media", None, url=None, message_id=123)
    engine = DownloadQueueEngine(client, http_session=FakeHTTPSession())

    async def run() -> None:
        await engine.submit([f])
        await engine.run(finish_on_empty=True)

    asyncio.run(run())
    assert engine.stats.errors == 1
    assert _status_lines(tmp_path / "post")[0]["error"] == "file_reference_expired"


def test_engine_tg_not_authorized(tmp_path: Path) -> None:
    client = FakeTGClient()
    client.authorized = False
    f = _make_file(tmp_path, 7, "telegram_media", None, url=None, message_id=123)
    engine = DownloadQueueEngine(client, http_session=FakeHTTPSession())

    async def run() -> None:
        await engine.submit([f])
        await engine.run(finish_on_empty=True)

    asyncio.run(run())
    assert engine.stats.errors == 1
    assert _status_lines(tmp_path / "post")[0]["error"] == "telegram_not_authorized"


def test_engine_tg_no_media(tmp_path: Path) -> None:
    class NoMediaClient(FakeTGClient):
        async def get_messages(self, entity: Any, ids: int | None = None) -> Any:
            return FakeMessage(ids, media=None)

    client = NoMediaClient()
    f = _make_file(tmp_path, 7, "telegram_media", None, url=None, message_id=123)
    engine = DownloadQueueEngine(client, http_session=FakeHTTPSession())

    async def run() -> None:
        await engine.submit([f])
        await engine.run(finish_on_empty=True)

    asyncio.run(run())
    assert engine.stats.errors == 1
    assert _status_lines(tmp_path / "post")[0]["error"] == "no_media"


def test_engine_tg_connect_error_keeps_code_and_detail(tmp_path: Path) -> None:
    class BrokenClient(FakeTGClient):
        async def connect(self) -> None:
            raise RuntimeError("boom")

    client = BrokenClient()
    f1 = _make_file(tmp_path, 7, "telegram_media", None, url=None, message_id=123)
    f2 = _make_file(tmp_path, 8, "telegram_media", None, url=None, message_id=124)
    events: list[tuple[str, int, bool, str | None]] = []
    engine = DownloadQueueEngine(
        client,
        event_callback=lambda e, ff, ok, err: events.append((e, ff.file_id, ok, err)),
        http_session=FakeHTTPSession(),
    )

    async def run() -> None:
        await engine.submit([f1, f2])
        await engine.run(finish_on_empty=True)

    asyncio.run(run())
    assert engine.stats.errors == 2
    lines = _status_lines(tmp_path / "post")
    assert sorted(r["error"] for r in lines) == ["telegram_client_error", "telegram_client_error"]
    errs = sorted(err for _, _, ok, err in events if not ok)
    assert errs == ["telegram_client_error: boom", "telegram_client_error: boom"]


def test_engine_skips_done_and_parallel(tmp_path: Path) -> None:
    files = [_make_file(tmp_path, i, "external_url", None) for i in range(1, 5)]
    files[0].done = True
    engine = DownloadQueueEngine(None, concurrency=3, http_session=FakeHTTPSession(content=b"z"))

    async def run() -> None:
        added = await engine.submit(files)
        await engine.run(finish_on_empty=True)
        return added

    added = asyncio.run(run())
    assert added == 3
    assert engine.stats.done == 3
    assert files[0].target_path is None or not Path(files[0].target_path).exists()
