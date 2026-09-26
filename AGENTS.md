# AGENTS.md — Инструкции для AI-агента

## Контекст проекта

Python-проект для скачивания сообщений и медиа из Telegram каналов и чатов.
Используется для сбора данных из русскоязычных AI/ML каналов и чатов.

### Конвенции

- Весь код — на **Python 3.11+** (сейчас 3.12, pyenv: `pyenv local system`)
- Виртуальные окружения живут в `~/workspace/venvs/<проект>/<имя>/`
- Этот проект: `~/workspace/venvs/tg_chat_search/default/`
- Перед запуском кода всегда активировать: `source .venv` (из каталога проекта)
- Сообщения с пользователем — на **русском языке**
- Без эмодзи в коде, логах и документации (общее правило workspace)
- Типизация обязательна; ruff (line-length 110) и mypy strict — чистые

## Структура проекта

```
tg_chat_search/
+-- chat_search/
|   +-- __init__.py              # Экспорт tg_client
|   +-- tg_client.py             # Общий Telethon: клиент, сессии
|   +-- config.py                # Загрузка YAML-конфигов (chats/channels/uav_channels)
|   +-- queue/                   # Общие модули очереди вложений (часть 2)
|   |   +-- __init__.py          # Re-export публичного API
|   |   +-- models.py            # QueueFile, DiskIndex, KIND_ORDER, USER_AGENT
|   |   +-- queue.py             # Загрузка очереди, скан диска, приоритеты, фильтры
|   |   +-- dead.py              # Мёртвые файлы: пометка, recheck, зонд URL
|   |   +-- engine.py            # Асинхронный движок скачивания (TG + HTTP)
|   |   +-- reporting.py         # Сводки, форматирование, отчёты (JSON/CSV/TSV)
|   +-- scripts/
|       +-- __init__.py
|       +-- download_chat.py     # Сообщения из чатов через Telethon
|       +-- download_channels.py # Публичные каналы (web scraping)
|       +-- download_attachments.py  # Медиа через Telethon
|       +-- parse_attachments.py # Очередь вложений (часть 1 из 2)
|       +-- download_queue.py    # Скачиватель очереди (часть 2 из 2): GUI + CLI-обёртки
|       +-- download_cli.py      # Консольный клиент очереди: list/download/post
|       +-- get_chat_id.py       # Получить chat_id
|       +-- check_connection.py  # Авторизация + проверка связи
+-- configs/
|   +-- chats.example.yaml       # Пример: чаты {имя: chat_id} для download_chat
|   +-- channels.example.yaml    # Пример: каналы для download_channels
|   +-- uav_channels.example.yaml # Пример: UAV-каналы для parse_attachments
|   # Реальные конфиги chats.yaml / channels.yaml / uav_channels.yaml —
|   # gitignored; копируются из .example.yaml
+-- .venv                        # Скрипт активации venv (source .venv)
+-- .env.example                 # Шаблон конфигурации
+-- pyproject.toml
+-- setup.sh                     # Скрипт установки
+-- README.md / INSTALL.md / DATA.md
```

## Данные

### Хранилище данных: `~/workspace/data_not_sync/` (~700МБ)

Все скачанные данные хранятся в `~/workspace/data_not_sync/`, НЕ в проекте
(решение пользователя: данные не синхронизируются):

```
~/workspace/data_not_sync/
+-- tg_nlp_chats_data/          # Сообщения из чатов (15 чатов, ~230МБ)
+-- tg_nlp_channels_data/       # Сообщения из каналов (45+ каналов, ~89МБ)
+-- tg_nlp_chats_threads/       # Треды (~35МБ)
+-- tg_nlp_chats_embs/          # Эмбеддинги (~280МБ .npz + queries.db)
+-- tg_channels_attachments/    # Очередь вложений (15 UAV-каналов, parse_attachments)
```

Подробное описание форматов — в `DATA.md`.

### Чаты (tg_nlp_chats_data)

15 чатов из `configs/chats.yaml` (пример — `configs/chats.example.yaml`):
`natural_language_processing` (61МБ) и др.
Формат: JSONL с полями: id, url, type, text, reply_to_message_id, chat_id,
pub_time, source.

### Каналы (tg_nlp_channels_data)

45+ каналов из `configs/channels.yaml` (пример — `configs/channels.example.yaml`),
(список — `configs/channels.yaml`). Формат: JSONL
(web scraping).

### Треды и эмбеддинги

- `tg_nlp_chats_threads/`: Треды из `natural_language_processing` (~35МБ)
- `tg_nlp_chats_embs/`: Эмбеддинги для `natural_language_processing`
  (.npz ~280МБ, queries.db) — генерируются внешним проектом

### Вложения (tg_channels_attachments)

Очередь вложений для 15 UAV-каналов из `configs/uav_channels.yaml`,
собирается `chat_search/scripts/parse_attachments.py` (часть 1 из 2;
скачиватель — часть 2: `download_queue.py` / `download_cli.py`).
На каждый канал: `queue.jsonl`
(1 строка = пост или альбом с файлами, `file_id` уникален в пределах канала),
`.queue_state.json` (resume) и папки постов
`{channel}/{YYYY}/{YYYY-MM}/{YYYY-MM-DD}_{post_id}/` (`post.json` +
`files.jsonl`). Формат — в `DATA.md`.

## Скрипты

Все команды — из каталога проекта: `source .venv`.

### 0. Проверка связи и авторизация: `check_connection.py`

```bash
python -m chat_search.scripts.check_connection
```

Первый запуск — интерактивная авторизация (телефон + код из Telegram).
Сессия сохраняется в `sessions/get_chat.session` (gitignored).
Повторные запуски — быстрая проверка аккаунта и доступа к чату.

### 1. Скачать сообщения из чатов: `download_chat.py`

```bash
# Все чаты (быстрый resume)
python -m chat_search.scripts.download_chat ~/workspace/data_not_sync/tg_nlp_chats_data

# Только конкретные чаты
python -m chat_search.scripts.download_chat \
    ~/workspace/data_not_sync/tg_nlp_chats_data --only chat_a,chat_b
```

**Важно:**
- Список чатов — `configs/chats.yaml` (`--chats_file`), пример —
  `configs/chats.example.yaml`; нет конфига — ошибка до подключения к Telegram
- Быстрый resume: ID сообщений монотонны, итерация останавливается на первом
  уже скачанном ID. `--full_scan` — старый режим (вся история).
- `--only` — фильтр по именам из конфига
- `--limit N` — для тестов
- Формат: JSONL
- BATCH_SIZE=1000, DELAY_AFTER_BATCH=0.5s

### 2. Скачать публичные каналы: `download_channels.py`

```bash
# Все каналы из configs/channels.yaml
python -m chat_search.scripts.download_channels \
    --output_dir ~/workspace/data_not_sync/tg_nlp_channels_data

# Другой файл со списком каналов
python -m chat_search.scripts.download_channels \
    --output_dir ~/workspace/data_not_sync/tg_nlp_channels_data \
    --channels_file configs/my_channels.yaml

# Полный проход (восстановление пропущенных постов, подхват новых)
python -m chat_search.scripts.download_channels \
    --output_dir ~/workspace/data_not_sync/tg_nlp_channels_data \
    --full_scan
```

- Список каналов — `configs/channels.yaml` (`--channels_file`), пример —
  `configs/channels.example.yaml`; нет конфига — ошибка до подключения
- Web scraping через t.me/s/...
- Не требует авторизации
- Пагинация циклом (без рекурсии) с защитой от зацикливания
- Resume: останавливается на первой странице без новых постов (новые посты
  всегда на самом свежем конце). Не зависит от min post_id — работает даже если
  ранние посты канала — репосты/медиа (первый текстовый пост > 3). Для нового
  канала (пустой файл) — полный обход
- `--max_pages N` — лимит страниц на канал
- `--full_scan` — пройти всю историю до начала (отключает раннюю остановку
  resume; для восстановления постов, пропущенных при предыдущих скачиваниях)
- Файлы всегда отсортированы по `post_id` (возрастанию); посты с `id <= 2`
  (сервисные) и репосты пропускаются

### 3. Скачать медиа: `download_attachments.py`

```bash
python -m chat_search.scripts.download_attachments \
    --attachments_dir ~/downloads \
    --channels channel_a,channel_b \
    --skip_mp4 false --skip_jpg false
```

### 4. Собрать очередь вложений: `parse_attachments.py`

```bash
# Все каналы из configs/uav_channels.yaml (первый запуск — полная история)
python -m chat_search.scripts.parse_attachments \
    --attachments_dir ~/workspace/data_not_sync/tg_channels_attachments

# Лимит сообщений на канал (для тестов)
python -m chat_search.scripts.parse_attachments \
    --attachments_dir ~/workspace/data_not_sync/tg_channels_attachments \
    --max_messages 20

# Полный обход истории (восстановление пропущенных постов)
python -m chat_search.scripts.parse_attachments \
    --attachments_dir ~/workspace/data_not_sync/tg_channels_attachments \
    --full_scan

# Только один день (UTC), например для проверки
python -m chat_search.scripts.parse_attachments \
    --attachments_dir ~/workspace/data_not_sync/tg_channels_attachments \
    --date 2026-02-19

# Заполнить размеры внешних URL (HEAD/GET, медленнее)
python -m chat_search.scripts.parse_attachments \
    --attachments_dir ~/workspace/data_not_sync/tg_channels_attachments \
    --fetch_sizes
```

- Telethon (нужна авторизация), обход истории от свежих к старым
- В очередь попадают: внешние URL с файловым расширением (список — константа
  `FILE_EXTENSIONS`) + нативное медиа (фото/документы). Посты без файлов
  не регистрируются
- Альбомы (несколько сообщений с общим `grouped_id`) — одна строка очереди:
  `post_id` = минимальный ID, `message_ids` — все ID по возрастанию, у каждого
  файла `message_id` (часть 2 скачивает медиа по сообщению). Альбом
  дочитывается целиком: лимиты и state — только на границах групп
- Resume: `.queue_state.json` (`max_processed_id`, `iter_messages` с
  `min_id = N + 1`); очередь дописывается инкрементально, в конце канала
  переписывается отсортированной по `post_id`; state пишется только на
  границах групп (после сбоя посреди альбома он пересобирается целиком)
- `file_id` — порядковый в пределах канала (max + 1), ренумерации нет;
  известный `post_id` пропускается (строка и папка поста не перезаписываются);
  альбом не пересобирается, если хотя бы один его участник уже в очереди
- `--full_scan` — обход всей истории, известные `post_id` пропускаются
- `--date YYYY-MM-DD` — только сообщения этого дня (UTC); state не читается
  и не пишется (окно дат не гарантирует инвариант resume), известные `post_id`
  пропускаются; приоритет над resume/`--full_scan`
- URL без расширения (Google Drive и т.п.) не попадают в очередь
- `size` внешних URL по умолчанию null (заполняет `--fetch_sizes` или часть 2)

### 5. Получить chat_id: `get_chat_id.py`

```bash
python -m chat_search.scripts.get_chat_id --channel some_channel
```

### 6. Скачать очередь вложений: `download_queue.py` (часть 2 из 2)

```bash
# Список файлов со статусом (сводка + таблица + отчёт)
python -m chat_search.scripts.download_queue list \
    ~/workspace/data_not_sync/tg_channels_attachments

# Фильтры: каналы / типы / расширения
python -m chat_search.scripts.download_queue list \
    ~/workspace/data_not_sync/tg_channels_attachments \
    --channels channel_a --kinds document --ext pdf --details

# JSON-отчёт (все выбранные файлы с приоритетами и статусами)
python -m chat_search.scripts.download_queue list \
    ~/workspace/data_not_sync/tg_channels_attachments --json /tmp/report.json

# CSV/TSV-отчёт (для просмотра: табуляция для .tsv, запятая иначе)
python -m chat_search.scripts.download_queue list \
    ~/workspace/data_not_sync/tg_channels_attachments --csv /tmp/report.tsv

# Скачать выбранные типы в порядке приоритетов (T1 -> T2 -> T3)
python -m chat_search.scripts.download_queue download \
    ~/workspace/data_not_sync/tg_channels_attachments --kinds document --limit 100

# Скачать только файлы из сохранённого отчёта (JSON/CSV/TSV, `list --json`/`--csv`)
python -m chat_search.scripts.download_queue download \
    ~/workspace/data_not_sync/tg_channels_attachments --list /tmp/report.tsv

# Textual GUI: дерево kind -> ext -> имя, выбор, скачивание, прогресс
python -m chat_search.scripts.download_queue gui \
    ~/workspace/data_not_sync/tg_channels_attachments
```

- Логика (очередь, диск, приоритеты, движок, отчёты) — в общих модулях
  `chat_search/queue/`; `list`/`download` — тонкие обёртки над командами
  `download_cli.py` (раздел 7), `gui` — Textual-приложение поверх тех же
  модулей
- Читает `queue.jsonl` (часть 1), скачивает в папку поста: `external_url` —
  HTTP GET, `telegram_media` — Telethon `client.download_media(entity, message_id)`
  (нужна авторизация, как у `parse_attachments`)
- Скачивание в `{target}.part` + атомарный `os.replace`; `size` (если известен)
  сверяется — несовпадение = `error: size_mismatch`
- Статусы — `{post_dir}/downloaded.jsonl`; resume пропускает `status=success`
  (или файл уже на диске с тем же именем+размером)
- **Приоритеты** (внутри тира: kind -> имя -> дата desc): T1 уникальные,
  T2 свежайший из группы дублей/семейства версий, T3 остальные + уже на диске;
  дубликаты — глобально по всем каналам (точное имя+размер; версии —
  `X.Y[.Z]`, опц. `v`)
- Файл уже на диске (то же имя+размер) по умолчанию не скачивается
  (`--force-on-disk` — принудительно)
- `--limit N` — только первые N по приоритетам; `--concurrency` — параллельность
- `list --json PATH` / `list --csv PATH` — полный отчёт в файл (JSON /
  CSV-TSV, разделитель по расширению: .tsv — табы, иначе запятая)
- `download --list PATH` — скачать только файлы из отчёта
  (`list --json`/`--csv`; JSON/CSV/TSV определяются автоматически);
  тиры/дубли считаются по всей очереди, resume работает как обычно
- `list` не трогает сессию Telegram; `download`/`gui` создают Telethon-клиент
  лениво (нужен только для `telegram_media`)
- GUI по умолчанию не показывает скачанные (done) и мёртвые (dead) — как
  `download_cli list`; статус-фильтр (`s` в GUI / `gui --status CSV`):
  `pending,done,dead,error,disk,down`; пусто = по умолчанию (done/dead скрыты)
- опция «уникальные» (`u` в GUI / `gui --unique`): в виде по умолчанию
  дополнительно скрывает файлы, чей дубль (то же имя+размер) уже на диске;
  по умолчанию выключена; статус-фильтр имеет приоритет

### 7. Консольный клиент очереди: `download_cli.py` (часть 2 из 2, без GUI)

```bash
# Список НЕ скачанных файлов: сводка + таблица в порядке приоритетов
python -m chat_search.scripts.download_cli list \
    ~/workspace/data_not_sync/tg_channels_attachments

# Все файлы со статусом (включая done)
python -m chat_search.scripts.download_cli list \
    ~/workspace/data_not_sync/tg_channels_attachments --show_all

# Фильтры + отчёт (JSON или CSV/TSV)
python -m chat_search.scripts.download_cli list \
    ~/workspace/data_not_sync/tg_channels_attachments \
    --channels channel_a --kinds document --ext pdf --json /tmp/report.json
python -m chat_search.scripts.download_cli list \
    ~/workspace/data_not_sync/tg_channels_attachments \
    --channels channel_a --kinds document --ext pdf --csv /tmp/report.tsv

# Скачать выбранные типы в порядке приоритетов (T1 -> T2 -> T3)
python -m chat_search.scripts.download_cli download \
    ~/workspace/data_not_sync/tg_channels_attachments --kinds document --limit 100

# Скачать только файлы из сохранённого отчёта (JSON/CSV/TSV, `list --json`/`--csv`)
python -m chat_search.scripts.download_cli download \
    ~/workspace/data_not_sync/tg_channels_attachments --list /tmp/report.tsv

# Все файлы поста: spec — channel/100 или URL поста
python -m chat_search.scripts.download_cli post \
    ~/workspace/data_not_sync/tg_channels_attachments channel_a/3745
python -m chat_search.scripts.download_cli post \
    ~/workspace/data_not_sync/tg_channels_attachments https://t.me/channel_a/3745

# Проверить доступность внешних URL: 404/410 — пометить мёртвым
python -m chat_search.scripts.download_cli check \
    ~/workspace/data_not_sync/tg_channels_attachments

# Часть: только PDF одного канала
python -m chat_search.scripts.download_cli check \
    ~/workspace/data_not_sync/tg_channels_attachments --channels channel_a --ext pdf

# Повторно проверить помеченные (снять пометку, если снова доступны)
python -m chat_search.scripts.download_cli check \
    ~/workspace/data_not_sync/tg_channels_attachments --recheck
```

- Та же общая реализация, что у `download_queue` (модули `chat_search/queue/`)
- `list`: по умолчанию — только не скачанные и не мёртвые (status != done и
  != dead, включая on_disk/error — со статусом в строке); `--show_all` — все
  (dead со статусом `[dead]`); `--json PATH` / `--csv PATH` — полный отчёт в
  файл (CSV/TSV — разделитель по расширению: .tsv — табы, иначе запятая); без
  сети и без сессии Telegram
- `download`: как у `download_queue` (включая `--list PATH` — скачать файлы
  из отчёта JSON/CSV/TSV); `--include_dead` — включить мёртвые (по умолчанию
  пропускаются); Telethon-клиент создаётся лениво, только если в выборке есть
  `telegram_media`
- `post`: все файлы поста с обычным resume (done/on_disk/dead пропускаются,
  кроме `--force_on_disk`/`--include_dead`); пост не найден — сообщение и
  exit code 1
- `check`: проверка доступности внешних URL (HTTP HEAD, фолбэк GET при
  403/405); 404/410 — пометить мёртвым (`.dead.jsonl`), 403/5xx/таймауты —
  в отчёт без пометки; по умолчанию проверяются не скачанные и не помеченные;
  `--recheck` — только помеченные (снова доступен — снять пометку;
  `message_not_found` пропускается со счётчиком). Без сессии Telegram
- **Авто-пометка в движке**: при скачивании `message_not_found` (сообщение
  удалено) и HTTP 404/410 файл автоматически помечается мёртвым (движок
  получает `attachments_dir`); список/скачивание dead пропускают по умолчанию

## Конфигурация

### .env

```
TG_API_ID=your_api_id
TG_API_HASH=your_api_hash
```

### Конфиги (configs/)

Списки чатов/каналов — во внешних YAML-конфигах (общий модуль загрузки —
`chat_search/config.py`):

- `chats.yaml` — маппинг `имя: chat_id` (download_chat, check_connection)
- `channels.yaml` — список `- имя` (download_channels)
- `uav_channels.yaml` — список `- имя` (parse_attachments)

Реальные конфиги — gitignored; примеры — в git
(`configs/*.example.yaml`). Первый запуск: скопировать пример в реальный файл
и отредактировать. Конфиг читается до подключения к Telegram; нет конфига —
понятная ошибка с подсказкой (исключение — check_connection: без `chats.yaml`
проверка доступа к чату пропускается, get_me работает).

### Зависимости (pyproject.toml)

- telethon>=1.36.0 — Telegram MTProto клиент
- requests>=2.31.0 — HTTP requests
- beautifulsoup4>=4.12.0, lxml>=5.0.0 — web scraping
- html2text>=2024.2.26 — конвертация HTML в текст
- fire>=0.6.0 — CLI интерфейс
- python-dotenv>=1.0.0 — загрузка .env
- tqdm>=4.66.0 — прогресс-бары
- textual>=8.0.0 — TUI (download_queue gui)
- pyyaml>=6.0 — YAML-конфиги (chat_search/config.py)
- dev: ruff, mypy, pytest

## Текущее состояние

### Окружение

- venv: `~/workspace/venvs/tg_chat_search/default/` (Python 3.12.3)
- Активация: `source .venv`
- Telegram с рабочей машины доступен напрямую

### Данные (на 2026-09-04)

- `tg_nlp_chats_data`: 15 чатов, ~260МБ, 703507 записей
  (докачаны 2026-09-04, добавлено 75034 сообщения; 0 битых, 0 дубликатов)
- `tg_nlp_channels_data`: 45+ каналов, ~89МБ, 68144 записи
  (докачаны 2026-09-04, добавлено ~15 тыс. постов)
- `tg_nlp_chats_threads`: 1 тред, ~35МБ (собирались из состояния чата
  на январь 2026 — при обновлении тредов пересобрать из нового JSONL)
- `tg_nlp_chats_embs`: 1 набор эмбеддингов, ~280МБ (актуальны только
  для сообщений на январь 2026 — при новых данных перегенерировать)
- `tg_channels_attachments`: очередь вложений, 15 UAV-каналов, полная
  история (2026-09-22: 65428 постов / 103937 файлов, ~804МБ; альбомы
  сгруппированы — 13394 поста-альбома со всеми файлами в одной строке;
  бэкап старого формата: `tg_channels_attachments_backup_20260921`)

### Задачи

1. **Регулярная докачка** — скрипты идемпотентны, можно в cron:
   - каналы: `python -m chat_search.scripts.download_channels --output_dir ~/workspace/data_not_sync/tg_nlp_channels_data`
   - чаты: `python -m chat_search.scripts.download_chat ~/workspace/data_not_sync/tg_nlp_chats_data`
   - очередь вложений: `python -m chat_search.scripts.parse_attachments --attachments_dir ~/workspace/data_not_sync/tg_channels_attachments`
2. **Пересобрать треды и эмбеддинги** для новых сообщений
   (внешние скрипты, не в этом репозитории)
3. **Скачиватель медиа (часть 2)** — готово: `download_queue.py`
   (list/download/gui, приоритеты T1/T2/T3, дубликаты, resume) +
   `download_cli.py` (консольный клиент: list/download/post); общая логика —
   в `chat_search/queue/`. Очередь полная (2026-09-22); запуск — разделы 6-7

## Полезные команды

### Проверка качества

```bash
source .venv
ruff check chat_search/ tests/
mypy chat_search/
python -m pytest tests/ -v
```

## Тестирование

Risk-based: тесты покрывают только реальные риски, не ради покрытия.

```
tests/
├── conftest.py           # фикстуры: load_post (HTML-фикстуры), make_spider
├── test_data/            # реальные сниппеты t.me (текст, one_media, репост, медиа-only)
└── unit/
    ├── queue_fixtures.py       # общие фикстуры очереди: make_fixture, фейки HTTP/TG
    ├── test_queue.py           # chat_search.queue: версия/семейства, тиры, диск, фильтры
    ├── test_queue_engine.py    # движок: probe_size, HTTP/TG success/error, resume, parallel, dead
    ├── test_dead.py            # мёртвые: mark/load/unmark, probe_url, check_urls
    ├── test_parse_post.py      # парсинг поста; регрессия: one_media не теряется
    ├── test_save_records.py    # merge/dedup/sort, целостность (нет потери записей)
    ├── test_pagination.py      # stop-условия: id<=1, без прогресса, max_pages, resume, full_scan
    ├── test_config.py          # config: load_chats / load_channel_list (валидация, ошибки)
    ├── test_download_chat.py   # чаты: load_existing_ids, message_record, resume, full_scan
    ├── test_parse_attachments.py # очередь вложений: URL/медиа, file_id, resume, full_scan, date, альбомы
    ├── test_download_queue.py  # GUI: TUI smoke, статус-фильтр, уникальные, обёртки
    ├── test_download_cli.py    # консольный клиент: run_list/download/post, parse_post_spec
    └── test_helpers.py         # parse_post_url, to_timestamp
```

- Тесты изолированы от сети: HTML-фикстуры в `test_data/`, Telethon-клиент,
  `_fetch_page`, HTTP-сессия и движок скачивания замещены фейками.
- Запуск: `python -m pytest tests/ -v` (206 тестов).
- При изменении парсинга/сохранения/пагинации/логики чатов/очереди вложений —
  сначала прогнать тесты.

### Скачивание чатов

```bash
source .venv
cd ~/workspace/projects/tg_chat_search
python -m chat_search.scripts.download_chat ~/workspace/data_not_sync/tg_nlp_chats_data
```

### Скачивание каналов

```bash
source .venv
cd ~/workspace/projects/tg_chat_search
python -m chat_search.scripts.download_channels \
    --output_dir ~/workspace/data_not_sync/tg_nlp_channels_data
```

## Важные замечания

1. **Докачка чатов**: быстрый resume — итерация до первого известного ID
2. **Докачка каналов**: если история полная — одна страница за запуск
3. **Сессии Telethon**: `sessions/` в проекте, gitignored; при переносе
   на другую машину — авторизация заново
4. **Парсинг медиа+текст**: текст поста ищется где угодно внутри поста
   (`div.tgme_widget_message_text`), а не по жёсткому уровню вложенности —
   раньше часть постов (текст в `tgme_widget_message_one_media`) терялась.
   Если подозреваешь пропущенные посты — прогони `--full_scan` по каналу
5. **Порядок в файлах каналов**: всегда отсортировано по `post_id`
   (возрастанию); первая строка — самый ранний пост
6. **Идемпотентность full_scan (чаты)**: `download_chat` в режиме `--full_scan`
   пропускает уже скачанные ID, поэтому повторный прогон не создаёт дубликаты
   (добавляет только новое). Тест: `test_download_full_scan_no_duplicates_on_rerun`
7. **Не перезаписываем существующее**: оба скрипта сохраняют уже скачанные
   записи — повторный прогон не меняет их содержимое. Чаты: append + пропуск
   известных ID. Каналы: `parse_channel` пропускает известные post_id, а
   `_save_records` при совпадении post_id сохраняет существующую версию
   (`setdefault`, не перезапись). Тесты: `test_download_preserves_existing_content`,
   `test_download_channel_preserves_existing_content`,
   `test_dedup_existing_version_not_overwritten`
8. **Быстрый resume не зависит от min post_id**: раньше ранняя остановка
    resume требовала `min_existing <= 3`, но если ранние посты канала —
    репосты/медиа (первый текстовый пост > 3, например min post_id = 4),
    эвристика не срабатывала и resume делал полный обход до начала. Теперь
    resume останавливается на первой странице без новых постов (новые посты
    всегда на самом свежем конце). Тест: `test_resume_stops_fast_when_first_text_post_gt_3`
9. **Очередь вложений (parse_attachments)**: `file_id` — порядковый в
   пределах канала (max + 1), ренумерации нет никогда; известный `post_id`
   пропускается без перезаписи строки и папки поста. Альбомы (общий
   `grouped_id`) — одна строка очереди: `post_id` = min ID, `message_ids` —
   все ID, у файлов `message_id` (часть 2 скачивает медиа по сообщению);
   альбом не пересобирается, если участник уже в очереди. State пишется
   только на границах групп: после сбоя посреди альбома он пересобирается
   целиком. Если первый прогон прервался посреди истории, докачай
   `--full_scan` (resume берёт только новые сообщения с `max_processed_id + 1`).
   URL без расширения (Google Drive и т.п.) в очередь не попадают
10. **Дата-фильтр не трогает state**: `--date` сканирует только окно дня и не
    пишет `.queue_state.json` — иначе прогон по старому дню при пустом state
    поставил бы `max_processed_id` на маленький ID и сломал бы resume
    (пропустил бы всю историю до него). Тесты: `test_parse_channel_date_filter*`
11. **Имя файла документа — из DocumentAttributeFilename**: у TL-объекта
    `Document` нет атрибута `file_name` (и `media.file_name` в Telethon 1.44
    тоже нет) — раньше парсер всегда читал None. Теперь имя берётся из
    `document.attributes` (`_document_file_name`); если имени нет —
    генерируется `file_{post_id}.{ext из mime}` (`MIME_TO_EXT`)
12. **Скачиватель очереди (download_queue / download_cli)**: общая логика —
    в пакете `chat_search/queue/` (models, queue, dead, engine, reporting);
    `download_queue.py` — GUI + CLI-обёртки, `download_cli.py` — консольный
    клиент (list/download/post/check). Скачивание в `.part` + атомарный
    `os.replace`; resume — по `downloaded.jsonl` (`status=success`) ИЛИ по
    файлу на диске (то же имя+размер). Приоритеты T1/T2/T3, дубликаты
    глобально (точное имя+размер) + семейства версий `X.Y[.Z]` (опц. `v`);
    T2 — свежайший `pub_time` из группы. Файл уже на диске по умолчанию
    не скачивается (`--force-on-disk`). Мёртвые файлы (404/410,
    `message_not_found`) — в `{channel}/.dead.jsonl`, list/download пропускают
    по умолчанию (`--show_all`/`--include_dead`). `list`/`check` не трогают
    сессию Telegram, `download`/`post`/`gui` создают Telethon-клиент лениво
    (только для `telegram_media`). Тесты: `tests/unit/test_queue.py`,
    `test_queue_engine.py`, `test_dead.py`, `test_download_queue.py`,
    `test_download_cli.py`
13. **TG-скачивание в движке: `get_messages` + `download_media(msg)`**:
    `client.download_media((entity, message_id))` (tuple-паттерн) НЕ работает
    в Telethon 1.44 — функция молча возвращает None (tuple не является
    Message/Media), файл не создаётся, и `_commit` падает на отсутствии
    `.part`. Правильно: `msg = await client.get_messages(entity, ids=...)`,
    затем `download_media(msg, file=tmp)`; `msg is None` → `message_not_found`,
    `msg.media is None` → `no_media`. Также `client.is_connected()` —
    синхронный метод (без `await`); `is_user_authorized()` — асинхронный.
    Сбой подготовки TG-клиента помечает движок «broken» и повторяет исходный
    код ошибки (не превращает в `telegram_not_authorized`); детали ошибки
    идут в событие `finished` (в `downloaded.jsonl` — только код).
    Тесты: `test_engine_tg_success`, `test_engine_tg_no_media`,
    `test_engine_tg_connect_error_keeps_code_and_detail`