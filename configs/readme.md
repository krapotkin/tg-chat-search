# запуск с конфигом configs/uav_channels.yaml
python -m chat_search.scripts.parse_attachments \
    ~/workspace/data_not_sync/tg_channels_attachments \
    --channels_file configs/uav_channels.yaml

# как запустить консольную gui утилиту по заказчке
python -m chat_search.scripts.download_queue gui ~/workspace/data_not_sync/tg_channels_attachments

python -m chat_search.scripts.download_queue gui \
    ~/workspace/data_not_sync/tg_channels_attachments

# Check на ошибки
# Прогон по всем ссылкам — команда check
python -m chat_search.scripts.download_cli check ~/workspace/data_not_sync/tg_channels_attachments

# повторная проверка уже помеченных (снова доступен — снять пометку)
python -m chat_search.scripts.download_cli check ~/workspace/data_not_sync/tg_channels_attachments --recheck

# часть: только PDF одного канала
python -m chat_search.scripts.download_cli check ~/workspace/data_not_sync/tg_channels_attachments --channels channel_a --ext pdf

# 1. Отчёт по всем PDF (по умолчанию — только не скачанные)
python -m chat_search.scripts.download_cli list \
    ~/workspace/data_not_sync/tg_channels_attachments \
    --ext pdf --json /tmp/pdfs.json

# 2. Оставить только уникальные (tier == 1)
python -c "
import json
data = json.load(open('/tmp/pdfs.json'))
unique = [d for d in data if d['tier'] == 1]
json.dump(unique, open('/tmp/pdfs_unique.json', 'w'), ensure_ascii=False)
print(f'Уникальных PDF: {len(unique)} из {len(data)}')
"

# 3. Скачать именно их
python -m chat_search.scripts.download_cli download \
    ~/workspace/data_not_sync/tg_channels_attachments \
    --list /tmp/pdfs_unique.json