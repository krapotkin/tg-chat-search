"""Тесты TelegramSpider._save_records: merge/dedup/sort, целостность данных.

Покрытый риск: логика сохранения — объединение новых записей с существующими,
дедупликация по post_id, сортировка. Ошибка здесь ломает датасет
(потеря записей, дубли, неупорядоченный файл).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path


def _read_ids(file_path: str) -> list[int]:
    with open(file_path, encoding="utf-8") as f:
        return [json.loads(line)["post_id"] for line in f if line.strip()]


def _write(file_path: str, records: list[dict]) -> None:
    with open(file_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def test_new_records_sorted(make_spider: Callable, tmp_path: Path) -> None:
    """Новые записи пишутся отсортированными по post_id."""
    spider = make_spider()
    fp = str(tmp_path / "ch.jsonl")
    spider._save_records(fp, [{"post_id": 10, "text": "a"}, {"post_id": 5, "text": "b"}])
    assert _read_ids(fp) == [5, 10]


def test_merge_with_unsorted_existing(make_spider: Callable, tmp_path: Path) -> None:
    """Новые записи мержатся с существующим неупорядоченным файлом."""
    spider = make_spider()
    fp = str(tmp_path / "ch.jsonl")
    _write(fp, [{"post_id": 20, "text": "x"}, {"post_id": 10, "text": "y"}])
    spider._save_records(fp, [{"post_id": 15, "text": "z"}])
    assert _read_ids(fp) == [10, 15, 20]


def test_dedup_existing_version_not_overwritten(make_spider: Callable, tmp_path: Path) -> None:
    """При дубле post_id уже существующая запись НЕ перезаписывается.

    Данные иммутабельны: если пост уже скачан, его содержимое сохраняется,
    повторная версия не затирает прежнюю (защита от потери/порчи данных).
    """
    spider = make_spider()
    fp = str(tmp_path / "ch.jsonl")
    _write(fp, [{"post_id": 5, "text": "existing"}])
    spider._save_records(fp, [{"post_id": 5, "text": "new"}])
    with open(fp, encoding="utf-8") as f:
        recs = [json.loads(line) for line in f if line.strip()]
    assert len(recs) == 1
    assert recs[0]["text"] == "existing", "существующая запись была перезаписана"


def test_no_data_loss(make_spider: Callable, tmp_path: Path) -> None:
    """Ни одна существующая запись не теряется при сохранении."""
    spider = make_spider()
    fp = str(tmp_path / "ch.jsonl")
    existing = [3, 7, 12, 20]
    _write(fp, [{"post_id": p, "text": f"t{p}"} for p in existing])
    spider._save_records(fp, [{"post_id": 25, "text": "new"}])
    ids = set(_read_ids(fp))
    assert set(existing) <= ids, "существующие post_id не должны теряться"
    assert 25 in ids


def test_empty_new_records_still_sorts(make_spider: Callable, tmp_path: Path) -> None:
    """Даже без новых записей файл приводится в порядок (сортируется)."""
    spider = make_spider()
    fp = str(tmp_path / "ch.jsonl")
    _write(fp, [{"post_id": 30, "text": "a"}, {"post_id": 10, "text": "b"}])
    spider._save_records(fp, [])
    assert _read_ids(fp) == [10, 30]


def test_corrupt_lines_ignored(make_spider: Callable, tmp_path: Path) -> None:
    """Битые строки (не-JSON) не роняют сохранение, валидные сохраняются."""
    spider = make_spider()
    fp = str(tmp_path / "ch.jsonl")
    with open(fp, "w", encoding="utf-8") as f:
        f.write(json.dumps({"post_id": 10, "text": "ok"}) + "\n")
        f.write("это не json\n")
        f.write(json.dumps({"post_id": 5, "text": "ok"}) + "\n")
    spider._save_records(fp, [{"post_id": 7, "text": "new"}])
    assert _read_ids(fp) == [5, 7, 10]
