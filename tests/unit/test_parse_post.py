"""Тесты TelegramSpider._parse_post: парсинг HTML поста.

Покрытый риск: реальный баг, при котором посты с текстом, вложенным в
контейнер медиа (media_supported_cont > tgme_widget_message_one_media),
отбрасывались как «только медиа» из-за жёстких CSS-селекторов по уровню
вложенности. Фикстуры — реальные сниппеты t.me.
"""

from __future__ import annotations

from collections.abc import Callable

from bs4 import BeautifulSoup

from chat_search.scripts.download_channels import TelegramSpider


def _spider() -> TelegramSpider:
    return TelegramSpider(channels=[], output_dir="/tmp/tg_test_parse")


def test_text_post_parsed(load_post: Callable[[str], BeautifulSoup]) -> None:
    """Обычный текстовый пост: текст, pub_time, ссылки извлекаются."""
    item = _spider()._parse_post(load_post("post_text.html"), "https://t.me/senior_augur/3")
    assert item is not None
    assert item["post_id"] == 3
    assert item["channel_id"] == "senior_augur"
    assert item["id"] == 3
    assert item["text"].strip() != ""
    assert "pub_time" in item
    assert isinstance(item["links"], list) and len(item["links"]) > 0


def test_media_text_post_one_media_regression(load_post: Callable[[str], BeautifulSoup]) -> None:
    """РЕГРЕССИЯ: пост с медиа+текстом (текст в one_media) не теряется."""
    item = _spider()._parse_post(load_post("post_media_text.html"), "https://t.me/senior_augur/558")
    assert item is not None, "пост с медиа+текстом (one_media) не должен отбрасываться"
    assert item["post_id"] == 558
    assert item["text"].strip() != ""


def test_repost_skipped(load_post: Callable[[str], BeautifulSoup]) -> None:
    """Репост (forwarded) пропускается."""
    item = _spider()._parse_post(load_post("post_repost.html"), "https://t.me/senior_augur/609")
    assert item is None


def test_media_only_skipped(load_post: Callable[[str], BeautifulSoup]) -> None:
    """Пост только с медиа (без текста) пропускается."""
    item = _spider()._parse_post(load_post("post_media_only.html"), "https://t.me/senior_augur/620")
    assert item is None
