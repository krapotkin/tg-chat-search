"""Тесты chat_search.queue.queue: очередь, тиры, диск, фильтры."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from queue_fixtures import T1, _file, _post, get_file, load_prepared, make_fixture

from chat_search.queue import (
    REPORT_FIELDS,
    QueueFile,
    discover_channels,
    filter_files,
    parse_csv,
    post_dir_for,
    read_json_report,
    read_report,
    report_file_ids,
    select_for_download,
    version_family,
    write_csv_report,
    write_json_report,
)
from chat_search.queue.reporting import format_size

# -- version_family ---------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("app_2.12.4.zip", "app"),
        ("app_v1.2.dmg", "app"),
        ("app2.12.4.bin", "app"),
        ("app-1.2.3.tar.gz", None),  # двойное расширение: версия не в конце stem
        ("file 1.0.txt", "file"),
        ("report.pdf", None),
        ("2.12.4.bin", None),
        ("APP_9.9.zip", "app"),
    ],
)
def test_version_family(name: str, expected: str | None) -> None:
    assert version_family(name) == expected


# -- мелкие утилиты ---------------------------------------------------------


def test_parse_csv() -> None:
    assert parse_csv(None) is None
    assert parse_csv("") is None
    assert parse_csv("  ") is None
    assert parse_csv("a, b ,c") == ["a", "b", "c"]


def test_format_size() -> None:
    assert format_size(None) == "?"
    assert format_size(500) == "500 Б"
    assert format_size(2048) == "2.0 КБ"
    assert format_size(5 * 1024 * 1024) == "5.0 МБ"


def test_read_json_report_roundtrip(tmp_path: Path) -> None:
    files = load_prepared(tmp_path)
    path = tmp_path / "report.json"
    write_json_report(str(path), files)
    data = read_json_report(str(path))
    assert report_file_ids(data) == {(f.channel, f.file_id) for f in files}


def test_read_json_report_invalid(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError):
        read_json_report(str(bad))
    obj = tmp_path / "obj.json"
    obj.write_text('{"a": 1}', encoding="utf-8")
    with pytest.raises(ValueError):
        read_json_report(str(obj))
    with pytest.raises(ValueError):
        read_json_report(str(tmp_path / "nope.json"))


def test_report_file_ids_skips_malformed() -> None:
    data = [{"channel": "ch_a", "file_id": 1}, {"channel": "ch_a"}, {"file_id": 2}, "junk"]
    assert report_file_ids(data) == {("ch_a", 1)}


def test_report_file_ids_csv_strings() -> None:
    """CSV/TSV: file_id строкой; пустые channel/file_id пропускаются."""
    data = [
        {"channel": "ch_a", "file_id": "7"},
        {"channel": "", "file_id": "3"},
        {"channel": "ch_b", "file_id": ""},
        {"channel": "ch_c", "file_id": "x"},
    ]
    assert report_file_ids(data) == {("ch_a", 7)}


def test_write_csv_report_roundtrip(tmp_path: Path) -> None:
    make_fixture(tmp_path)
    files = load_prepared(tmp_path)
    path = tmp_path / "report.csv"
    write_csv_report(str(path), files)
    text = path.read_text(encoding="utf-8")
    assert text.splitlines()[0] == ",".join(REPORT_FIELDS)
    data = read_report(str(path))
    assert report_file_ids(data) == {(f.channel, f.file_id) for f in files}
    by_id = {(d["channel"], int(d["file_id"])): d for d in data}
    assert by_id[("ch_a", 1)]["filename"] == "report.pdf"
    assert by_id[("ch_b", 3)]["kind"] == "photo"


def test_write_csv_report_quoting_and_tsv(tmp_path: Path) -> None:
    make_fixture(tmp_path)
    files = load_prepared(tmp_path)
    f = get_file(files, "ch_a", 1)
    f.filename = 'a,b "q".pdf'  # запятая и кавычки в имени
    path = tmp_path / "report.tsv"
    write_csv_report(str(path), [f])
    header = path.read_text(encoding="utf-8").splitlines()[0]
    assert header.startswith("channel\tpost_id\t")
    data = read_report(str(path))
    assert report_file_ids(data) == {("ch_a", 1)}
    assert data[0]["filename"] == 'a,b "q".pdf'


def test_read_report_invalid(tmp_path: Path) -> None:
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ValueError):
        read_report(str(empty))
    no_header = tmp_path / "nohdr.csv"
    no_header.write_text("foo,bar\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError):
        read_report(str(no_header))
    with pytest.raises(ValueError):
        read_report(str(tmp_path / "nope.csv"))
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("[1, 2", encoding="utf-8")
    with pytest.raises(ValueError):
        read_report(str(bad_json))


def test_post_dir_for(tmp_path: Path) -> None:
    assert post_dir_for(str(tmp_path), "ch_a", T1, 100) == str(
        tmp_path / "ch_a" / "2023" / "2023-11" / "2023-11-14_100"
    )
    assert post_dir_for(str(tmp_path), "ch_a", None, 7) == str(
        tmp_path / "ch_a" / "1970" / "1970-01" / "1970-01-01_7"
    )


def test_filter_files() -> None:
    f_doc = QueueFile("c", 1, "", T1, 1, "external_url", "u", "a.pdf", "pdf", None, 10, "document", None)
    f_photo = QueueFile("c", 1, "", T1, 2, "external_url", "u", "b.jpg", "jpg", None, 10, "photo", None)
    assert filter_files([f_doc, f_photo], ["document"], None) == [f_doc]
    assert filter_files([f_doc, f_photo], None, ["jpg"]) == [f_photo]
    assert filter_files([f_doc, f_photo], ["document"], ["jpg"]) == []
    assert filter_files([f_doc, f_photo], None, None) == [f_doc, f_photo]


# -- очередь и тиры ---------------------------------------------------------


def test_load_queue_counts(tmp_path: Path) -> None:
    make_fixture(tmp_path)
    files = load_prepared(tmp_path)
    assert len(files) == 6
    assert len(discover_channels(str(tmp_path))) == 2


def test_tiers_and_order(tmp_path: Path) -> None:
    make_fixture(tmp_path)
    files = load_prepared(tmp_path)
    f1 = get_file(files, "ch_a", 1)  # report.pdf, старый дубль
    f2 = get_file(files, "ch_a", 2)  # app_1.0.zip, старая версия
    f3 = get_file(files, "ch_b", 1)  # report.pdf, свежий дубль
    f4 = get_file(files, "ch_b", 2)  # app_2.0.zip, средняя версия
    f5 = get_file(files, "ch_b", 3)  # photo.jpg, уникален
    f6 = get_file(files, "ch_b", 4)  # app_2.0.zip, свежая версия

    assert (f1.tier, f2.tier, f3.tier, f4.tier, f5.tier, f6.tier) == (3, 3, 2, 3, 1, 2)
    assert f1.group_size == 2 and f3.group_size == 2
    assert f2.family_size == 3 and f6.family_size == 3
    assert f5.group_size == 1 and f5.family_size == 1

    order = [(f.channel, f.file_id) for f in select_for_download(files)]
    assert order == [
        ("ch_b", 3),  # T1 photo
        ("ch_b", 4),  # T2 app_2.0.zip (по имени раньше report)
        ("ch_b", 1),  # T2 report.pdf
        ("ch_a", 2),  # T3 app_1.0.zip
        ("ch_b", 2),  # T3 app_2.0.zip
        ("ch_a", 1),  # T3 report.pdf
    ]


def test_done_from_downloaded_jsonl(tmp_path: Path) -> None:
    make_fixture(tmp_path)
    post_dir = tmp_path / "ch_a" / "2023" / "2023-11" / "2023-11-14_100"
    post_dir.mkdir(parents=True)
    (post_dir / "downloaded.jsonl").write_text(
        json.dumps({"file_id": 1, "status": "success"}) + "\n", encoding="utf-8"
    )
    files = load_prepared(tmp_path)
    assert get_file(files, "ch_a", 1).done
    selected = select_for_download(files)
    assert not any(f.channel == "ch_a" and f.file_id == 1 for f in selected)


def test_done_by_existing_target_file(tmp_path: Path) -> None:
    make_fixture(tmp_path)
    post_dir = tmp_path / "ch_a" / "2023" / "2023-11" / "2023-11-14_100"
    post_dir.mkdir(parents=True)
    (post_dir / "report.pdf").write_bytes(b"x" * 1000)
    files = load_prepared(tmp_path)
    assert get_file(files, "ch_a", 1).done


def test_on_disk_excluded_by_default(tmp_path: Path) -> None:
    make_fixture(tmp_path)
    other_post = tmp_path / "ch_a" / "2023" / "2023-11" / "2023-11-14_100"
    other_post.mkdir(parents=True)
    (other_post / "photo.jpg").write_bytes(b"x" * 500)
    files = load_prepared(tmp_path)
    f5 = get_file(files, "ch_b", 3)
    assert f5.on_disk and f5.tier == 3
    assert f5 not in select_for_download(files)
    assert f5 in select_for_download(files, force_on_disk=True)


def test_target_path_uniqueness(tmp_path: Path) -> None:
    (tmp_path / "ch_x").mkdir(parents=True)
    (tmp_path / "ch_x" / "queue.jsonl").write_text(
        json.dumps(
            _post(
                5,
                "ch_x",
                T1,
                [
                    _file(1, "dup.pdf", 10, "document"),
                    _file(2, "dup.pdf", 10, "document"),
                    _file(3, "dup.pdf", 10, "document"),
                ],
            )
        )
        + "\n",
        encoding="utf-8",
    )
    files = load_prepared(tmp_path)
    targets = sorted(Path(f.target_path).name for f in files if f.target_path)
    assert targets == ["dup.pdf", "dup_1.pdf", "dup_2.pdf"]
