# tg-chat-search

Скачивание сообщений и медиа из Telegram каналов и чатов.
Используется для сбора данных из русскоязычных AI/ML каналов и чатов.

## Возможности

- **Скачивание сообщений** из приватных чатов через Telethon API (JSONL)
- **Скачивание публичных каналов** через web-скрейпинг (t.me/s/...) без авторизации
- **Скачивание медиа** (фото, документы) из каналов
- **Получение chat_id** по имени канала
- **Проверка связи и первичная авторизация** (`check_connection.py`)
- **Быстрая докачка**:
  - чаты: итерация останавливается на первом уже скачанном ID (ID монотонны)
  - каналы: если история полная, resume обходится одной страницей
- **Сортировка каналов**: JSONL-файлы каналов всегда отсортированы по
  `post_id` (возрастанию) — первая строка самый ранний пост

## Установка

```bash
cd ~/workspace/projects/tg_chat_search
chmod +x setup.sh
./setup.sh
```

Или вручную (подробности — в `INSTALL.md`):

```bash
python3 -m venv ~/workspace/venvs/tg_chat_search/default
source .venv
pip install -e .
```

Активация окружения в любой момент:

```bash
cd ~/workspace/projects/tg_chat_search
source .venv
```

## Настройка

### 1. Telegram API credentials

Получите на https://my.telegram.org и заполните `.env`:

```bash
TG_API_ID=your_api_id
TG_API_HASH=your_api_hash
```

### 2. Конфиги со списками чатов/каналов

Списки целей — в YAML-конфигах в `configs/` (реальные файлы вне git,
примеры — в git). Первый запуск: скопировать пример в реальный файл:

```bash
cp configs/chats.example.yaml configs/chats.yaml          # download_chat
cp configs/channels.example.yaml configs/channels.yaml    # download_channels
cp configs/uav_channels.example.yaml configs/uav_channels.yaml  # parse_attachments
```

Затем при необходимости отредактировать списки.

## Использование

Все команды — из каталога проекта с активированным окружением:

```bash
cd ~/workspace/projects/tg_chat_search
source .venv
```

### 0. Проверка связи и первичная авторизация

Первый запуск любого Telethon-скрипта требует авторизации
(номер телефона + код из Telegram, ~2 минуты). Сессия сохраняется
в `sessions/get_chat.session`, повторные запуски — автономны.

```bash
python -m chat_search.scripts.check_connection
```

### 1. Скачать сообщения из чатов (Telethon)

```bash
# Все чаты из configs/chats.yaml (быстрый resume)
python -m chat_search.scripts.download_chat ~/workspace/data_not_sync/tg_nlp_chats_data

# Только конкретные чаты
python -m chat_search.scripts.download_chat \
    ~/workspace/data_not_sync/tg_nlp_chats_data --only chat_a,chat_b
```

Флаги:

- `--chats_file PATH` — YAML-конфиг {имя: chat_id} (по умолчанию `configs/chats.yaml`)
- `--only a,b,c` — только перечисленные чаты из конфига
- `--full_scan` — пройти всю историю (по умолчанию — быстрый resume)
- `--limit N` — ограничить число сообщений (для тестов)

### 2. Скачать публичные каналы (web scraping)

Не требует авторизации, использует тёмный режим t.me:

```bash
# Все каналы из configs/channels.yaml
python -m chat_search.scripts.download_channels \
    --output_dir ~/workspace/data_not_sync/tg_nlp_channels_data

# Другой файл со списком каналов
python -m chat_search.scripts.download_channels \
    --output_dir ~/workspace/data_not_sync/tg_nlp_channels_data \
    --channels_file configs/my_channels.yaml

# Полный проход всей истории (восстановление пропущенных постов)
python -m chat_search.scripts.download_channels \
    --output_dir ~/workspace/data_not_sync/tg_nlp_channels_data \
    --full_scan
```

Флаги:

- `--channels_file PATH` — YAML-конфиг со списком каналов
  (по умолчанию `configs/channels.yaml`)
- `--max_pages N` — ограничить число страниц на канал (для тестов)
- `--full_scan` — пройти всю историю до начала (по умолчанию — быстрый
  resume, останавливается когда история полная и новых постов нет)

Файлы всегда отсортированы по `post_id` (возрастанию): первая строка —
самый ранний пост. Посты с `id <= 2` (сервисные) и репосты пропускаются.

### 3. Получить chat_id

```bash
python -m chat_search.scripts.get_chat_id --channel some_channel
```

### 4. Скачать медиа (фото, документы)

```bash
# По имени каналов
python -m chat_search.scripts.download_attachments \
    --attachments_dir ~/downloads \
    --channels some_channel

# С медиа всех типов (включая MP4 и JPEG)
python -m chat_search.scripts.download_attachments \
    --attachments_dir ~/downloads \
    --channels some_channel \
    --skip_mp4 false --skip_jpg false
```

## Архитектура

```
+---------------------------------------------------+
|              Скрипт tg-chat-search                |
|                                                   |
|  +---------------------------------------------+  |
|  |  tg_client.py (общий для всех Telethon-     |  |
|  |  скриптов): клиент, сессии в sessions/      |  |
|  +---------------------------------------------+  |
|                                                   |
|  Telethon (сообщения, медиа)                      |
|  requests + BeautifulSoup (web scraping)          |
+---------------------------------------------------+
```

## Структура проекта

```
tg_chat_search/
+-- chat_search/
|   +-- __init__.py
|   +-- tg_client.py             # Общий Telethon-клиент + сессии
|   +-- config.py                # Загрузка YAML-конфигов (chats/channels/uav_channels)
|   +-- scripts/
|       +-- download_chat.py     # Сообщения из чатов (Telethon)
|       +-- download_channels.py # Публичные каналы (web scraping)
|       +-- download_attachments.py  # Медиа (Telethon)
|       +-- get_chat_id.py       # Получить chat_id
|       +-- check_connection.py  # Авторизация + проверка связи
+-- configs/                     # YAML-конфиги списков (реальные — gitignored,
|                                #   примеры *.example.yaml — в git)
+-- tests/                       # Тесты (unit, test_data с HTML-фикстурами)
+-- .venv                        # Скрипт активации venv (source .venv)
+-- .env.example                 # Шаблон конфигурации
+-- pyproject.toml
+-- setup.sh                     # Скрипт установки
+-- INSTALL.md                   # Пошаговая установка с нуля
+-- DATA.md                      # Описание данных
+-- README.md
+-- AGENTS.md                    # Контекст для AI-агента
```

## Требования

- Python 3.11+
- Telegram API credentials (https://my.telegram.org)

## Зависимости

- telethon — Telegram MTProto клиент
- requests + beautifulsoup4 + lxml — web scraping
- fire — CLI интерфейс
- python-dotenv — загрузка .env
- html2text — конвертация HTML в текст
- tqdm — прогресс-бары
- pyyaml — YAML-конфиги списков чатов/каналов

## Проверка качества

```bash
source .venv
ruff check chat_search/ tests/
mypy chat_search/
python -m pytest tests/ -v
```

## Тестирование

Risk-based тесты (39) покрывают реальные риски: парсинг поста (включая
регрессию one_media), сохранение (merge/dedup/sort, целостность данных),
пагинацию (stop-условия, resume, full_scan) и логику чатов (resume,
message_record, идемпотентность full_scan). Ключевая гарантия: повторный
прогон **не перезаписывает** уже скачанные записи и не создаёт дубликатов.
Тесты изолированы от сети (HTML-фикстуры + фейки Telethon-клиента и
`_fetch_page`). Подробности — в `AGENTS.md`.