# INSTALL.md — Установка и повторное развёртывание с нуля

Пошаговая инструкция. Предполагается, что читатель может захотеть
развернуть проект заново через месяцы.

## Требования

- Python 3.11+ (проверено на 3.12)
- pyenv (для управления версиями Python; опционально)
- Доступ в интернет
- Telegram API credentials: https://my.telegram.org

## Шаг 1. Клонирование / получение кода

```bash
cd ~/workspace/projects
git clone <url-репозитория> tg_chat_search
cd tg_chat_search
```

## Шаг 2. Привязка версии Python (pyenv)

```bash
pyenv local system
```

(Создаёт `.python-version` в корне проекта. Если нужна конкретная версия:
`pyenv install 3.12.3 && pyenv local 3.12.3`.)

## Шаг 3. Создание виртуального окружения

venv живёт **вне** проекта, в `~/workspace/venvs/`:

```bash
mkdir -p ~/workspace/venvs/tg_chat_search
python3 -m venv ~/workspace/venvs/tg_chat_search/default
```

## Шаг 4. Установка зависимостей

```bash
cd ~/workspace/projects/tg_chat_search
source .venv
pip install -e .
```

Для разработки (ruff, mypy, pytest):

```bash
pip install -e ".[dev]"
```

## Шаг 5. Конфигурация

```bash
cp .env.example .env
```

Заполните `.env`:

```bash
TG_API_ID=<ваш api id с my.telegram.org>
TG_API_HASH=<ваш api hash с my.telegram.org>
```

## Шаг 6. Проверка

```bash
cd ~/workspace/projects/tg_chat_search
source .venv

# Линт и типы
ruff check chat_search/
mypy chat_search/

# Проверка связи с Telegram (первый запуск — интерактивная авторизация:
# номер телефона + код из Telegram; сессия сохранится в sessions/)
python -m chat_search.scripts.check_connection

# Быстрый тест web-скрейпера (без авторизации): минимальный конфиг
# с одним каналом + --channels_file
cat > /tmp/test_channels.yaml <<'EOF'
- example_channel
EOF
python -m chat_search.scripts.download_channels \
    ~/workspace/tmp/tg_chat_search/test_data --channels_file /tmp/test_channels.yaml
```

## Шаг 7. Использование

См. `README.md`, раздел «Использование».

## Повторное развёртывание

Если venv пропал — достаточно шагов 3-4. Если пропал весь проект —
шаги 1-6. Данные (JSONL) живут в `~/workspace/data_not_sync/` и
не зависят от проекта; при повторной установке скачивание продолжится
с места обрыва (докачка).

## Устранение неполадок

| Симптом | Причина | Решение |
|---|---|---|
| `pip install -e .` падает на сборке | битый `build-backend` в pyproject | должен быть `setuptools.build_meta` |
| `ModuleNotFoundError: telethon` | не активирован venv | `source .venv` |
| `RuntimeError: Установите TG_API_ID...` | пустой `.env` | заполнить credentials |
| Скрейпер: «Посты не найдены» | страница изменила структуру | посмотреть HTML t.me/s/... |
| `RecursionError` (старые версии) | пагинация через рекурсию | уже исправлено: пагинация циклом |