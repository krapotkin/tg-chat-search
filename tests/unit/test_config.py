"""Тесты chat_search/config.py: загрузка YAML-конфигов (load_channel_list, load_chats)."""

from __future__ import annotations

from typing import Any

import pytest

from chat_search.config import load_channel_list, load_chats

# --- load_channel_list -------------------------------------------------------


def test_load_channel_list_valid(tmp_path: Any) -> None:
    path = tmp_path / "channels.yaml"
    path.write_text("# комментарий\n- chan1\n- chan2\n", encoding="utf-8")
    assert load_channel_list(str(path)) == ["chan1", "chan2"]


def test_load_channel_list_empty_lines_and_comments(tmp_path: Any) -> None:
    path = tmp_path / "channels.yaml"
    path.write_text("# комментарий\n- chan1\n\n#chan2\n\n", encoding="utf-8")
    assert load_channel_list(str(path)) == ["chan1"]


def test_load_channel_list_dedup_preserves_order(tmp_path: Any) -> None:
    path = tmp_path / "channels.yaml"
    path.write_text("- b\n- a\n- b\n- c\n- a\n", encoding="utf-8")
    assert load_channel_list(str(path)) == ["b", "a", "c"]


def test_load_channel_list_missing_file(tmp_path: Any) -> None:
    with pytest.raises(FileNotFoundError, match="nope.yaml"):
        load_channel_list(str(tmp_path / "nope.yaml"))


def test_load_channel_list_missing_file_example_hint(tmp_path: Any) -> None:
    # .example.yaml рядом — подсказка в ошибке должна его упомянуть
    (tmp_path / "channels.example.yaml").write_text("- a\n", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="channels.example.yaml"):
        load_channel_list(str(tmp_path / "channels.yaml"))


def test_load_channel_list_broken_yaml(tmp_path: Any) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("- a\n  b: [", encoding="utf-8")
    with pytest.raises(ValueError, match="bad.yaml"):
        load_channel_list(str(path))


def test_load_channel_list_not_a_list(tmp_path: Any) -> None:
    path = tmp_path / "dict.yaml"
    path.write_text("a: 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="список"):
        load_channel_list(str(path))


def test_load_channel_list_non_string_item(tmp_path: Any) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("- a\n- 123\n", encoding="utf-8")
    with pytest.raises(ValueError, match="bad.yaml"):
        load_channel_list(str(path))


def test_load_channel_list_empty_file(tmp_path: Any) -> None:
    path = tmp_path / "empty.yaml"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="пуст"):
        load_channel_list(str(path))


# --- load_chats ---------------------------------------------------------------


def test_load_chats_valid(tmp_path: Any) -> None:
    path = tmp_path / "chats.yaml"
    path.write_text("# комментарий\nchat1: -1001\nchat2: -1002\n", encoding="utf-8")
    assert load_chats(str(path)) == {"chat1": -1001, "chat2": -1002}


def test_load_chats_missing_file(tmp_path: Any) -> None:
    with pytest.raises(FileNotFoundError, match="nope.yaml"):
        load_chats(str(tmp_path / "nope.yaml"))


def test_load_chats_missing_file_example_hint(tmp_path: Any) -> None:
    (tmp_path / "chats.example.yaml").write_text("a: -1\n", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="chats.example.yaml"):
        load_chats(str(tmp_path / "chats.yaml"))


def test_load_chats_broken_yaml(tmp_path: Any) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("chat1: [", encoding="utf-8")
    with pytest.raises(ValueError, match="bad.yaml"):
        load_chats(str(path))


def test_load_chats_not_a_dict(tmp_path: Any) -> None:
    path = tmp_path / "list.yaml"
    path.write_text("- a\n", encoding="utf-8")
    with pytest.raises(ValueError, match="маппинг"):
        load_chats(str(path))


def test_load_chats_value_not_int(tmp_path: Any) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("chat1: abc\n", encoding="utf-8")
    with pytest.raises(ValueError, match="chat_id"):
        load_chats(str(path))


def test_load_chats_value_bool_rejected(tmp_path: Any) -> None:
    # bool — подтип int в Python: true/false как chat_id отклоняются
    path = tmp_path / "bad.yaml"
    path.write_text("chat1: true\n", encoding="utf-8")
    with pytest.raises(ValueError, match="chat_id"):
        load_chats(str(path))


def test_load_chats_empty_file(tmp_path: Any) -> None:
    path = tmp_path / "empty.yaml"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="пуст"):
        load_chats(str(path))
