"""Тесты download_queue: TUI smoke, статус-фильтр, опция «уникальные», обёртки."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from queue_fixtures import T1, T3, _file, _post, load_prepared, make_fixture
from textual.widgets import Input, Static, Tree

from chat_search.queue import post_dir_for
from chat_search.scripts import download_queue as dq

# -- TUI smoke ---------------------------------------------------------------


async def _tui_smoke(app: dq.DownloadQueueApp) -> None:
    async with app.run_test(size=(120, 30)):
        for _ in range(300):
            if app._all_files:
                break
            await asyncio.sleep(0.01)
        assert app._all_files, "load-воркер не завершился"
        tree = app.query_one("#tree", Tree)
        labels = [str(n.label) for n in tree.root.children]
        assert "document (5)" in labels
        assert "photo (1)" in labels
        details = app.query_one("#details", Static)
        assert details is not None


def test_tui_smoke(tmp_path: Path) -> None:
    make_fixture(tmp_path)
    app = dq.DownloadQueueApp(str(tmp_path))
    asyncio.run(_tui_smoke(app))


# -- статус-фильтр -------------------------------------------------------------


def _mark_done_and_dead(root: Path) -> None:
    """ch_a/100: report.pdf = done (downloaded.jsonl), app_1.0.zip = dead."""
    post_dir = root / "ch_a" / "2023" / "2023-11" / "2023-11-14_100"
    post_dir.mkdir(parents=True)
    (post_dir / "downloaded.jsonl").write_text(
        json.dumps({"file_id": 1, "status": "success"}) + "\n", encoding="utf-8"
    )
    (root / "ch_a" / ".dead.jsonl").write_text(
        json.dumps({"file_id": 2, "reason": "http_404"}) + "\n", encoding="utf-8"
    )


def test_gui_default_hides_done_and_dead(tmp_path: Path) -> None:
    make_fixture(tmp_path)
    _mark_done_and_dead(tmp_path)
    app = dq.DownloadQueueApp(str(tmp_path))
    app._all_files = load_prepared(tmp_path)
    app._apply_filters()
    assert {(f.channel, f.file_id) for f in app._visible} == {
        ("ch_b", 1),
        ("ch_b", 2),
        ("ch_b", 3),
        ("ch_b", 4),
    }


def test_gui_status_filter_explicit(tmp_path: Path) -> None:
    make_fixture(tmp_path)
    _mark_done_and_dead(tmp_path)
    app = dq.DownloadQueueApp(str(tmp_path))
    app._all_files = load_prepared(tmp_path)
    app._filter_values["status"] = "done"
    app._apply_filters()
    assert [(f.channel, f.file_id) for f in app._visible] == [("ch_a", 1)]
    app._filter_values["status"] = "dead"
    app._apply_filters()
    assert [(f.channel, f.file_id) for f in app._visible] == [("ch_a", 2)]
    app._filter_values["status"] = "pending,disk,error"
    app._apply_filters()
    assert {(f.channel, f.file_id) for f in app._visible} == {
        ("ch_b", 1),
        ("ch_b", 2),
        ("ch_b", 3),
        ("ch_b", 4),
    }


async def _tui_smoke_hides_done_dead(app: dq.DownloadQueueApp) -> None:
    async with app.run_test(size=(120, 30)):
        for _ in range(300):
            if app._all_files:
                break
            await asyncio.sleep(0.01)
        assert app._all_files, "load-воркер не завершился"
        tree = app.query_one("#tree", Tree)
        labels = [str(n.label) for n in tree.root.children]
        assert "document (3)" in labels
        assert "photo (1)" in labels


def test_tui_smoke_hides_done_dead(tmp_path: Path) -> None:
    make_fixture(tmp_path)
    _mark_done_and_dead(tmp_path)
    app = dq.DownloadQueueApp(str(tmp_path))
    asyncio.run(_tui_smoke_hides_done_dead(app))


def test_filter_screen_status_field(tmp_path: Path) -> None:
    make_fixture(tmp_path)
    app = dq.DownloadQueueApp(str(tmp_path), statuses=["done"])

    async def scenario() -> None:
        async with app.run_test(size=(120, 30)):
            for _ in range(300):
                if app._all_files:
                    break
                await asyncio.sleep(0.01)
            assert app._all_files, "load-воркер не завершился"
            app.action_filter_status()
            for _ in range(300):
                if isinstance(app.screen, dq.FilterScreen) and app.screen.is_mounted:
                    break
                await asyncio.sleep(0.01)
            screen = app.screen
            assert isinstance(screen, dq.FilterScreen)
            status_input = screen.query_one("#f-status", Input)
            assert status_input.value == "done"
            status_input.value = "done,dead"
            screen._apply()
            for _ in range(300):
                if app._filter_values.get("status") == "done,dead":
                    break
                await asyncio.sleep(0.01)
            assert app._filter_values["status"] == "done,dead"

    asyncio.run(scenario())


# -- опция «уникальные» -------------------------------------------------------


def _make_disk_dup(root: Path) -> None:
    """report.pdf (1000) в папке ch_a/100: ch_a/100 report = done, ch_b/200 = on_disk."""
    post_dir = Path(post_dir_for(str(root), "ch_a", T1, 100))
    post_dir.mkdir(parents=True, exist_ok=True)
    (post_dir / "report.pdf").write_bytes(b"x" * 1000)


def test_gui_unique_view_off_by_default(tmp_path: Path) -> None:
    make_fixture(tmp_path)
    _make_disk_dup(tmp_path)
    app = dq.DownloadQueueApp(str(tmp_path))
    app._all_files = load_prepared(tmp_path)
    app._apply_filters()
    # дефолт без опции: disk-файл (подтверждённый дубль) виден
    assert {(f.channel, f.file_id) for f in app._visible} == {
        ("ch_a", 2),
        ("ch_b", 1),
        ("ch_b", 2),
        ("ch_b", 3),
        ("ch_b", 4),
    }


async def _unique_toggle_scenario(app: dq.DownloadQueueApp) -> None:
    async with app.run_test(size=(120, 30)):
        for _ in range(300):
            if app._all_files:
                break
            await asyncio.sleep(0.01)
        assert app._all_files, "load-воркер не завершился"
        assert {(f.channel, f.file_id) for f in app._visible} == {
            ("ch_a", 2),
            ("ch_b", 2),
            ("ch_b", 3),
            ("ch_b", 4),
        }
        app.action_toggle_unique()
        assert {(f.channel, f.file_id) for f in app._visible} == {
            ("ch_a", 2),
            ("ch_b", 1),
            ("ch_b", 2),
            ("ch_b", 3),
            ("ch_b", 4),
        }
        app.action_toggle_unique()
        assert {(f.channel, f.file_id) for f in app._visible} == {
            ("ch_a", 2),
            ("ch_b", 2),
            ("ch_b", 3),
            ("ch_b", 4),
        }


def test_gui_unique_view_hides_confirmed_dup(tmp_path: Path) -> None:
    make_fixture(tmp_path)
    _make_disk_dup(tmp_path)
    app = dq.DownloadQueueApp(str(tmp_path), unique=True)
    asyncio.run(_unique_toggle_scenario(app))


def _add_unknown_size_file(root: Path) -> None:
    """ch_a/400: notes.txt (size=null) в очереди; notes.txt (42) на диске в ch_b/300."""
    post = _post(
        400,
        "ch_a",
        T3,
        [_file(3, "notes.txt", None, "document", url="http://example.com/notes.txt")],
    )
    with (root / "ch_a" / "queue.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(post) + "\n")
    post_dir = Path(post_dir_for(str(root), "ch_b", T3, 300))
    post_dir.mkdir(parents=True, exist_ok=True)
    (post_dir / "notes.txt").write_bytes(b"y" * 42)


def test_gui_unique_view_keeps_unknown_size(tmp_path: Path) -> None:
    make_fixture(tmp_path)
    _make_disk_dup(tmp_path)
    _add_unknown_size_file(tmp_path)
    app = dq.DownloadQueueApp(str(tmp_path))
    app._all_files = load_prepared(tmp_path)
    app._apply_filters()
    # on_disk по одному имени (size=None) не подтверждён — виден обеим веткам
    assert {(f.channel, f.file_id) for f in app._visible} == {
        ("ch_a", 2),
        ("ch_a", 3),
        ("ch_b", 1),
        ("ch_b", 2),
        ("ch_b", 3),
        ("ch_b", 4),
    }
    app._unique_view = True
    app._apply_filters()
    assert {(f.channel, f.file_id) for f in app._visible} == {
        ("ch_a", 2),
        ("ch_a", 3),
        ("ch_b", 2),
        ("ch_b", 3),
        ("ch_b", 4),
    }


def test_gui_unique_view_status_filter_wins(tmp_path: Path) -> None:
    make_fixture(tmp_path)
    _make_disk_dup(tmp_path)
    app = dq.DownloadQueueApp(str(tmp_path), unique=True)
    app._all_files = load_prepared(tmp_path)
    app._filter_values["status"] = "disk"
    app._apply_filters()
    # статус-фильтр побеждает: виден подтверждённый дубль
    assert [(f.channel, f.file_id) for f in app._visible] == [("ch_b", 1)]


# -- обёртки CLI над общей реализацией ---------------------------------------


def test_cli_list_wrapper_delegates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run_list(
        attachments_dir: str,
        channels: list[str] | None = None,
        kinds: list[str] | None = None,
        exts: list[str] | None = None,
        json_path: str | None = None,
        csv_path: str | None = None,
        show_all: bool = False,
        details: bool = False,
    ) -> None:
        calls.append(
            {
                "attachments_dir": attachments_dir,
                "channels": channels,
                "kinds": kinds,
                "exts": exts,
                "json_path": json_path,
                "csv_path": csv_path,
                "show_all": show_all,
                "details": details,
            }
        )

    monkeypatch.setattr(dq, "run_list", fake_run_list)
    dq.DownloadQueueCLI().list(
        str(tmp_path), channels="ch_a,ch_b", kinds="photo", details=True, csv="/tmp/rep.tsv"
    )
    assert calls == [
        {
            "attachments_dir": str(tmp_path),
            "channels": ["ch_a", "ch_b"],
            "kinds": ["photo"],
            "exts": None,
            "json_path": None,
            "csv_path": "/tmp/rep.tsv",
            "show_all": True,
            "details": True,
        }
    ]


def test_cli_download_wrapper_delegates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run_download(
        attachments_dir: str,
        channels: list[str] | None = None,
        kinds: list[str] | None = None,
        exts: list[str] | None = None,
        limit: int | None = None,
        concurrency: int = 4,
        force_on_disk: bool = False,
        file_list: str | None = None,
    ) -> None:
        calls.append(
            {
                "attachments_dir": attachments_dir,
                "channels": channels,
                "kinds": kinds,
                "exts": exts,
                "limit": limit,
                "concurrency": concurrency,
                "force_on_disk": force_on_disk,
                "file_list": file_list,
            }
        )

    monkeypatch.setattr(dq, "run_download", fake_run_download)
    dq.DownloadQueueCLI().download(str(tmp_path), ext="pdf", limit=5, force_on_disk=True)
    assert calls == [
        {
            "attachments_dir": str(tmp_path),
            "channels": None,
            "kinds": None,
            "exts": ["pdf"],
            "limit": 5,
            "concurrency": 4,
            "force_on_disk": True,
            "file_list": None,
        }
    ]


def test_cli_list_output_unified_format(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Обёртка list: сводка + полная таблица в порядке приоритетов (show_all)."""
    make_fixture(tmp_path)
    dq.DownloadQueueCLI().list(str(tmp_path))
    out = capsys.readouterr().out
    assert "Всего: 6 файлов" in out
    assert "В порядке приоритетов (T1 -> T3):" in out
    # все 6 файлов (show_all), T1 photo первым
    assert out.index("ch_b/300  photo.jpg") < out.index("ch_b/200  app_2.0.zip")
    assert "ch_a/100  report.pdf" in out
