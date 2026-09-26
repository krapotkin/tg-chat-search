"""Тесты TelegramSpider.parse_channel: логика пагинации и остановки.

Покрытый риск: условия остановки обхода истории. Ошибка здесь либо роняет
раннюю остановку resume (лишние запросы), либо зацикливает (бесконечный
обход), либо пропускает посты. `_fetch_page` замещён — сеть не используется.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable

from lxml import etree

from chat_search.scripts.download_channels import TelegramSpider

CHANNEL = "testchan"
URL = f"https://t.me/s/{CHANNEL}"


def make_post(post_id: int, kind: str = "text", channel: str = CHANNEL):
    """Собрать lxml-элемент поста для фейковой страницы."""
    if kind == "text":
        html = (
            f'<div class="tgme_widget_message" data-post="{channel}/{post_id}">'
            '<div class="tgme_widget_message_bubble">'
            f'<div class="tgme_widget_message_text">текст {post_id}</div>'
            '<time class="time" datetime="2024-01-01T00:00:00+00:00">1 янв</time>'
            "</div></div>"
        )
    elif kind == "repost":
        html = (
            f'<div class="tgme_widget_message" data-post="{channel}/{post_id}">'
            '<div class="tgme_widget_message_bubble">'
            f'<div class="tgme_widget_message_text">репост {post_id}</div>'
            '<a class="tgme_widget_message_forwarded_from_name">источник</a>'
            '<time class="time" datetime="2024-01-01T00:00:00+00:00">1 янв</time>'
            "</div></div>"
        )
    elif kind == "media":
        html = (
            f'<div class="tgme_widget_message" data-post="{channel}/{post_id}">'
            '<div class="tgme_widget_message_bubble">'
            '<div class="tgme_widget_message_photo_wrap"></div>'
            '<time class="time" datetime="2024-01-01T00:00:00+00:00">1 янв</time>'
            "</div></div>"
        )
    else:
        raise ValueError(f"неизвестный kind: {kind}")
    return etree.fromstring(html.encode("utf-8"))


def make_post_with_text(post_id: int, text: str, channel: str = CHANNEL):
    """Собрать lxml-элемент текстового поста с заданным текстом."""
    html = (
        f'<div class="tgme_widget_message" data-post="{channel}/{post_id}">'
        '<div class="tgme_widget_message_bubble">'
        f'<div class="tgme_widget_message_text">{text}</div>'
        '<time class="time" datetime="2024-01-01T00:00:00+00:00">1 янв</time>'
        "</div></div>"
    )
    return etree.fromstring(html.encode("utf-8"))


def page(*posts) -> list:
    """Собрать страницу из постов."""
    return list(posts)


def make_fetch(pages: list[list]):
    """Заместитель _fetch_page: возвращает страницы по порядку, потом None."""
    state = {"n": 0}

    def _fetch(url: str):
        idx = state["n"]
        state["n"] += 1
        return pages[idx] if idx < len(pages) else None

    return _fetch, state


def _run(spider: TelegramSpider, fetch, existing: set[int], full_scan: bool = False,
         max_pages: int | None = None) -> list[dict]:
    spider._fetch_page = fetch  # type: ignore[method-assign]
    return spider.parse_channel(URL, existing, max_pages, full_scan)


def test_stops_at_first_post(make_spider: Callable) -> None:
    """Остановка на первом посте канала (id <= 1)."""
    spider = make_spider()
    fetch, state = make_fetch([
        page(make_post(5), make_post(4), make_post(3)),
        page(make_post(2), make_post(1)),
        page(make_post(0)),  # не должна быть запрошена
    ])
    records = _run(spider, fetch, existing=set())
    # посты 2 и 1 пропускаются (id <= 2), скачаны 5, 4, 3
    assert sorted(r["post_id"] for r in records) == [3, 4, 5]
    assert state["n"] == 2, "должна остановиться после достижения id <= 1"


def test_no_progress_loop_protection(make_spider: Callable) -> None:
    """Защита от зацикливания: страница без прогресса (min не уменьшился)."""
    spider = make_spider()
    fetch, state = make_fetch([
        page(make_post(10), make_post(9), make_post(8)),  # min 8
        page(make_post(8), make_post(7)),                 # min 7 < 8 — прогресс
        page(make_post(7), make_post(6)),                 # min 6 < 7 — прогресс
        page(make_post(6)),                               # min 6 >= 6 — без прогресса
    ])
    records = _run(spider, fetch, existing=set())
    # остановилось на 4-й странице (без прогресса), не ушло в бесконечность
    assert state["n"] == 4
    assert records, "посты с пройденных страниц должны быть собраны"


def test_max_pages_limit(make_spider: Callable) -> None:
    """Лимит max_pages: не больше N страниц."""
    spider = make_spider()
    fetch, state = make_fetch([
        page(make_post(10), make_post(9), make_post(8)),
        page(make_post(7), make_post(6), make_post(5)),
        page(make_post(4), make_post(3)),  # не должна быть запрошена
    ])
    records = _run(spider, fetch, existing=set(), max_pages=2)
    assert state["n"] == 2
    assert sorted(r["post_id"] for r in records) == [5, 6, 7, 8, 9, 10]


def test_resume_stops_when_history_complete(make_spider: Callable) -> None:
    """Быстрый resume: история полная (min<=3) и страница без новых -> стоп за 1 страницу."""
    spider = make_spider()
    fetch, state = make_fetch([
        page(make_post(5), make_post(4), make_post(3)),  # все уже в файле
        page(make_post(2), make_post(1)),                # не должна быть запрошена
    ])
    records = _run(spider, fetch, existing={3, 4, 5})
    assert records == [], "новых постов нет"
    assert state["n"] == 1, "resume должен остановиться на первой странице"


def test_resume_stops_fast_when_first_text_post_gt_3(make_spider: Callable) -> None:
    """Быстрый resume, когда min post_id > 3 (ранние посты — сервисные/репосты).

    Сценарий: история полная, но min post_id = 4 (посты 1-3 —
    сервисные/репосты/медиа). Ранняя эвристика min<=3 не срабатывала, и resume
    делал полный обход до начала. Теперь resume останавливается на первой
    странице без новых постов (новые посты всегда на самом свежем конце).
    """
    spider = make_spider()
    fetch, state = make_fetch([
        page(make_post(7), make_post(6), make_post(5)),  # все уже в файле
        page(make_post(4)),                               # не должна быть запрошена
        page(make_post(3), make_post(2), make_post(1)),  # не должна быть запрошена
    ])
    records = _run(spider, fetch, existing={4, 5, 6, 7})
    assert records == [], "новых постов нет"
    assert state["n"] == 1, "resume должен остановиться на первой странице, а не идти до начала"


def test_full_scan_does_not_stop_early(make_spider: Callable) -> None:
    """full_scan: не останавливается рано, идёт до начала истории."""
    spider = make_spider()
    fetch, state = make_fetch([
        page(make_post(5), make_post(4), make_post(3)),  # все уже в файле
        page(make_post(2), make_post(1)),                # full_scan идёт дальше
    ])
    records = _run(spider, fetch, existing={3, 4, 5}, full_scan=True)
    assert records == []
    assert state["n"] == 2, "full_scan не должен останавливаться на первой странице"


def test_skips_reposts_and_media_only(make_spider: Callable) -> None:
    """Репосты и медиа-only пропускаются, текстовые скачиваются."""
    spider = make_spider()
    fetch, state = make_fetch([
        page(make_post(10, "text"), make_post(9, "repost"),
             make_post(8, "media"), make_post(7, "text")),
        page(make_post(1)),  # стоп
    ])
    records = _run(spider, fetch, existing=set())
    assert sorted(r["post_id"] for r in records) == [7, 10]


def test_skips_existing_posts(make_spider: Callable) -> None:
    """Уже скачанные post_id не скачиваются повторно."""
    spider = make_spider()
    fetch, state = make_fetch([
        page(make_post(10), make_post(9), make_post(8)),
        page(make_post(1)),  # стоп
    ])
    records = _run(spider, fetch, existing={9})
    assert sorted(r["post_id"] for r in records) == [8, 10], "пост 9 уже в файле"


def test_download_channel_preserves_existing_content(make_spider: Callable) -> None:
    """Повторный прогон не перезаписывает содержимое уже скачанного поста.

    В файле уже есть пост 5 с текстом «оригинал». Страница отдаёт тот же пост 5,
    но с ДРУГИМ текстом, плюс новый пост 7. Ожидаем, что в файле останется
    прежний текст поста 5, а пост 7 добавится.
    """
    spider = make_spider()
    fp = os.path.join(spider.output_dir, "chan.jsonl")
    with open(fp, "w", encoding="utf-8") as f:
        f.write(json.dumps({"post_id": 5, "text": "оригинал"}) + "\n")

    pages = [
        [make_post_with_text(5, "ИЗМЕНЁННЫЙ ТЕКСТ"), make_post_with_text(7, "новый")],
        None,  # дальше страниц нет
    ]
    state = {"n": 0}

    def fake_fetch(url: str):
        idx = state["n"]
        state["n"] += 1
        return pages[idx] if idx < len(pages) else None

    spider._fetch_page = fake_fetch  # type: ignore[method-assign]
    spider.download_channel("chan", full_scan=True)

    with open(fp, encoding="utf-8") as f:
        recs = {json.loads(line)["post_id"]: json.loads(line) for line in f if line.strip()}
    assert recs[5]["text"] == "оригинал", "существующий пост перезаписан"
    assert 7 in recs, "новый пост не добавлен"
