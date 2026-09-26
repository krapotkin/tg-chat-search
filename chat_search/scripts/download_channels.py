"""
download_channels.py — Скачать сообщения публичных каналов Telegram
через web-скрейпинг (t.me/s/{channel}).

- Не требует авторизации (публичные t.me страницы)
- Пагинация циклом (без рекурсии) с защитой от бесконечного цикла
- Докачка: уже скачанные post_id пропускаются; если история полная,
  останавливаемся на первой странице без новых постов
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

import fire
import html2text
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from lxml import etree
from lxml.html import tostring

from chat_search.config import DEFAULT_CHANNELS_FILE, load_channel_list

logger = logging.getLogger(__name__)


def parse_post_url(url: str) -> dict[str, Any]:
    """Разобрать URL поста: https://t.me/{channel}/{post_id}."""
    url = url.split("?")[0]
    channel_id, post_id = url.split("/")[-2:]
    return {
        "url": url,
        "channel_id": channel_id.lower(),
        "post_id": int(post_id),
        "id": int(post_id),
    }


def to_timestamp(dt_str: str) -> int:
    """Конвертировать datetime атрибут t.me в unix timestamp."""
    dt = datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%S+00:00")
    dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp())


def html2text_setup() -> html2text.HTML2Text:
    instance = html2text.HTML2Text(bodywidth=0)
    instance.ignore_links = True
    instance.ignore_images = True
    instance.ignore_tables = True
    instance.ignore_emphasis = True
    instance.ul_item_mark = ""
    return instance


class TelegramSpider:
    """Скрейпер публичных каналов t.me/s/{channel}."""

    name = "telegram"
    channel_url_template = "https://t.me/s/{}"
    post_url_template = "https://t.me/{}?embed=1"
    HISTORY_XPATH = (
        "//body/main/div/section[contains(@class, 'tgme_channel_history')]/div"
    )

    def __init__(
        self,
        channels: list[str],
        output_dir: str,
    ) -> None:
        self.channels = channels
        self.output_dir = output_dir
        self.html2text = html2text_setup()
        os.makedirs(output_dir, exist_ok=True)

    def __call__(self) -> None:
        """Скачать все каналы."""
        for channel in self.channels:
            self.download_channel(channel)

    def download_channel(
        self,
        channel_name: str,
        max_pages: int | None = None,
        full_scan: bool = False,
    ) -> None:
        """Скачать (докачать) сообщения конкретного канала."""
        logger.info("=" * 60)
        logger.info("Обработка канала: %s", channel_name)

        output_file = os.path.join(self.output_dir, f"{channel_name}.jsonl")
        existing_post_ids = self._load_existing_post_ids(output_file)

        url = self.channel_url_template.format(channel_name)
        new_records = self.parse_channel(url, existing_post_ids, max_pages, full_scan)

        # Всегда переписываем файл отсортированным по post_id (даже если новых
        # записей нет — приводим в порядок ранее неупорядоченный файл).
        self._save_records(output_file, new_records)
        if new_records:
            logger.info("Добавлено %d новых сообщений в %s", len(new_records), output_file)
        else:
            logger.info("Нет новых сообщений для канала %s", channel_name)

    def _load_existing_post_ids(self, file_path: str) -> set[int]:
        """Загрузить уже скачанные post_id из файла."""
        existing_post_ids: set[int] = set()

        if os.path.exists(file_path):
            try:
                with open(file_path, encoding="utf-8") as f:
                    for line in f:
                        try:
                            record = json.loads(line.strip())
                            existing_post_ids.add(record["post_id"])
                        except (json.JSONDecodeError, KeyError):
                            continue
                logger.info("Найдено %d уже скачанных сообщений", len(existing_post_ids))
            except Exception as e:
                logger.error("Ошибка чтения %s: %s", file_path, e)

        return existing_post_ids

    def _save_records(self, file_path: str, records: list[dict[str, Any]]) -> None:
        """
        Сохранить записи: объединить с существующими, дедуплицировать по post_id,
        отсортировать по post_id (возрастанию) и переписать файл.

        Файл всегда остаётся отсортированным по post_id — даже если новых
        записей нет (приводит в порядок ранее неупорядоченный файл).
        """
        by_id: dict[int, dict[str, Any]] = {}
        if os.path.exists(file_path):
            with open(file_path, encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                        by_id[rec["post_id"]] = rec
                    except (json.JSONDecodeError, KeyError):
                        continue
        for rec in records:
            # Существующая запись (из файла) не перезаписывается: при совпадении
            # post_id сохраняем уже скачанную версию, новые post_id добавляются.
            by_id.setdefault(rec["post_id"], rec)
        with open(file_path, "w", encoding="utf-8") as w:
            for post_id in sorted(by_id):
                w.write(json.dumps(by_id[post_id], ensure_ascii=False) + "\n")

    def _fetch_page(self, url: str) -> list[Any] | None:
        """
        Загрузить одну страницу t.me/s и вернуть список элементов постов.

        Возвращает None, если страница не загрузилась.
        """
        try:
            with requests.Session() as session:
                headers = {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    )
                }

                response = session.get(url, headers=headers, timeout=30)
                response.raise_for_status()
                doc = response.content
                response_page = etree.HTML(doc)
                posts = response_page.xpath(self.HISTORY_XPATH + "/div")

                if not posts:
                    logger.warning("  Посты не найдены на странице")
                    return None

                logger.info("  Получено %d постов на странице", len(posts))
                return list(posts)

        except requests.exceptions.Timeout:
            logger.warning("  Таймаут (30с)")
        except requests.exceptions.RequestException as e:
            logger.warning("  Ошибка запроса: %s", e)
        except Exception as e:
            logger.warning("  Неизвестная ошибка: %s", e)

        logger.error("Не удалось загрузить страницу: %s", url)
        return None

    def parse_channel(
        self,
        url: str,
        existing_post_ids: set[int],
        max_pages: int | None = None,
        full_scan: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Пройти историю канала от новых постов к старым и вернуть новые записи.

        Станции остановки:
        - страница не загрузилась
        - постов на странице нет
        - достигнут первый пост (id <= 1)
        - нет прогресса (защита от зацикливания)
        - resume (не full_scan) для уже скачанного канала: страница без новых
          постов (новые посты всегда на самом свежем конце); для нового канала
          (пустой файл) и для full_scan — идём до самого начала
        - достигнут max_pages (если задан)
        """
        channel_name = url.split("/")[-1].split("?")[0]

        records: list[dict[str, Any]] = []
        before: int | None = None
        pages = 0

        # Resume (full_scan=False) для уже скачанного канала: новые посты всегда
        # на самом свежем конце, поэтому останавливаемся на первой странице без
        # новых постов. Для нового канала (файл пуст) и для full_scan ранняя
        # остановка отключена — проходим всю историю (для восстановления
        # постов, пропущенных при предыдущих скачиваниях).
        has_existing = bool(existing_post_ids)

        while True:
            page_url = url if before is None else f"{url}?before={before}"
            posts = self._fetch_page(page_url)
            if not posts:
                break

            page_post_ids: list[int] = []
            new_on_page = 0

            for post in posts:
                post_path_attr = post.xpath("@data-post")
                if not post_path_attr:
                    continue
                post_path_attr = post_path_attr[0]

                try:
                    post_id = int(post_path_attr.split("/")[-1])
                except ValueError:
                    continue
                page_post_ids.append(post_id)

                if post_id in existing_post_ids:
                    continue
                if post_id <= 2:
                    continue

                post_soup = BeautifulSoup(tostring(post), features="lxml")
                post_url = self.post_url_template.format(post_path_attr)
                try:
                    item = self._parse_post(post_soup, post_url)
                except Exception as e:
                    logger.error("Ошибка парсинга %s: %s", post_url, e)
                    continue

                if item is None:
                    continue
                item["source"] = channel_name
                records.append(item)
                new_on_page += 1

            if not page_post_ids:
                break

            page_min = min(page_post_ids)

            if not full_scan and has_existing and new_on_page == 0:
                logger.info("Новых постов нет — остановка (resume)")
                break
            if page_min <= 1:
                logger.info("Достигнут первый пост канала — остановка")
                break
            if before is not None and page_min >= before:
                logger.warning(
                    "Нет прогресса (before=%s, page_min=%s) — остановка", before, page_min
                )
                break

            before = page_min
            pages += 1
            if max_pages is not None and pages >= max_pages:
                logger.info("Достигнут лимит страниц (%d) — остановка", max_pages)
                break

        return records

    def _parse_post(self, post_element: Any, post_url: str) -> dict[str, Any] | None:
        """Разобрать один пост. None — если поста нет текста (только медиа) или это репост."""
        time_path = "time.time"
        reply_path = "a.tgme_widget_message_reply"
        forward_path = "a.tgme_widget_message_forwarded_from_name"

        item = parse_post_url(post_url)
        # Текст поста: ищем div.tgme_widget_message_text где угодно внутри поста.
        # Он может быть прямым ребёнком bubble, или вложен в контейнер медиа
        # (media_supported_cont > tgme_widget_message_one_media > tgme_widget_message_text)
        # — жёсткие селекторы по уровню вложенности часть таких постов теряли.
        text_element = post_element.select_one("div.tgme_widget_message_text")

        if not text_element:
            return None  # только медиа

        item["text"] = self._parse_html(str(text_element))
        item["links"] = [link.get("href") for link in text_element.select("a")]
        item["fetch_time"] = int(datetime.now().replace(tzinfo=UTC).timestamp())

        time_element = post_element.select_one(time_path)
        if time_element:
            item["pub_time"] = to_timestamp(time_element.get("datetime"))

        reply_element = post_element.select_one(reply_path)
        if reply_element:
            item["reply_to"] = reply_element.get("href")
            item["reply_to_message_id"] = item["reply_to"].split("/")[-1]

        forward_element = post_element.select_one(forward_path)
        if forward_element:
            return None  # пропускаем репосты

        return item

    def _parse_html(self, html: str) -> str:
        """Конвертировать HTML поста в чистый текст."""
        text = self.html2text.handle(html)
        sentences = [s.strip() for s in text.strip().split("\n") if s.strip()]
        for i, sentence in enumerate(sentences):
            if sentence and sentence[-1].isalpha():
                sentences[i] = sentence + "."
        return "\n".join(sentences)


def download_channels(
    output_dir: str,
    channels_file: str = DEFAULT_CHANNELS_FILE,
    max_pages: int | None = None,
    full_scan: bool = False,
) -> None:
    """
    Скачать сообщения из публичных каналов Telegram (web scraping).

    Args:
        output_dir: Директория для сохранения файлов (JSONL)
        channels_file: YAML-конфиг со списком каналов
            (по умолчанию configs/channels.yaml)
        max_pages: Ограничить число страниц на канал (для тестов/лимитов)
        full_scan: Пройти всю историю до начала (по умолчанию — быстрый resume,
            останавливается когда история полная и новых постов нет)
    """
    channels = load_channel_list(channels_file)

    spider = TelegramSpider(
        channels=channels,
        output_dir=output_dir,
    )

    for channel in channels:
        spider.download_channel(channel, max_pages=max_pages, full_scan=full_scan)


def main(
    output_dir: str,
    channels_file: str = DEFAULT_CHANNELS_FILE,
    max_pages: int | None = None,
    full_scan: bool = False,
) -> None:
    """Entry point — загружает .env и запускает download_channels."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )
    load_dotenv()
    download_channels(output_dir, channels_file, max_pages, full_scan)


if __name__ == "__main__":
    fire.Fire(main)
