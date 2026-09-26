"""Тесты download_cli: run_list / run_download / run_post, parse_post_spec.

Движок и Telegram-клиент заменены фейками — сеть и авторизация не нужны.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from queue_fixtures import (
    T3,
    FakeHTTPSession,
    _FakeResp,
    _file,
    _post,
    get_file,
    load_prepared,
    make_fixture,
)

from chat_search import tg_client
from chat_search.queue import (
    QueueFile,
    load_dead,
    mark_dead,
    read_report,
    write_csv_report,
    write_json_report,
)
from chat_search.scripts import download_cli as dcli


def _add_tg_channel(root: Path) -> None:
    """Канал ch_c с одним telegram_media-файлом (post 400)."""
    (root / "ch_c").mkdir(parents=True, exist_ok=True)
    tg_file = _file(1, "video.mp4", 100, "video", file_type="telegram_media", url=None, message_id=55)
    with (root / "ch_c" / "queue.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_post(400, "ch_c", T3, [tg_file])) + "\n")


def _install_fake_engine(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Движок-фейк: запоминает конструктор и submitted-файлы (без сети)."""
    instances: list[Any] = []

    class FakeEngine:
        def __init__(
            self,
            client: Any,
            concurrency: int = 4,
            event_callback: Any = None,
            http_session: Any = None,
            attachments_dir: Any = None,
        ) -> None:
            self.client = client
            self.concurrency = concurrency
            self.attachments_dir = attachments_dir
            self.submitted: list[QueueFile] = []
            self.stats = SimpleNamespace(done=0, errors=0)
            instances.append(self)

        async def submit(self, files: list[QueueFile]) -> int:
            self.submitted = list(files)
            self.stats.done = len(self.submitted)
            return len(self.submitted)

        async def run(self, finish_on_empty: bool) -> None:
            return None

    monkeypatch.setattr(dcli, "DownloadQueueEngine", FakeEngine)
    return instances


def _install_fake_tg_client(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    """make_client-фейк: фиксирует вызовы, возвращает сентинел."""
    calls: list[bool] = []
    sentinel = object()

    def fake_make_client(api_id: int, api_hash: str, session_name: str = "get_chat") -> object:
        calls.append(True)
        return sentinel

    monkeypatch.setattr(tg_client, "get_credentials", lambda: (1, "hash"))
    monkeypatch.setattr(tg_client, "make_client", fake_make_client)
    return calls


def _report(report: Path) -> list[dict[str, Any]]:
    return json.loads(report.read_text(encoding="utf-8"))


# -- parse_post_spec ---------------------------------------------------------


def test_parse_post_spec_plain() -> None:
    assert dcli.parse_post_spec("ch_b/300") == ("ch_b", 300)


def test_parse_post_spec_url() -> None:
    assert dcli.parse_post_spec("https://t.me/ch_b/300") == ("ch_b", 300)


@pytest.mark.parametrize("spec", ["", "ch_b", "ch_b/", "ch_b/abc"])
def test_parse_post_spec_invalid(spec: str) -> None:
    with pytest.raises(ValueError):
        dcli.parse_post_spec(spec)


# -- run_list ----------------------------------------------------------------


def test_run_list_default_excludes_done(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    make_fixture(tmp_path)
    post_dir = tmp_path / "ch_a" / "2023" / "2023-11" / "2023-11-14_100"
    post_dir.mkdir(parents=True)
    (post_dir / "downloaded.jsonl").write_text(
        json.dumps({"file_id": 1, "status": "success"}) + "\n", encoding="utf-8"
    )
    report = tmp_path / "report.json"
    dcli.run_list(str(tmp_path), json_path=str(report))
    data = _report(report)
    assert len(data) == 5
    assert not any(d["channel"] == "ch_a" and d["file_id"] == 1 for d in data)
    out = capsys.readouterr().out
    assert "Всего: 5 файлов" in out
    assert "В порядке приоритетов (T1 -> T3):" in out
    assert "Отчёт:" in out


def test_run_list_show_all_includes_done(tmp_path: Path) -> None:
    make_fixture(tmp_path)
    post_dir = tmp_path / "ch_a" / "2023" / "2023-11" / "2023-11-14_100"
    post_dir.mkdir(parents=True)
    (post_dir / "downloaded.jsonl").write_text(
        json.dumps({"file_id": 1, "status": "success"}) + "\n", encoding="utf-8"
    )
    report = tmp_path / "report.json"
    dcli.run_list(str(tmp_path), json_path=str(report), show_all=True)
    data = _report(report)
    assert len(data) == 6
    done = [d for d in data if d["channel"] == "ch_a" and d["file_id"] == 1]
    assert done and done[0]["status"] == "done"


def test_run_list_filters(tmp_path: Path) -> None:
    make_fixture(tmp_path)
    report = tmp_path / "report.json"
    dcli.run_list(str(tmp_path), kinds=["photo"], json_path=str(report))
    data = _report(report)
    assert [(d["channel"], d["file_id"]) for d in data] == [("ch_b", 3)]
    dcli.run_list(str(tmp_path), exts=["pdf"], json_path=str(report))
    data = _report(report)
    assert {(d["channel"], d["file_id"]) for d in data} == {("ch_a", 1), ("ch_b", 1)}
    dcli.run_list(str(tmp_path), channels=["ch_a"], json_path=str(report))
    data = _report(report)
    assert len(data) == 2 and all(d["channel"] == "ch_a" for d in data)


def test_run_list_priority_order(tmp_path: Path) -> None:
    make_fixture(tmp_path)
    report = tmp_path / "report.json"
    dcli.run_list(str(tmp_path), json_path=str(report))
    data = _report(report)
    assert [(d["channel"], d["file_id"]) for d in data] == [
        ("ch_b", 3),  # T1 photo
        ("ch_b", 4),  # T2 app_2.0.zip
        ("ch_b", 1),  # T2 report.pdf
        ("ch_a", 2),  # T3 app_1.0.zip
        ("ch_b", 2),  # T3 app_2.0.zip
        ("ch_a", 1),  # T3 report.pdf
    ]


def test_run_list_writes_csv_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    make_fixture(tmp_path)
    report = tmp_path / "report.csv"
    dcli.run_list(str(tmp_path), csv_path=str(report))
    data = read_report(str(report))
    assert len(data) == 6
    assert {(d["channel"], int(d["file_id"])) for d in data} == {
        ("ch_a", 1),
        ("ch_a", 2),
        ("ch_b", 1),
        ("ch_b", 2),
        ("ch_b", 3),
        ("ch_b", 4),
    }
    out = capsys.readouterr().out
    assert f"Отчёт: {report}" in out


# -- run_download --------------------------------------------------------------


def test_run_download_filters_and_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_fixture(tmp_path)
    instances = _install_fake_engine(monkeypatch)
    _install_fake_tg_client(monkeypatch)
    dcli.run_download(str(tmp_path), kinds=["document"], limit=2)
    assert len(instances) == 1
    assert [(f.channel, f.file_id) for f in instances[0].submitted] == [("ch_b", 4), ("ch_b", 1)]
    assert instances[0].client is None


def test_run_download_no_client_without_tg_media(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_fixture(tmp_path)
    instances = _install_fake_engine(monkeypatch)
    calls = _install_fake_tg_client(monkeypatch)
    dcli.run_download(str(tmp_path), kinds=["photo"])
    assert calls == []
    assert instances[0].client is None
    assert [(f.channel, f.file_id) for f in instances[0].submitted] == [("ch_b", 3)]


def test_run_download_creates_client_for_tg_media(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_fixture(tmp_path)
    _add_tg_channel(tmp_path)
    instances = _install_fake_engine(monkeypatch)
    calls = _install_fake_tg_client(monkeypatch)
    dcli.run_download(str(tmp_path), kinds=["video"])
    assert calls == [True]
    assert instances[0].client is not None
    assert [(f.channel, f.file_id) for f in instances[0].submitted] == [("ch_c", 1)]


def test_run_download_nothing_to_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    make_fixture(tmp_path)
    instances = _install_fake_engine(monkeypatch)
    dcli.run_download(str(tmp_path), kinds=["audio"])
    assert instances == []
    assert "Нечего скачивать." in capsys.readouterr().out


def _write_file_list(tmp_path: Path, files: list[QueueFile]) -> Path:
    path = tmp_path / "file_list.json"
    write_json_report(str(path), files)
    return path


def _write_csv_list(tmp_path: Path, files: list[QueueFile], suffix: str = "csv") -> Path:
    path = tmp_path / f"file_list.{suffix}"
    write_csv_report(str(path), files)
    return path


def test_run_download_from_list_only_those_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_fixture(tmp_path)
    files = load_prepared(tmp_path)
    path = _write_file_list(tmp_path, [get_file(files, "ch_b", 3), get_file(files, "ch_a", 1)])
    instances = _install_fake_engine(monkeypatch)
    _install_fake_tg_client(monkeypatch)
    dcli.run_download(str(tmp_path), file_list=str(path))
    assert [(f.channel, f.file_id) for f in instances[0].submitted] == [("ch_b", 3), ("ch_a", 1)]


def test_run_download_from_list_resume_skips_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_fixture(tmp_path)
    post_dir = tmp_path / "ch_b" / "2023" / "2023-11" / "2023-11-15_300"
    post_dir.mkdir(parents=True)
    (post_dir / "downloaded.jsonl").write_text(
        json.dumps({"file_id": 3, "status": "success"}) + "\n", encoding="utf-8"
    )
    files = load_prepared(tmp_path)
    path = _write_file_list(tmp_path, [get_file(files, "ch_b", 3), get_file(files, "ch_a", 1)])
    instances = _install_fake_engine(monkeypatch)
    _install_fake_tg_client(monkeypatch)
    dcli.run_download(str(tmp_path), file_list=str(path))
    assert [(f.channel, f.file_id) for f in instances[0].submitted] == [("ch_a", 1)]


def test_run_download_from_list_unknown_entries_warn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    make_fixture(tmp_path)
    files = load_prepared(tmp_path)
    path = _write_file_list(tmp_path, [get_file(files, "ch_b", 3)])
    data = json.loads(path.read_text(encoding="utf-8"))
    data.append({"channel": "ch_z", "file_id": 99})
    path.write_text(json.dumps(data), encoding="utf-8")
    instances = _install_fake_engine(monkeypatch)
    _install_fake_tg_client(monkeypatch)
    dcli.run_download(str(tmp_path), file_list=str(path))
    assert [(f.channel, f.file_id) for f in instances[0].submitted] == [("ch_b", 3)]
    assert "не найдено в очереди" in capsys.readouterr().out


def test_run_download_from_list_invalid_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_fixture(tmp_path)
    _install_fake_engine(monkeypatch)
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError):
        dcli.run_download(str(tmp_path), file_list=str(bad))


@pytest.mark.parametrize("suffix", ["csv", "tsv"])
def test_run_download_from_csv_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    make_fixture(tmp_path)
    files = load_prepared(tmp_path)
    path = _write_csv_list(tmp_path, [get_file(files, "ch_b", 3), get_file(files, "ch_a", 1)], suffix)
    instances = _install_fake_engine(monkeypatch)
    _install_fake_tg_client(monkeypatch)
    dcli.run_download(str(tmp_path), file_list=str(path))
    assert [(f.channel, f.file_id) for f in instances[0].submitted] == [("ch_b", 3), ("ch_a", 1)]


# -- run_check -----------------------------------------------------------------


def test_run_check_marks_404_skips_done(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    make_fixture(tmp_path)
    post_dir = tmp_path / "ch_a" / "2023" / "2023-11" / "2023-11-14_100"
    post_dir.mkdir(parents=True)
    (post_dir / "downloaded.jsonl").write_text(
        json.dumps({"file_id": 1, "status": "success"}) + "\n", encoding="utf-8"
    )
    sess = FakeHTTPSession(head_status=404)
    dcli.run_check(str(tmp_path), http_session=sess)
    dead = load_dead(str(tmp_path))
    assert set(dead) == {("ch_a", 2), ("ch_b", 1), ("ch_b", 2), ("ch_b", 3), ("ch_b", 4)}
    out = capsys.readouterr().out
    assert "помечено мёртвыми 5" in out


def test_run_check_recheck_restores_and_keeps(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    make_fixture(tmp_path)
    mark_dead(str(tmp_path), "ch_a", 1, "http_404")
    mark_dead(str(tmp_path), "ch_a", 2, "http_410")

    class PerUrl(FakeHTTPSession):
        def head(self, url: str, **kwargs: Any) -> _FakeResp:
            code = 200 if "report.pdf" in url else 404
            return _FakeResp(code, {})

    dcli.run_check(str(tmp_path), recheck=True, http_session=PerUrl())
    dead = load_dead(str(tmp_path))
    assert set(dead) == {("ch_a", 2)}
    out = capsys.readouterr().out
    assert "снова доступно 1" in out


def test_run_check_nothing_to_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    make_fixture(tmp_path)
    # Все внешние URL уже скачаны — проверять нечего
    for ch, post, fid in [
        ("ch_a", "2023-11-14_100", 1),
        ("ch_a", "2023-11-14_100", 2),
        ("ch_b", "2023-11-15_200", 1),
        ("ch_b", "2023-11-15_200", 2),
        ("ch_b", "2023-11-15_300", 3),
        ("ch_b", "2023-11-15_300", 4),
    ]:
        post_dir = tmp_path / ch / "2023" / "2023-11" / post
        post_dir.mkdir(parents=True, exist_ok=True)
        with (post_dir / "downloaded.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"file_id": fid, "status": "success"}) + "\n")
    dcli.run_check(str(tmp_path), http_session=FakeHTTPSession(head_status=404))
    assert load_dead(str(tmp_path)) == {}
    assert "Проверять нечего." in capsys.readouterr().out


# -- run_list / run_download: dead ---------------------------------------------


def test_run_list_hides_dead_by_default(tmp_path: Path) -> None:
    make_fixture(tmp_path)
    mark_dead(str(tmp_path), "ch_a", 1, "http_404")
    report = tmp_path / "report.json"
    dcli.run_list(str(tmp_path), json_path=str(report))
    data = _report(report)
    assert len(data) == 5
    assert not any(d["channel"] == "ch_a" and d["file_id"] == 1 for d in data)
    dcli.run_list(str(tmp_path), json_path=str(report), show_all=True)
    data = _report(report)
    assert len(data) == 6
    dead = [d for d in data if d["channel"] == "ch_a" and d["file_id"] == 1]
    assert dead and dead[0]["status"] == "dead"


def test_run_download_skips_dead_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    make_fixture(tmp_path)
    mark_dead(str(tmp_path), "ch_b", 3, "http_404")
    _install_fake_tg_client(monkeypatch)
    instances1 = _install_fake_engine(monkeypatch)
    dcli.run_download(str(tmp_path), kinds=["photo"])
    assert instances1 == []
    assert "Нечего скачивать." in capsys.readouterr().out
    instances2 = _install_fake_engine(monkeypatch)
    dcli.run_download(str(tmp_path), kinds=["photo"], include_dead=True)
    assert [(f.channel, f.file_id) for f in instances2[0].submitted] == [("ch_b", 3)]


# -- run_post ------------------------------------------------------------------


def test_run_post_by_spec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_fixture(tmp_path)
    instances = _install_fake_engine(monkeypatch)
    _install_fake_tg_client(monkeypatch)
    dcli.run_post(str(tmp_path), "ch_b/300")
    assert len(instances) == 1
    assert [(f.channel, f.file_id) for f in instances[0].submitted] == [("ch_b", 3), ("ch_b", 4)]
    assert instances[0].client is None


def test_run_post_by_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_fixture(tmp_path)
    instances = _install_fake_engine(monkeypatch)
    _install_fake_tg_client(monkeypatch)
    dcli.run_post(str(tmp_path), "https://t.me/ch_b/300")
    assert [(f.channel, f.file_id) for f in instances[0].submitted] == [("ch_b", 3), ("ch_b", 4)]


def test_run_post_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    make_fixture(tmp_path)
    instances = _install_fake_engine(monkeypatch)
    with pytest.raises(SystemExit) as excinfo:
        dcli.run_post(str(tmp_path), "ch_b/999")
    assert excinfo.value.code == 1
    assert "не найден в очереди" in capsys.readouterr().out
    assert instances == []


def test_run_post_invalid_spec(tmp_path: Path) -> None:
    make_fixture(tmp_path)
    with pytest.raises(ValueError):
        dcli.run_post(str(tmp_path), "not-a-spec")


def test_run_post_resume_skips_done(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_fixture(tmp_path)
    post_dir = tmp_path / "ch_b" / "2023" / "2023-11" / "2023-11-15_300"
    post_dir.mkdir(parents=True)
    (post_dir / "downloaded.jsonl").write_text(
        json.dumps({"file_id": 3, "status": "success"}) + "\n", encoding="utf-8"
    )
    instances = _install_fake_engine(monkeypatch)
    _install_fake_tg_client(monkeypatch)
    dcli.run_post(str(tmp_path), "ch_b/300")
    assert [(f.channel, f.file_id) for f in instances[0].submitted] == [("ch_b", 4)]


def test_run_post_all_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    make_fixture(tmp_path)
    post_dir = tmp_path / "ch_b" / "2023" / "2023-11" / "2023-11-15_300"
    post_dir.mkdir(parents=True)
    (post_dir / "downloaded.jsonl").write_text(
        json.dumps({"file_id": 3, "status": "success"}) + "\n"
        + json.dumps({"file_id": 4, "status": "success"}) + "\n",
        encoding="utf-8",
    )
    instances = _install_fake_engine(monkeypatch)
    dcli.run_post(str(tmp_path), "ch_b/300")
    assert instances == []
    assert "нечего скачивать" in capsys.readouterr().out


def test_run_post_tg_media_creates_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_fixture(tmp_path)
    _add_tg_channel(tmp_path)
    instances = _install_fake_engine(monkeypatch)
    calls = _install_fake_tg_client(monkeypatch)
    dcli.run_post(str(tmp_path), "ch_c/400")
    assert calls == [True]
    assert instances[0].client is not None


# -- fire-обёртка --------------------------------------------------------------


def test_cli_post_invalid_spec_clean_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    make_fixture(tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        dcli.DownloadCLI().post(str(tmp_path), "bad-spec")
    assert excinfo.value.code == 1
    assert "Некорректная спецификация поста" in capsys.readouterr().out


def test_cli_list_delegates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(dcli, "run_list", lambda *a, **kw: calls.append((a, kw)))
    dcli.DownloadCLI().list(
        str(tmp_path), channels="ch_a,ch_b", kinds="photo", show_all=True, csv="/tmp/rep.csv"
    )
    args, kwargs = calls[0]
    assert args == (str(tmp_path), ["ch_a", "ch_b"], ["photo"], None)
    assert kwargs["show_all"] is True
    assert kwargs["json_path"] is None
    assert kwargs["csv_path"] == "/tmp/rep.csv"


def test_cli_download_delegates_file_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(dcli, "run_download", lambda *a, **kw: calls.append((a, kw)))
    dcli.DownloadCLI().download(str(tmp_path), list="/tmp/rep.json", limit=5)
    args, kwargs = calls[0]
    assert args == (str(tmp_path), None, None, None)
    assert kwargs["file_list"] == "/tmp/rep.json"
    assert kwargs["limit"] == 5
