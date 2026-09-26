# DATA.md — Описание данных

Все скачанные данные хранятся в `~/workspace/data_not_sync/` (решение пользователя:
данные не синхронизируются, поэтому не переносились в `~/workspace/data/`).

```
~/workspace/data_not_sync/
+-- tg_nlp_chats_data/     # Сообщения из приватных чатов (Telethon)
+-- tg_nlp_channels_data/  # Сообщения из публичных каналов (web scraping)
+-- tg_nlp_chats_threads/  # Треды (собранные из natural_language_processing)
+-- tg_nlp_chats_embs/     # Эмбеддинги (внешний проект, не генерируется этим кодом)
+-- tg_channels_attachments/  # Очередь вложений (15 UAV-каналов, Telethon)
```

## tg_nlp_chats_data/ — сообщения из чатов

15 чатов, скачаны `chat_search/scripts/download_chat.py`.
Формат: JSONL, одна запись на строку:

```json
{"id": 12345, "url": "https://t.me/example_chat/12345", "type": "message",
 "text": "...", "reply_to_message_id": 12340, "chat_id": -1001234567890,
 "pub_time": 1618932302, "source": "example_chat"}
```

Поля:

- `id` — ID сообщения в Telegram
- `url` — публичная ссылка на сообщение
- `type` — всегда `message`
- `text` — текст сообщения (медиа-only сообщения не сохраняются)
- `reply_to_message_id` — ID сообщения, на которое отвечают (или null)
- `chat_id` — числовой ID чата
- `pub_time` — unix timestamp публикации
- `source` — имя чата

Крупнейшие: `natural_language_processing` (~61МБ) и др.

## tg_nlp_channels_data/ — сообщения из каналов

45+ публичных каналов, скачаны `chat_search/scripts/download_channels.py`.
Формат: JSONL:

```json
{"url": "https://t.me/example_channel/12345", "channel_id": "example_channel",
 "post_id": 12345, "id": 12345, "text": "...",
 "links": ["https://..."], "fetch_time": 1775470211, "pub_time": 1773752092,
 "reply_to": "...", "reply_to_message_id": "...", "source": "example_channel"}
```

Особенности:

- Репосты (forwarded) не сохраняются
- Посты без текста (только медиа) не сохраняются
- `fetch_time` — момент скачивания, `pub_time` — момент публикации
- `reply_to*` присутствуют только у ответов

## tg_nlp_chats_threads/ — треды

Треды, собранные из `natural_language_processing` (~35МБ).
Собирались отдельным (не входящим в этот репозиторий) скриптом
по `reply_to_message_id` из `tg_nlp_chats_data/natural_language_processing.jsonl`.

## tg_nlp_chats_embs/ — эмбеддинги

Генерировались внешним проектом (поиск по сообщениям), не этим репозиторием:

- `natural_language_processing.jsonl` — сообщения для эмбеддингов
- `natural_language_processing.npz` — матрица эмбеддингов (~280МБ)
- `natural_language_processing_queries.db` — SQLite с запросами

## tg_channels_attachments/ — очередь вложений

Очередь файлов для скачивания, собирается `chat_search/scripts/parse_attachments.py`
(часть 1 из 2; скачиватель — часть 2). Каналы — из `configs/uav_channels.yaml`
(15 UAV-каналов), источник — Telethon.

Структура (на каждый канал):

```
tg_channels_attachments/
+-- {channel}/
|   +-- queue.jsonl              # Очередь: 1 строка = пост с файлами
|   +-- .queue_state.json        # {"max_processed_id": N} — resume
|   +-- .dead.jsonl              # Мёртвые файлы части 2 (404/410, удалённое сообщение)
|   +-- {YYYY}/{YYYY-MM}/{YYYY-MM-DD}_{post_id}/
|       +-- post.json            # Исходник сообщения (что извлёк парсер)
|       +-- files.jsonl          # 1 строка = файл (та же схема, что queue.files)
|       +-- <скачанные файлы>    # Заполнит часть 2
|       +-- downloaded.jsonl     # Статусы части 2
```

### Строка `queue.jsonl`

```json
{"post_id": 356, "channel": "example_channel", "url": "https://t.me/example_channel/356",
 "pub_time": 1749667200, "message_ids": [356], "files": [
  {"file_id": 12, "type": "external_url", "url": "https://example.com/a.pdf",
   "filename": "a.pdf", "ext": "pdf", "size": null, "kind": "document",
   "message_id": 356},
  {"file_id": 13, "type": "telegram_media", "url": null,
   "filename": "GPS_spuфинг.pdf", "ext": "pdf", "mime_type": "application/pdf",
   "size": 123456, "kind": "document", "message_id": 356}
]}
```

- В очередь попадают только посты с хотя бы одним файлом; файл — это
  внешний URL с файловым расширением (список — `FILE_EXTENSIONS` в
  `parse_attachments.py`) или нативное медиа Telegram (фото/документ).
  URL без расширения (Google Drive и т.п.) не попадают
- **Альбомы** (несколько сообщений с общим `grouped_id`) — одна строка:
  `post_id` — минимальный ID альбома, `message_ids` — все ID по возрастанию,
  файлы — со всех сообщений альбома (по возрастанию ID сообщений).
  Одиночный пост: `message_ids = [post_id]`
- `file_id` — уникален в пределах канала, монотонно растёт (max + 1),
  ренумерации нет
- `message_id` (у файла) — ID сообщения, из которого файл извлечён;
  для `telegram_media` часть 2 скачивает по нему
  (`client.download_media(entity, message_id)`)
- `type` ∈ `external_url` | `telegram_media`; `url` — только для
  `external_url` (для `telegram_media` — null)
- `filename` — лучшее предположение имени (basename из URL,
  `DocumentAttributeFilename` у документа, для фото `photo_{message_id}.jpg`);
  если имени нет — `file_{message_id}.{ext}` (ext из mime_type)
- `ext` — нижний регистр без точки; `size` — байты или null (у внешних URL
  по умолчанию null)
- `kind` ∈ `photo` | `video` | `audio` | `document` | `other`
- Файлы внутри строки — по возрастанию `file_id`; строки — по возрастанию
  `post_id`
- Репосты не пропускаются (их файлы тоже в очереди)

### `post.json`

Одиночный пост:

```json
{"post_id": 356, "channel": "example_channel", "url": "https://t.me/example_channel/356",
 "pub_time": 1749667200, "text": "...", "caption": "...",
 "media": {"type": "document", "file_name": "...", "mime_type": "application/pdf",
           "size": 123},
 "reply_to_message_id": null, "forwarded_from": null, "parse_time": 1758530000}
```

Альбом (папка `{date}_{min_id}`): исходник — сообщение альбома с текстом или
подписью (иначе — с минимальным ID), `post_id`/`url`/`pub_time` — от
минимального ID, `media` — список сводок по всем сообщениям альбома
(по возрастанию ID), `message_ids` — все ID:

```json
{"post_id": 3745, "channel": "example_channel", "url": "https://t.me/example_channel/3745",
 "pub_time": 1789474726, "text": "...", "caption": null,
 "media": [{"type": "document", "file_name": "PIDtoolboxV094_osx.dmg", ...}, ...],
 "message_ids": [3745, 3746, 3747, 3748, 3749, 3750],
 "reply_to_message_id": null, "forwarded_from": null, "parse_time": 1758530000}
```

Сводка медиа: `{"type": "photo"}` для фото, `{"type": "document", ...}` для
документов, `{"type": "<имя класса>"}` для прочих типов.
`forwarded_from` — `{"from_id", "name"}` у репостов.

### Контракт части 2 (скачиватель)

Реализован в общих модулях `chat_search/queue/` (очередь, диск, приоритеты,
движок, отчёты) и двух входах: `chat_search/scripts/download_queue.py`
(CLI: `list` / `download` / `gui`; `list`/`download` — обёртки над
`download_cli`) и консольным `chat_search/scripts/download_cli.py`
(`list` / `download` / `post`, без GUI).

- Вход: `queue.jsonl`; `external_url` — HTTP GET в папку поста,
  `telegram_media` — Telethon `client.download_media(entity, message_id)`
  (по `files[].message_id`, а не по `post_id` — в альбоме файл лежит в
  своём сообщении)
- Скачивание идёт в `{target}.part`, затем атомарно `os.replace` в целевой
  путь; если `size` известен и не совпал — файл удаляется, статус `error`
  (`size_mismatch`)
- Статусы: `{post_dir}/downloaded.jsonl`, строка:
  `{"file_id": 12, "type": "external_url", "filename": "a.pdf",
    "status": "success", "size": 123, "downloaded_at": "2026-09-21T12:00:00"}`
  (`status` ∈ success | error; поле `"error"` с кодом — только при
  `status=error`)
- Resume: пропускать записи с `status=success` (или целевой файл уже на
  диске с совпадающим размером)
- **Приоритеты** (порядок скачивания, внутри тира: kind → имя → дата desc):
  - T1 — уникальные файлы (никто не делит точное имя+размер);
  - T2 — самый свежий (`pub_time`) из каждой группы дублей / семейства
    версий (имя с версионным суффиксом `X.Y[.Z]`, опц. `v`);
  - T3 — остальные (старые копии/версии) и файлы, уже лежащие на диске
- Файл, уже лежащий на диске (то же имя+размер в любом канале), по
  умолчанию не скачивается (`--force-on-disk` — принудительно);
  группировка дублей глобальная по всем каналам

### Мёртвые файлы (`.dead.jsonl`)

Файлы с несуществующими URL (404) и удалёнными сообщениями Telegram не должны
«портить» список и выборку скачивания. Пометка хранится в `{channel}/.dead.jsonl`
(строки `queue.jsonl` не удаляются — их «владеет» часть 1, и запись нужна для
повторной проверки). «Исключение» — на уровне представления: `list` скрывает
dead по умолчанию (`--show_all`), `download`/`post`/GUI пропускают
(`--include_dead` — принудительно).

Строка (1 = 1 помётка, append-only; при чтении последняя по `(channel, file_id)`
побеждает):

```json
{"file_id": 123, "filename": "a.pdf", "url": "https://...", "reason": "http_404",
 "checked_at": "2026-09-26T06:00:00+00:00"}
```

- `reason` ∈ `http_404 | http_410 | message_not_found`
- Пометку ставят: `check` (HTTP 404/410) и движок при скачивании
  (`message_not_found` — сообщение удалено, это навсегда; 404/410 при HTTP)
- `check --recheck` — повторно проверить помеченные: снова доступен — снять
  пометку, 404/410 — обновить `checked_at`. TG-мёртвые (`message_not_found`)
  HTTP-проверке не подлежат — пропускаются со счётчиком
- 403/5xx/таймауты/сетевые ошибки пометкой НЕ считаются (в отчёт, без dead)
- Удаление файла `.dead.jsonl` = полный сброс помёток канала

## Правила работы с данными

- Скачивание — только в режиме докачки (скрипты сами пропускают существующие ID)
- Не редактировать JSONL на месте; при необходимости трансформаций —
  писать в новый файл
- Данные тяжёлые (~700МБ), в git не попадают