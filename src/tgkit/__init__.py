"""tgkit — каркас Telegram-бота на aiogram 3.

Из корня пакета доступно самое ходовое: база, хранилище FSM, пейлоады, ошибки и
шифрование. Мидлвари, клавиатуры, экран ошибки и утилиты — в своих модулях
(tgkit.middlewares, tgkit.kb, tgkit.utils.*).
"""

from tgkit.core.exceptions import (
    AccessDeniedError,
    AppError,
    NotFoundError,
    UserInputError,
    safe_error_text,
)
from tgkit.core.state import FlowPayload, StateHelper
from tgkit.crypto import CryptoService, decrypt_fields, encrypt_fields
from tgkit.db.database import Database
from tgkit.db.migrations import Migration, run_migrations
from tgkit.storage import SQLiteStorage

__all__ = [
    "AccessDeniedError",
    "AppError",
    "CryptoService",
    "Database",
    "FlowPayload",
    "Migration",
    "NotFoundError",
    "SQLiteStorage",
    "StateHelper",
    "UserInputError",
    "decrypt_fields",
    "encrypt_fields",
    "run_migrations",
    "safe_error_text",
]
