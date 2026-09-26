"""Тесты вспомогательных функций: parse_post_url, to_timestamp."""

from __future__ import annotations

from chat_search.scripts.download_channels import parse_post_url, to_timestamp


def test_parse_post_url_basic() -> None:
    r = parse_post_url("https://t.me/senior_augur/123")
    assert r["post_id"] == 123
    assert r["channel_id"] == "senior_augur"
    assert r["id"] == 123
    assert r["url"] == "https://t.me/senior_augur/123"


def test_parse_post_url_with_query_string() -> None:
    """Query-строка (embed=1 и т.п.) не ломает парсинг."""
    r = parse_post_url("https://t.me/senior_augur/123?embed=1")
    assert r["post_id"] == 123
    assert r["channel_id"] == "senior_augur"


def test_parse_post_url_lowercase_channel() -> None:
    r = parse_post_url("https://t.me/Senior_Augur/5")
    assert r["channel_id"] == "senior_augur"


def test_to_timestamp_utc() -> None:
    assert to_timestamp("2024-01-01T00:00:00+00:00") == 1704067200


def test_to_timestamp_known_value() -> None:
    # 2024-06-15T12:30:00 UTC
    assert to_timestamp("2024-06-15T12:30:00+00:00") == 1718454600
