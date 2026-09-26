"""Chat search — Telegram chat/channel downloader."""

from chat_search.tg_client import get_credentials as get_credentials
from chat_search.tg_client import make_client as make_client

__all__ = [
    "get_credentials",
    "make_client",
]
