"""
download_cli.py — Консольный клиент очереди вложений (часть 2), без GUI.

Читает очередь, собранную parse_attachments.py (часть 1), через общие
модули chat_search.queue:
1. `list` — список файлов (по умолчанию НЕ скачанные и НЕ мёртвые,
   `--show_all` — все, dead со статусом [dead]), сводка по типам + таблица
   в порядке приоритетов T1 -> T3, `--json PATH` — полный отчёт в JSON,
   `--csv PATH` — тот же отчёт в CSV/TSV (табуляция для .tsv, запятая
   иначе) для просмотра. Без сети и без сессии Telegram.
2. `download` — скачивание файлов выбранных типов (--kinds, --ext)
   в порядке приоритетов (T1 уникальные, T2 свежайший из дублей/версий,
   T3 остальные; внутри тира: kind -> имя -> дата); `--list PATH` —
   только файлы из сохранённого отчёта (`list --json PATH` или
   `list --csv PATH`; JSON/CSV/TSV определяются автоматически);
   `--include_dead` — включить мёртвые файлы (по умолчанию пропускаются).
3. `post` — все файлы поста; спецификация — `channel/100` либо URL поста.
4. `check` — проверка доступности внешних URL: 404/410 — пометить файл
   мёртвым (исключить из списка/скачивания по умолчанию); `--recheck` —
   повторно проверить помеченные (снять пометку, если снова доступны).
   Без сессии Telegram.

Файл пропускается, если в downloaded.jsonl есть status=success или целевой
файл существует (resume); файл, уже лежащий на диске в другом канале
(то же имя+размер), пропускается по умолчанию (--force_on_disk —
принудительно). telegram_media — Telethon, external_url — HTTP GET.
Telethon-клиент создаётся лениво, только если в выборке есть
telegram_media.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import fire
import requests
from dotenv import load_dotenv

from chat_search import tg_client
from chat_search.queue import (
    DownloadQueueEngine,
    QueueFile,
    build_summary,
    check_urls,
    filter_files,
    format_details,
    format_row,
    format_summary,
    load_dead,
    load_queue,
    mark_dead,
    parse_csv,
    prepare_queue,
    read_report,
    report_file_ids,
    scan_disk,
    select_for_download,
    sort_key,
    unmark_dead,
    write_csv_report,
    write_json_report,
)
from chat_search.scripts.download_channels import parse_post_url

logger = logging.getLogger(__name__)


def parse_post_spec(spec: str) -> tuple[str, int]:
    """Спецификация поста: `channel/100` либо URL https://t.me/channel/100."""
    if "://" in spec:
        info = parse_post_url(spec)
        return str(info["channel_id"]), int(info["post_id"])
    channel, sep, post_id = spec.rpartition("/")
    if not sep or not post_id.isdigit():
        msg = f"Некорректная спецификация поста: {spec!r} (ожидается channel/100 или URL)"
        raise ValueError(msg)
    return channel, int(post_id)


def run_list(
    attachments_dir: str,
    channels: list[str] | None = None,
    kinds: list[str] | None = None,
    exts: list[str] | None = None,
    json_path: str | None = None,
    csv_path: str | None = None,
    show_all: bool = False,
    details: bool = False,
) -> None:
    """
    Список файлов: сводка по типам + таблица в порядке приоритетов.

    По умолчанию — только НЕ скачанные и НЕ мёртвые (status != done и
    != dead, включая on_disk и error — со статусом в строке); show_all — все
    файлы со статусом (dead — со статусом [dead]).
    json_path/csv_path — полный отчёт в файл (JSON / CSV-TSV).
    """
    files = load_queue(attachments_dir, channels)
    disk = scan_disk(attachments_dir)
    dead = load_dead(attachments_dir, channels)
    prepare_queue(attachments_dir, files, disk, dead=dead)
    files = filter_files(files, kinds, exts)
    if not show_all:
        files = [f for f in files if not f.done and not f.dead]
    files = sorted(files, key=sort_key)
    if json_path:
        write_json_report(json_path, files)
        print(f"Отчёт: {json_path} ({len(files)} файлов)")
    if csv_path:
        write_csv_report(csv_path, files)
        print(f"Отчёт: {csv_path} ({len(files)} файлов)")
    if details:
        for f in files:
            print(format_details(f))
            print()
        return
    print(format_summary(build_summary(files)))
    print()
    if files:
        print("В порядке приоритетов (T1 -> T3):")
        for f in files:
            print(format_row(f))
    else:
        print("Файлов нет.")


def run_download(
    attachments_dir: str,
    channels: list[str] | None = None,
    kinds: list[str] | None = None,
    exts: list[str] | None = None,
    limit: int | None = None,
    concurrency: int = 4,
    force_on_disk: bool = False,
    file_list: str | None = None,
    include_dead: bool = False,
) -> None:
    """
    Скачать файлы выбранных типов в порядке приоритетов T1 -> T2 -> T3.

    file_list — отчёт (`list --json PATH` или `list --csv PATH`, JSON/CSV/TSV):
    скачать только файлы из него (тиры/дубли считаются по всей очереди,
    resume работает как обычно). include_dead — включить мёртвые файлы
    (по умолчанию пропускаются).
    """
    files = load_queue(attachments_dir, channels)
    disk = scan_disk(attachments_dir)
    dead = load_dead(attachments_dir, channels)
    prepare_queue(attachments_dir, files, disk, dead=dead)
    if file_list is not None:
        wanted = report_file_ids(read_report(file_list))
        missing = len(wanted - {(f.channel, f.file_id) for f in files})
        files = [f for f in files if (f.channel, f.file_id) in wanted]
        if missing:
            print(f"Внимание: {missing} файлов из {file_list} не найдено в очереди (пропущено)")
    selected = select_for_download(
        filter_files(files, kinds, exts), force_on_disk, include_dead=include_dead
    )
    if limit is not None:
        selected = selected[:limit]
    if not selected:
        print("Нечего скачивать.")
        return
    print(format_summary(build_summary(selected)))
    _download(selected, concurrency, attachments_dir)


def run_post(
    attachments_dir: str,
    spec: str,
    concurrency: int = 4,
    force_on_disk: bool = False,
    include_dead: bool = False,
) -> None:
    """
    Все файлы поста (spec: channel/100 или URL поста), с обычным resume.
    include_dead — включить мёртвые файлы (по умолчанию пропускаются).
    """
    channel, post_id = parse_post_spec(spec)
    files = load_queue(attachments_dir, [channel])
    post_files = [f for f in files if f.post_id == post_id]
    if not post_files:
        print(f"Пост {channel}/{post_id} не найден в очереди.")
        raise SystemExit(1)
    disk = scan_disk(attachments_dir)
    dead = load_dead(attachments_dir, [channel])
    prepare_queue(attachments_dir, files, disk, dead=dead)
    selected = select_for_download(post_files, force_on_disk, include_dead=include_dead)
    if not selected:
        print("В посте нечего скачивать (все done/on_disk/dead).")
        return
    print(format_summary(build_summary(selected)))
    _download(selected, concurrency, attachments_dir)


def run_check(
    attachments_dir: str,
    channels: list[str] | None = None,
    kinds: list[str] | None = None,
    exts: list[str] | None = None,
    recheck: bool = False,
    concurrency: int = 8,
    http_session: requests.Session | None = None,
) -> None:
    """
    Проверить доступность внешних URL: 404/410 — пометить файл мёртвым.

    Обычный режим — проверяются files, не скачанные и не помеченные
    (external_url). --recheck — повторно проверяются помеченные: снова
    доступен — снять пометку. Telegram-медиа не проверяются (авто-пометка
    движком при удалённом сообщении); TG-мёртвые пропускаются со счётчиком.
    """
    files = load_queue(attachments_dir, channels)
    disk = scan_disk(attachments_dir)
    dead = load_dead(attachments_dir, channels)
    prepare_queue(attachments_dir, files, disk, dead=dead)
    candidates = [
        f
        for f in filter_files(files, kinds, exts)
        if f.file_type == "external_url"
        and f.url is not None
        and (f.dead if recheck else not f.dead and not f.done)
    ]
    tg_skipped = 0
    if recheck:
        kept: list[QueueFile] = []
        for f in candidates:
            if f.dead_reason == "message_not_found":
                tg_skipped += 1
                continue
            kept.append(f)
        candidates = kept
    if not candidates:
        if recheck and tg_skipped:
            print(f"Проверять нечего; пропущено TG {tg_skipped} (не через HTTP).")
        else:
            print("Проверять нечего.")
        return
    print(f"Проверяю {len(candidates)} URL, concurrency={concurrency} ...")
    results = asyncio.run(check_urls(candidates, concurrency=concurrency, session=http_session))
    marked = 0
    restored = 0
    still_dead = 0
    other = 0
    for f, result in results:
        if recheck:
            if result == "ok":
                unmark_dead(attachments_dir, f.channel, f.file_id)
                restored += 1
                print(f"  [ok]    {f.channel}/{f.post_id} {f.filename} — снова доступен")
            elif result in ("http_404", "http_410"):
                mark_dead(
                    attachments_dir, f.channel, f.file_id, result, url=f.url, filename=f.filename
                )
                still_dead += 1
                print(f"  [dead]  {f.channel}/{f.post_id} {f.filename}: {result}")
            else:
                other += 1
                print(f"  [?]     {f.channel}/{f.post_id} {f.filename}: {result}")
        else:
            if result in ("http_404", "http_410"):
                mark_dead(
                    attachments_dir, f.channel, f.file_id, result, url=f.url, filename=f.filename
                )
                marked += 1
                print(f"  [dead]  {f.channel}/{f.post_id} {f.filename}: {result}")
            elif result != "ok":
                other += 1
                print(f"  [?]     {f.channel}/{f.post_id} {f.filename}: {result}")
    if recheck:
        print(
            f"Проверено {len(results)}; снова доступно {restored}; "
            f"всё ещё мёртвых {still_dead}; прочих/ошибок {other}; пропущено TG {tg_skipped}"
        )
    else:
        print(f"Проверено {len(results)}; помечено мёртвыми {marked}; прочих/ошибок {other}")


def _cli_event_callback(event: str, f: QueueFile, ok: bool, error: str | None) -> None:
    if event != "finished":
        return
    if ok:
        print(f"  [ok]    {f.channel}/{f.post_id} {f.filename}")
    else:
        print(f"  [error] {f.channel}/{f.post_id} {f.filename}: {error}")


async def _cli_download(engine: DownloadQueueEngine, files: list[QueueFile]) -> None:
    await engine.submit(files)
    await engine.run(finish_on_empty=True)


def _download(files: list[QueueFile], concurrency: int, attachments_dir: str) -> None:
    """Общий хвост скачивания: ленивый Telegram-клиент + запуск движка."""
    print(f"\nСкачиваю {len(files)} файлов, concurrency={concurrency} ...")
    client: Any = None
    if any(f.file_type == "telegram_media" for f in files):
        api_id, api_hash = tg_client.get_credentials()
        client = tg_client.make_client(api_id, api_hash)
    engine = DownloadQueueEngine(
        client,
        concurrency=concurrency,
        event_callback=_cli_event_callback,
        attachments_dir=attachments_dir,
    )
    asyncio.run(_cli_download(engine, files))
    print(f"Готово: {engine.stats.done}, ошибок: {engine.stats.errors}")


class DownloadCLI:
    """CLI: list / download / post. Помощь: `download_cli.py <subcommand> --help`."""

    def list(
        self,
        attachments_dir: str,
        channels: str | None = None,
        kinds: str | None = None,
        ext: str | None = None,
        json: str | None = None,
        csv: str | None = None,
        show_all: bool = False,
        details: bool = False,
    ) -> None:
        """Список файлов (по умолчанию — не скачанные): сводка + таблица.

        json — путь к JSON-отчёту; csv — путь к CSV/TSV-отчёту (табуляция
        для .tsv, запятая иначе).
        """
        run_list(
            attachments_dir,
            parse_csv(channels),
            parse_csv(kinds),
            parse_csv(ext),
            json_path=json,
            csv_path=csv,
            show_all=show_all,
            details=details,
        )

    def download(
        self,
        attachments_dir: str,
        channels: str | None = None,
        kinds: str | None = None,
        ext: str | None = None,
        limit: int | None = None,
        concurrency: int = 4,
        force_on_disk: bool = False,
        list: str | None = None,
        include_dead: bool = False,
    ) -> None:
        """Скачать файлы выбранных типов (в порядке приоритетов).

        list — отчёт `list --json PATH` / `list --csv PATH` (JSON/CSV/TSV):
        скачать только файлы из него. include_dead — включить мёртвые файлы
        (по умолчанию пропускаются).
        """
        run_download(
            attachments_dir,
            parse_csv(channels),
            parse_csv(kinds),
            parse_csv(ext),
            limit=limit,
            concurrency=concurrency,
            force_on_disk=force_on_disk,
            file_list=list,
            include_dead=include_dead,
        )

    def post(
        self,
        attachments_dir: str,
        spec: str,
        concurrency: int = 4,
        force_on_disk: bool = False,
        include_dead: bool = False,
    ) -> None:
        """Скачать все файлы поста (spec: channel/100 или URL поста)."""
        try:
            run_post(
                attachments_dir,
                spec,
                concurrency=concurrency,
                force_on_disk=force_on_disk,
                include_dead=include_dead,
            )
        except ValueError as e:
            print(f"Ошибка: {e}")
            raise SystemExit(1) from None

    def check(
        self,
        attachments_dir: str,
        channels: str | None = None,
        kinds: str | None = None,
        ext: str | None = None,
        recheck: bool = False,
        concurrency: int = 8,
    ) -> None:
        """Проверить доступность внешних URL: 404/410 — пометить мёртвым.

        recheck — повторно проверить помеченные (снять пометку, если снова
        доступны). Telegram-медиа не проверяются.
        """
        run_check(
            attachments_dir,
            parse_csv(channels),
            parse_csv(kinds),
            parse_csv(ext),
            recheck=recheck,
            concurrency=concurrency,
        )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    load_dotenv()
    fire.Fire(DownloadCLI)


if __name__ == "__main__":
    main()
