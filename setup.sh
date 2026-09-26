#!/bin/bash
# setup.sh — Установка и настройка tg-chat-search
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_NAME="tg_chat_search"
VENV_DIR="$HOME/workspace/venvs/$PROJECT_NAME/default"

echo "[1/4] Создание виртуального окружения: $VENV_DIR"
mkdir -p "$(dirname "$VENV_DIR")"
python3 -m venv "$VENV_DIR"

echo "[2/4] Установка зависимостей..."
source "$VENV_DIR/bin/activate"
cd "$PROJECT_DIR"
pip install -e . --retries 5

echo "[3/4] Конфигурация..."
if [ ! -f "$PROJECT_DIR/.env" ]; then
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
    echo "   Создан .env — заполните TG_API_ID и TG_API_HASH"
else
    echo "   .env уже существует"
fi

mkdir -p "$PROJECT_DIR/sessions"

echo "[4/4] Проверка..."
ruff check chat_search/ 2>/dev/null || echo "   (ruff не установлен — пропуск)"
python -c "import chat_search; print('   import chat_search: OK')"

echo ""
echo "Установка завершена."
echo ""
echo "Дальнейшие шаги:"
echo "  1. Заполните .env (TG_API_ID, TG_API_HASH)"
echo "  2. Активируйте окружение:  source .venv"
echo "  3. Проверьте связь (первый раз — авторизация):"
echo "     python -m chat_search.scripts.check_connection"
echo ""
echo "  Скачивание:"
echo "     # Чаты (Telethon):"
echo "     python -m chat_search.scripts.download_chat ~/workspace/data_not_sync/tg_nlp_chats_data"
echo "     # Каналы (web scraping, без авторизации):"
echo "     python -m chat_search.scripts.download_channels --output_dir ~/workspace/data_not_sync/tg_nlp_channels_data"