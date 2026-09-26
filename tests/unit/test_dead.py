"""Тесты chat_search.queue.dead: mark/load/unmark, probe_url, check_urls."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import requests
from queue_fixtures import FakeHTTPSession, _FakeResp, _make_file

from chat_search.queue import DEAD_FILE, check_urls, load_dead, mark_dead, probe_url, unmark_dead


def _read_dead(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").strip().splitlines()]


# -- mark_dead / load_dead ----------------------------------------------------


def test_mark_dead_appends(tmp_path: Path) -> None:
    mark_dead(str(tmp_path), "ch_a", 1, "http_404", url="http://x", filename="a.pdf")
    mark_dead(str(tmp_path), "ch_a", 2, "http_410")
    path = tmp_path / "ch_a" / DEAD_FILE
    recs = _read_dead(path)
    assert [(r["file_id"], r["reason"]) for r in recs] == [(1, "http_404"), (2, "http_410")]
    assert recs[0]["url"] == "http://x" and recs[0]["filename"] == "a.pdf"
    assert "checked_at" in recs[0]


def test_load_dead_last_wins(tmp_path: Path) -> None:
    mark_dead(str(tmp_path), "ch_a", 1, "http_404")
    mark_dead(str(tmp_path), "ch_a", 1, "http_410")
    dead = load_dead(str(tmp_path))
    assert set(dead) == {("ch_a", 1)}
    assert dead[("ch_a", 1)]["reason"] == "http_410"


def test_load_dead_skips_broken_lines(tmp_path: Path) -> None:
    mark_dead(str(tmp_path), "ch_a", 1, "http_404")
    path = tmp_path / "ch_a" / DEAD_FILE
    with path.open("a", encoding="utf-8") as fh:
        fh.write("not json\n")
    dead = load_dead(str(tmp_path))
    assert set(dead) == {("ch_a", 1)}


def test_load_dead_channel_filter(tmp_path: Path) -> None:
    mark_dead(str(tmp_path), "ch_a", 1, "http_404")
    mark_dead(str(tmp_path), "ch_b", 2, "http_410")
    assert set(load_dead(str(tmp_path), ["ch_a"])) == {("ch_a", 1)}
    assert set(load_dead(str(tmp_path), ["ch_b"])) == {("ch_b", 2)}
    assert set(load_dead(str(tmp_path), ["ch_z"])) == set()


def test_load_dead_missing_dir(tmp_path: Path) -> None:
    assert load_dead(str(tmp_path / "nope")) == {}


# -- unmark_dead ---------------------------------------------------------------


def test_unmark_dead_removes_record(tmp_path: Path) -> None:
    mark_dead(str(tmp_path), "ch_a", 1, "http_404")
    mark_dead(str(tmp_path), "ch_a", 2, "http_410")
    unmark_dead(str(tmp_path), "ch_a", 1)
    dead = load_dead(str(tmp_path))
    assert set(dead) == {("ch_a", 2)}


def test_unmark_dead_deletes_empty_file(tmp_path: Path) -> None:
    mark_dead(str(tmp_path), "ch_a", 1, "http_404")
    path = tmp_path / "ch_a" / DEAD_FILE
    unmark_dead(str(tmp_path), "ch_a", 1)
    assert not path.exists()


def test_unmark_dead_no_file(tmp_path: Path) -> None:
    unmark_dead(str(tmp_path), "ch_a", 1)
    assert load_dead(str(tmp_path)) == {}


# -- probe_url ------------------------------------------------------------------


def test_probe_url_ok() -> None:
    assert probe_url(FakeHTTPSession(head_status=200), "http://x") == "ok"


def test_probe_url_404() -> None:
    assert probe_url(FakeHTTPSession(head_status=404), "http://x") == "http_404"


def test_probe_url_410() -> None:
    assert probe_url(FakeHTTPSession(head_status=410), "http://x") == "http_410"


def test_probe_url_500() -> None:
    assert probe_url(FakeHTTPSession(head_status=500), "http://x") == "http_500"


def test_probe_url_head_403_get_200() -> None:
    sess = FakeHTTPSession(head_status=403, status=200)
    assert probe_url(sess, "http://x") == "ok"
    assert sess.get_calls == 1


def test_probe_url_head_405_get_404() -> None:
    sess = FakeHTTPSession(head_status=405, status=404)
    assert probe_url(sess, "http://x") == "http_404"


def test_probe_url_error() -> None:
    sess = FakeHTTPSession(head_raises=requests.exceptions.RequestException("nope"))
    assert probe_url(sess, "http://x") == "error"


# -- check_urls ------------------------------------------------------------------


def test_check_urls_mixed(tmp_path: Path) -> None:
    class PerUrl(FakeHTTPSession):
        def head(self, url: str, **kwargs: Any) -> _FakeResp:
            code = 404 if "notfound" in url else 200
            return _FakeResp(code, {})

    files = [
        _make_file(tmp_path, 1, "external_url", None, url="http://x/ok"),
        _make_file(tmp_path, 2, "external_url", None, url="http://x/notfound"),
    ]
    results = asyncio.run(check_urls(files, concurrency=2, session=PerUrl()))
    assert [(f.file_id, res) for f, res in results] == [(1, "ok"), (2, "http_404")]


def test_check_urls_concurrency_respected(tmp_path: Path) -> None:
    files = [
        _make_file(tmp_path, i, "external_url", None, url=f"http://x/{i}") for i in range(1, 9)
    ]
    sess = FakeHTTPSession(head_status=200, head_delay=0.05)
    asyncio.run(check_urls(files, concurrency=3, session=sess))
    assert sess.head_calls == 8
    assert sess.max_inflight <= 3
