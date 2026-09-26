"""
config.py — Загрузка YAML-конфигов со списками чатов и каналов.

Реальные конфиги (configs/chats.yaml, configs/channels.yaml,
configs/uav_channels.yaml) — вне git; примеры (configs/*.example.yaml) — в git.
"""

from __future__ import annotations

import os
from typing import Any

import yaml

DEFAULT_CHATS_FILE = "configs/chats.yaml"
DEFAULT_CHANNELS_FILE = "configs/channels.yaml"
DEFAULT_UAV_CHANNELS_FILE = "configs/uav_channels.yaml"


def _missing_config_message(path: str, what: str) -> str:
    """Сообщение об отсутствии конфига: подсказка про .example.yaml, если он есть."""
    root, ext = os.path.splitext(path)
    example = f"{root}.example.yaml" if ext == ".yaml" else None
    if example is not None and os.path.exists(example):
        return (
            f"Не найден конфиг {path} ({what}). "
            f"Скопируйте {example} в {path} и при необходимости отредактируйте."
        )
    return f"Не найден конфиг {path} ({what}). Создайте файл вручную."


def _read_yaml(path: str) -> Any:
    """Прочитать YAML-файл (None, если файл пуст). ValueError при битом YAML."""
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ValueError(f"Не удалось разобрать YAML в {path}: {e}") from e


def load_channel_list(path: str) -> list[str]:
    """
    Список каналов из YAML-конфига (список строк, по одному на строку).

    Дубликаты удаляются, порядок сохраняется.
    Ошибки: FileNotFoundError (нет файла, с подсказкой про .example.yaml),
    ValueError (битый YAML, не список, элемент не str, пустой список).
    """
    if not os.path.exists(path):
        raise FileNotFoundError(_missing_config_message(path, "список каналов"))

    data = _read_yaml(path)
    if data is None:
        raise ValueError(f"{path}: список каналов пуст")
    if not isinstance(data, list):
        kind = type(data).__name__
        raise ValueError(f"{path}: ожидается YAML-список каналов, получено {kind}")

    channels: list[str] = []
    seen: set[str] = set()
    for i, item in enumerate(data):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(
                f"{path}: элемент {i} должен быть непустой строкой, получено {item!r}"
            )
        name = item.strip()
        if name not in seen:
            seen.add(name)
            channels.append(name)

    if not channels:
        raise ValueError(f"{path}: список каналов пуст")
    return channels


def load_chats(path: str) -> dict[str, int]:
    """
    Словарь {имя чата: chat_id} из YAML-конфига.

    Ошибки: FileNotFoundError (нет файла, с подсказкой про .example.yaml),
    ValueError (битый YAML, не словарь, имя не str, chat_id не int, пустой словарь).
    """
    if not os.path.exists(path):
        raise FileNotFoundError(_missing_config_message(path, "маппинг имя: chat_id"))

    data = _read_yaml(path)
    if data is None:
        raise ValueError(f"{path}: список чатов пуст")
    if not isinstance(data, dict):
        kind = type(data).__name__
        raise ValueError(f"{path}: ожидается YAML-маппинг имя: chat_id, получено {kind}")

    chats: dict[str, int] = {}
    for name, chat_id in data.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{path}: имя чата должно быть непустой строкой, получено {name!r}")
        # bool — подтип int в Python: true/false как chat_id отклоняем явно
        if isinstance(chat_id, bool) or not isinstance(chat_id, int):
            raise ValueError(f"{path}: chat_id для '{name}' должен быть int, получено {chat_id!r}")
        chats[name] = chat_id

    if not chats:
        raise ValueError(f"{path}: список чатов пуст")
    return chats
