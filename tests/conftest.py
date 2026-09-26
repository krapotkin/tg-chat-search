"""Общие фикстуры и пути к test_data для тестов tg_chat_search.

Тесты изолированы от сети: HTML-фикстуры постов лежат в tests/test_data/
(реальные сниппеты t.me, сохранённые одноразово).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from chat_search.scripts.download_channels import TelegramSpider

TEST_DATA_DIR: Path = Path(__file__).parent / "test_data"


@pytest.fixture
def test_data_dir() -> Path:
    """Путь к каталогу с HTML-фикстурами."""
    return TEST_DATA_DIR


@pytest.fixture
def load_post() -> Callable[[str], BeautifulSoup]:
    """Загрузить HTML-фикстуру поста и вернуть BeautifulSoup-элемент."""

    def _load(fixture_name: str) -> BeautifulSoup:
        html = (TEST_DATA_DIR / fixture_name).read_text(encoding="utf-8")
        return BeautifulSoup(html, features="lxml")

    return _load


@pytest.fixture
def make_spider(tmp_path: Path) -> Callable[..., TelegramSpider]:
    """Фабрика TelegramSpider с временной output_dir (без сети)."""

    def _make(channels: list[str] | None = None) -> TelegramSpider:
        return TelegramSpider(channels=channels or [], output_dir=str(tmp_path))

    return _make
