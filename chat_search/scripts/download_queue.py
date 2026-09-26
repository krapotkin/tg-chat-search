"""
download_queue.py — Скачиватель очереди вложений (часть 2 из 2): GUI + CLI.

Читает очередь, собранную parse_attachments.py (часть 1): {channel}/queue.jsonl,
и скачивает файлы в папки постов, записывая статусы в {post_dir}/downloaded.jsonl
(контракт — в DATA.md, раздел «Контракт части 2»).

Логика получения данных и скачивания — в общих модулях chat_search.queue
(очередь/диск/приоритеты, движок, отчёты). Приоритеты: T1 уникальные,
T2 свежайший из группы дублей/семейства версий, T3 остальные и уже лежащие
на диске; внутри тира: kind -> имя -> дата (свежие раньше).

- `list` / `download` — тонкие обёртки над командами download_cli.py
  (единая реализация: chat_search.scripts.download_cli);
- `gui` — Textual GUI: дерево файлов по типам, выбор, скачивание; по
  умолчанию скрыты скачанные (done) и мёртвые (dead) — как download_cli list;
  статус-фильтр (s / --status): pending,done,dead,error,disk,down;
  опция «уникальные» (u / --unique): в виде по умолчанию дополнительно
  скрыть файлы, чей дубль (то же имя+размер) уже на диске; статус-фильтр
  имеет приоритет.

telegram_media скачивается через Telethon (по files[].message_id),
external_url — HTTP GET. Файл пропускается, если в downloaded.jsonl есть
status=success или целевой файл существует; файл, уже лежащий на диске
(то же имя+размер в любом канале), пропускается по умолчанию
(--force-on-disk / явно в GUI). Telethon-клиент создаётся лениво, только
если в выборке есть telegram_media.
"""

from __future__ import annotations

import logging
from typing import Any

import fire
from dotenv import load_dotenv
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, ProgressBar, RichLog, Static, Tree
from textual.worker import Worker, WorkerState

from chat_search import tg_client
from chat_search.queue import (
    KIND_ORDER,
    DiskIndex,
    DownloadQueueEngine,
    QueueFile,
    filter_files,
    format_details,
    format_size,
    load_dead,
    load_queue,
    parse_csv,
    prepare_queue,
    probe_size,
    scan_disk,
    select_for_download,
    sort_key,
)
from chat_search.scripts.download_cli import run_download, run_list


def file_status(f: QueueFile) -> str:
    """Статус файла: down/done/dead/error/disk/pending (для фильтра и меток)."""
    if f.downloading:
        return "down"
    if f.done:
        return "done"
    if f.dead:
        return "dead"
    if f.last_error:
        return "error"
    if f.on_disk:
        return "disk"
    return "pending"


def is_downloaded_duplicate(f: QueueFile) -> bool:
    """Подтверждённый дубль уже на диске: то же имя и размер (размер известен)."""
    return f.on_disk and f.size is not None


class DownloadQueueCLI:
    """CLI: list / download / gui. Помощь: `download_queue.py <subcommand> --help`."""

    def list(
        self,
        attachments_dir: str,
        channels: str | None = None,
        kinds: str | None = None,
        ext: str | None = None,
        details: bool = False,
        json: str | None = None,
        csv: str | None = None,
    ) -> None:
        """Список файлов со статусом (общая реализация — download_cli).

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
            show_all=True,
            details=details,
        )

    def download(
        self,
        attachments_dir: str,
        channels: str | None = None,
        kinds: str | None = None,
        ext: str | None = None,
        concurrency: int = 4,
        limit: int | None = None,
        force_on_disk: bool = False,
        list: str | None = None,
    ) -> None:
        """Скачать файлы выбранных типов (в порядке приоритетов).

        list — отчёт `list --json PATH` / `list --csv PATH` (JSON/CSV/TSV):
        скачать только файлы из него.
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
        )

    def gui(
        self,
        attachments_dir: str,
        channels: str | None = None,
        kinds: str | None = None,
        ext: str | None = None,
        status: str | None = None,
        unique: bool = False,
        concurrency: int = 4,
    ) -> None:
        """Textual GUI: дерево файлов по типам, выбор, скачивание.

        status — CSV-список статусов (pending,done,dead,error,disk,down);
        по умолчанию (пусто) done/dead скрыты. unique — вид не скачанных
        без подтверждённых дублей (то же имя+размер уже на диске);
        статус-фильтр имеет приоритет.
        """
        app = DownloadQueueApp(
            attachments_dir,
            parse_csv(channels),
            parse_csv(kinds),
            parse_csv(ext),
            parse_csv(status),
            concurrency,
            unique=unique,
        )
        app.run()


class FilterScreen(ModalScreen[dict[str, str]]):
    """Диалог фильтров: тип, расширение, канал, подстрока имени, статусы.

    Статусы: pending,done,dead,error,disk,down; пусто — по умолчанию
    (done/dead скрыты).
    """

    CSS = """
    FilterScreen {
        align: center middle;
    }
    #filter-box {
        width: 60;
        height: auto;
        border: round $primary;
        padding: 1 2;
    }
    #filter-box Input {
        margin-bottom: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Закрыть"),
    ]

    def __init__(self, current: dict[str, str], focus: str = "kind") -> None:
        super().__init__()
        self._current = dict(current)
        self._focus = focus

    def compose(self) -> ComposeResult:
        with Vertical(id="filter-box"):
            yield Input(
                value=self._current.get("kind", ""),
                placeholder="photo,video,document",
                id="f-kind",
            )
            yield Input(value=self._current.get("ext", ""), placeholder="pdf,zip", id="f-ext")
            yield Input(
                value=self._current.get("channel", ""),
                placeholder="channel_a,channel_b",
                id="f-channel",
            )
            yield Input(value=self._current.get("name", ""), placeholder="подстрока имени", id="f-name")
            yield Input(
                value=self._current.get("status", ""),
                placeholder="pending,done,dead,error,disk",
                id="f-status",
            )
            yield Button("Применить", variant="primary", id="f-apply")

    def on_mount(self) -> None:
        self.query_one(f"#f-{self._focus}", Input).focus()

    def _apply(self) -> None:
        values = {
            "kind": self.query_one("#f-kind", Input).value,
            "ext": self.query_one("#f-ext", Input).value,
            "channel": self.query_one("#f-channel", Input).value,
            "name": self.query_one("#f-name", Input).value,
            "status": self.query_one("#f-status", Input).value,
        }
        self.dismiss(values)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "f-apply":
            self._apply()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._apply()


class DownloadQueueApp(App[None]):
    """GUI: дерево файлов по типам, фильтры, скачивание с приоритетами.

    По умолчанию скрыты done/dead (как download_cli list); показать их —
    статус-фильтром (s / --status): pending,done,dead,error,disk,down.
    Опция «уникальные» (u / unique=True): в виде по умолчанию дополнительно
    скрыть файлы, чей дубль (то же имя+размер) уже на диске; статус-фильтр
    имеет приоритет.
    """

    TITLE = "Очередь вложений"

    CSS = """
    #main {
        height: 1fr;
    }
    #tree {
        width: 55%;
    }
    #details {
        width: 1fr;
        border-left: tall $primary;
        padding: 0 1;
    }
    #progress {
        width: 1fr;
    }
    #progress-label {
        width: auto;
        padding: 0 1;
    }
    #log {
        height: 8;
    }
    """

    BINDINGS = [
        Binding("d", "download_selected", "Файл"),
        Binding("D", "download_group", "Группа"),
        Binding("G", "download_branch", "Ветка"),
        Binding("A", "download_all", "Все"),
        Binding("p", "toggle_pause", "Пауза"),
        Binding("x", "stop_downloads", "Стоп"),
        Binding("f", "filter_kind", "Тип"),
        Binding("e", "filter_ext", "Расширение"),
        Binding("c", "filter_channel", "Канал"),
        Binding("slash", "filter_name", "Имя"),
        Binding("s", "filter_status", "Статус"),
        Binding("u", "toggle_unique", "Уникальные"),
        Binding("r", "rescan", "Диск"),
        Binding("i", "probe", "HEAD-размер"),
        Binding("q", "quit", "Выход"),
    ]

    def __init__(
        self,
        attachments_dir: str,
        channels: list[str] | None = None,
        kinds: list[str] | None = None,
        exts: list[str] | None = None,
        statuses: list[str] | None = None,
        concurrency: int = 4,
        unique: bool = False,
    ) -> None:
        super().__init__()
        self._attachments_dir = attachments_dir
        self._channels = channels
        self._concurrency = concurrency
        self._unique_view = unique
        self._filter_values: dict[str, str] = {
            "kind": ",".join(kinds) if kinds else "",
            "ext": ",".join(exts) if exts else "",
            "channel": ",".join(channels) if channels else "",
            "name": "",
            "status": ",".join(statuses) if statuses else "",
        }
        self._all_files: list[QueueFile] = []
        self._visible: list[QueueFile] = []
        self._by_kind: dict[str, list[QueueFile]] = {}
        self._exact: dict[tuple[str, int], list[QueueFile]] = {}
        self._families: dict[str, list[QueueFile]] = {}
        self._remaining = 0
        self._done = 0
        self._errors = 0
        self._paused = False
        self._engine: DownloadQueueEngine | None = None
        self._file_nodes: dict[int, Any] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main"):
            yield Tree("Загрузка...", id="tree")
            yield Static("", id="details")
        yield ProgressBar(total=1, id="progress")
        yield Static("Осталось: 0", id="progress-label")
        yield RichLog(id="log", wrap=True, markup=False, max_lines=200)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#progress", ProgressBar).update(total=1, progress=0)
        self.run_worker(self._prepare, name="load", thread=True, exclusive=True)

    def _prepare(self) -> list[QueueFile]:
        files = load_queue(self._attachments_dir, self._channels)
        disk = scan_disk(self._attachments_dir)
        dead = load_dead(self._attachments_dir, self._channels)
        prepare_queue(self._attachments_dir, files, disk, dead=dead)
        return files

    def _rescan(self) -> DiskIndex:
        return scan_disk(self._attachments_dir)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        worker = event.worker
        if event.state in (WorkerState.ERROR, WorkerState.CANCELLED):
            self._write_log(f"Ошибка в {worker.name}: {worker.error}")
            return
        if event.state is not WorkerState.SUCCESS:
            return
        result = worker.result
        if result is None:
            return
        if worker.name == "load":
            self._prepared(result)
        elif worker.name == "rescan":
            self._rescanned(result)
        elif worker.name == "probe":
            self._probed(result)

    def _prepared(self, files: list[QueueFile]) -> None:
        self._all_files = files
        self._apply_filters()
        self._rebuild_tree()
        self._refresh_progress()
        self._write_log(f"Загружено {len(files)} файлов из очереди")

    def _rescanned(self, disk: DiskIndex) -> None:
        for f in self._all_files:
            f.done = False
            f.on_disk = False
            if disk.is_done(f.channel, f.file_id, f.target_path, f.size):
                f.done = True
                continue
            path = disk.on_disk_path(f.filename, f.size)
            if path is not None and path != f.target_path:
                f.on_disk = True
                f.tier = max(f.tier, 3)
        self._apply_filters()
        self._rebuild_tree()
        self._refresh_progress()
        self._write_log("Диск пересканирован")

    def _apply_filters(self) -> None:
        kinds = parse_csv(self._filter_values.get("kind"))
        exts = parse_csv(self._filter_values.get("ext"))
        channels = parse_csv(self._filter_values.get("channel"))
        name = (self._filter_values.get("name") or "").strip().lower()
        visible = self._all_files
        if channels:
            wanted = set(channels)
            visible = [f for f in visible if f.channel in wanted]
        if name:
            visible = [f for f in visible if name in f.filename.lower()]
        statuses = parse_csv(self._filter_values.get("status"))
        if statuses:
            wanted = {s.strip().lower() for s in statuses}
            visible = [f for f in visible if file_status(f) in wanted]
        else:
            visible = [
                f
                for f in visible
                if not f.done and not f.dead and (not self._unique_view or not is_downloaded_duplicate(f))
            ]
        self._visible = filter_files(visible, kinds, exts)
        self._by_kind = {}
        self._exact = {}
        self._families = {}
        for f in self._visible:
            self._by_kind.setdefault(f.kind, []).append(f)
            if f.size is not None:
                self._exact.setdefault((f.filename.lower(), f.size), []).append(f)
            if f.family is not None:
                self._families.setdefault(f.family, []).append(f)

    def _rebuild_tree(self) -> None:
        tree = self.query_one("#tree", Tree)
        tree.reset("Файлы", ("root",))
        self._file_nodes.clear()
        for kind in sorted(self._by_kind, key=lambda k: KIND_ORDER.get(k, 99)):
            tree.root.add(f"{kind} ({len(self._by_kind[kind])})", ("kind", kind))

    def on_tree_node_expanded(self, event: Tree.NodeExpanded[Any]) -> None:
        node = event.node
        if node.children:
            return
        data = node.data
        if not isinstance(data, tuple):
            return
        if data[0] == "kind":
            by_ext: dict[str, list[QueueFile]] = {}
            for f in self._by_kind.get(data[1], []):
                by_ext.setdefault(f.ext or "", []).append(f)
            for ext in sorted(by_ext, key=lambda e: (e == "", e)):
                label = f".{ext}" if ext else "(без расширения)"
                node.add(f"{label} ({len(by_ext[ext])})", ("ext", data[1], ext))
        elif data[0] == "ext":
            kind, ext = data[1], data[2]
            by_name: dict[str, list[QueueFile]] = {}
            for f in self._by_kind.get(kind, []):
                if (f.ext or "") == ext:
                    by_name.setdefault(f.filename.lower(), []).append(f)
            for name in sorted(by_name):
                node.add(f"{name} ({len(by_name[name])})", ("name", kind, ext, name))
        elif data[0] == "name":
            kind, ext, name = data[1], data[2], data[3]
            for f in sorted(self._by_kind.get(kind, []), key=sort_key):
                if (f.ext or "") == ext and f.filename.lower() == name:
                    leaf = node.add(self._leaf_label(f), f)
                    self._file_nodes[f.file_id] = leaf

    def _leaf_label(self, f: QueueFile) -> str:
        status = "" if file_status(f) == "pending" else file_status(f)
        prefix = f"T{f.tier} {status}" if status else f"T{f.tier} "
        return f"{prefix} {f.filename}  {f.channel}/{f.post_id}  {format_size(f.size)}"

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[Any]) -> None:
        self._refresh_details(event.node)

    def _refresh_details(self, node: Any) -> None:
        details = self.query_one("#details", Static)
        data = node.data
        if isinstance(data, QueueFile):
            details.update(format_details(data))
        elif isinstance(data, tuple) and data[0] in ("kind", "ext", "name"):
            files = self._branch_files(node)
            total = sum(f.size or 0 for f in files)
            pending = sum(1 for f in files if not f.done)
            details.update(
                f"Ветка: {len(files)} файлов, {format_size(total)}\n"
                f"Не скачано: {pending}\n\n"
                "d - файл, D - группа, G - вся ветка, A - всё видимое, u - уникальные"
            )
        else:
            details.update("")

    def _branch_files(self, node: Any) -> list[QueueFile]:
        """Файлы ветки по данным узла (не зависит от раскрытия дерева)."""
        data = node.data
        if isinstance(data, QueueFile):
            return [data]
        if not isinstance(data, tuple):
            return []
        if data[0] == "kind":
            return list(self._by_kind.get(data[1], []))
        if data[0] == "ext":
            kind, ext = data[1], data[2]
            return [f for f in self._by_kind.get(kind, []) if (f.ext or "") == ext]
        if data[0] == "name":
            kind, ext, name = data[1], data[2], data[3]
            return [
                f for f in self._by_kind.get(kind, []) if (f.ext or "") == ext and f.filename.lower() == name
            ]
        return []

    def _refresh_progress(self) -> None:
        bar = self.query_one("#progress", ProgressBar)
        total = self._remaining + self._done + self._errors
        if total:
            bar.update(total=total, progress=self._done + self._errors)
        label = f"Осталось: {self._remaining}"
        if self._unique_view:
            label += " (уникальные)"
        if self._paused:
            label += " (пауза)"
        self.query_one("#progress-label", Static).update(label)

    def _write_log(self, message: str) -> None:
        self.query_one("#log", RichLog).write(message)

    def _ensure_engine(self) -> DownloadQueueEngine:
        if self._engine is not None:
            return self._engine
        client = None
        if any(f.file_type == "telegram_media" for f in self._visible if not f.done):
            api_id, api_hash = tg_client.get_credentials()
            client = tg_client.make_client(api_id, api_hash)
        engine = DownloadQueueEngine(
            client,
            concurrency=self._concurrency,
            event_callback=self._on_engine_event,
            attachments_dir=self._attachments_dir,
        )
        self._engine = engine
        self.run_worker(
            engine.run(finish_on_empty=False),
            name="engine",
            group="download",
            exclusive=True,
            exit_on_error=False,
        )
        return engine

    async def _submit_async(self, engine: DownloadQueueEngine, files: list[QueueFile]) -> None:
        await engine.submit(files)

    def _submit(self, files: list[QueueFile]) -> None:
        pending = [f for f in files if not f.done and not f.downloading]
        if not pending:
            self.notify("Нечего скачивать", severity="warning")
            return
        engine = self._ensure_engine()
        self._remaining += len(pending)
        self._refresh_progress()
        self.run_worker(self._submit_async(engine, pending), name="submit")
        self._write_log(f"В очередь: {len(pending)} файлов")

    def action_download_selected(self) -> None:
        node = self.query_one("#tree", Tree).cursor_node
        if node is None or not isinstance(node.data, QueueFile):
            self.notify("Выберите файл в дереве", severity="warning")
            return
        self._submit([node.data])

    def action_download_group(self) -> None:
        node = self.query_one("#tree", Tree).cursor_node
        if node is None or not isinstance(node.data, QueueFile):
            self.notify("Выберите файл в дереве", severity="warning")
            return
        f: QueueFile = node.data
        group: list[QueueFile] = []
        if f.size is not None:
            group = self._exact.get((f.filename.lower(), f.size), [])
        if not group and f.family is not None:
            group = self._families.get(f.family, [])
        if not group:
            group = [f]
        self._submit(group)

    def action_download_branch(self) -> None:
        node = self.query_one("#tree", Tree).cursor_node
        if node is None:
            self.notify("Выберите узел в дереве", severity="warning")
            return
        files = self._branch_files(node)
        if not files:
            self.notify("В ветке нет файлов", severity="warning")
            return
        self._submit(files)

    def action_download_all(self) -> None:
        self._submit(select_for_download(self._visible))

    def action_toggle_pause(self) -> None:
        if self._engine is None:
            self.notify("Скачивание не запущено", severity="warning")
            return
        if self._paused:
            self._engine.resume()
            self._paused = False
            self._write_log("Продолжаю")
        else:
            self._engine.pause()
            self._paused = True
            self._write_log("Пауза (активные файлы завершатся)")
        self._refresh_progress()

    def action_stop_downloads(self) -> None:
        if self._engine is None:
            return
        self._engine.stop()
        self._paused = False
        self._write_log("Стоп после завершения активных файлов")

    def action_filter_kind(self) -> None:
        self.push_screen(FilterScreen(self._filter_values, "kind"), self._on_filter)

    def action_filter_ext(self) -> None:
        self.push_screen(FilterScreen(self._filter_values, "ext"), self._on_filter)

    def action_filter_channel(self) -> None:
        self.push_screen(FilterScreen(self._filter_values, "channel"), self._on_filter)

    def action_filter_name(self) -> None:
        self.push_screen(FilterScreen(self._filter_values, "name"), self._on_filter)

    def action_filter_status(self) -> None:
        self.push_screen(FilterScreen(self._filter_values, "status"), self._on_filter)

    def action_toggle_unique(self) -> None:
        self._unique_view = not self._unique_view
        self._apply_filters()
        self._rebuild_tree()
        self._refresh_progress()
        self._write_log(f"Уникальные: {'вкл' if self._unique_view else 'выкл'}")

    def _on_filter(self, values: dict[str, str] | None) -> None:
        if values is None:
            return
        self._filter_values.update(values)
        self._apply_filters()
        self._rebuild_tree()
        self._refresh_progress()

    def action_rescan(self) -> None:
        self._write_log("Пересканирование диска...")
        self.run_worker(self._rescan, name="rescan", thread=True, exclusive=True)

    def action_probe(self) -> None:
        node = self.query_one("#tree", Tree).cursor_node
        if node is None or not isinstance(node.data, QueueFile):
            self.notify("Выберите файл в дереве", severity="warning")
            return
        f: QueueFile = node.data
        if f.file_type != "external_url" or not f.url:
            self.notify("HEAD-размер доступен только для внешних URL", severity="warning")
            return
        self._write_log(f"HEAD {f.url} ...")
        self.run_worker(lambda: self._probe(f), name="probe", thread=True)

    def _probe(self, f: QueueFile) -> tuple[int, int | None]:
        size = probe_size(f.url) if f.url else None
        f.probed_size = size
        return f.file_id, size

    def _probed(self, result: tuple[int, int | None]) -> None:
        file_id, size = result
        f = next((x for x in self._all_files if x.file_id == file_id), None)
        if f is None:
            return
        node = self._file_nodes.get(file_id)
        if node is not None:
            node.set_label(self._leaf_label(f))
        self._write_log(f"{f.filename}: размер {format_size(size)}")
        if node is not None:
            self._refresh_details(node)

    def _on_engine_event(self, event: str, f: QueueFile, ok: bool, error: str | None) -> None:
        if event == "started":
            self._update_node(f)
        elif event != "finished":
            return
        self._remaining -= 1
        if ok:
            self._done += 1
            self._write_log(f"[ok] {f.channel}/{f.post_id} {f.filename}")
        else:
            self._errors += 1
            self._write_log(f"[error] {f.channel}/{f.post_id} {f.filename}: {error}")
        self._update_node(f)
        self._refresh_details(self.query_one("#tree", Tree).cursor_node)
        self._refresh_progress()
        if self._remaining <= 0:
            self._write_log(f"Готово: {self._done} скачано, {self._errors} ошибок")

    def _update_node(self, f: QueueFile) -> None:
        node = self._file_nodes.get(f.file_id)
        if node is not None:
            node.set_label(self._leaf_label(f))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    load_dotenv()
    fire.Fire(DownloadQueueCLI)


if __name__ == "__main__":
    main()
