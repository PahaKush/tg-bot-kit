import asyncio
import json
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import aiosqlite
from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey

# Записи старше этого срока — мусор от брошенных мастеров: пользователь ушёл с половины
# флоу и не вернулся. Без очистки fsm.db растёт монотонно и никогда не уменьшается.
STALE_AFTER_SECONDS = 7 * 24 * 60 * 60

_KEY_COLUMNS = ("bot_id", "chat_id", "user_id", "destiny")
_KEY_WHERE = " AND ".join(f"{c} = ?" for c in _KEY_COLUMNS)


class SQLiteStorage(BaseStorage):
    """
    Асинхронный FSM Storage на базе aiosqlite.
    Хранит состояния в отдельной БД, чтобы не блокировать основную логику.

    Ключ записи — полный `StorageKey` (bot_id, chat_id, user_id, destiny), а не один
    chat_id. В приватном чате chat_id == user_id, поэтому разницы не видно; в группе один
    chat_id общий для всех участников, и с ключом по нему одному состояния собеседников
    схлопываются в одну строку: любой участник затирает незаконченный мастер другого.
    """

    def __init__(self, database_name: str = "db_data/fsm.db") -> None:
        self.db_path = Path(database_name)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: aiosqlite.Connection | None = None
        # Без лока два одновременных апдейта на холодном старте оба видят _conn is None,
        # оба открывают соединение, и одно из них остаётся висеть незакрытым.
        self._lock = asyncio.Lock()

    async def _get_conn(self) -> aiosqlite.Connection:
        """Ленивая инициализация постоянного подключения."""
        if self._conn is not None:
            return self._conn

        async with self._lock:
            if self._conn is not None:  # мог успеть создать тот, кто держал лок до нас
                return self._conn

            # Автокоммит: каждый оператор хранилища атомарен сам по себе.
            conn = await aiosqlite.connect(self.db_path, isolation_level=None)
            await conn.execute("PRAGMA journal_mode=WAL;")
            await conn.execute("PRAGMA synchronous=NORMAL;")

            await self._ensure_schema(conn)
            await self._drop_stale(conn)

            self._conn = conn
            return conn

    @staticmethod
    async def _ensure_schema(conn: aiosqlite.Connection) -> None:
        """Создаёт таблицы; если в файле таблицы с другим ключом (PK по одному chat_id), пересоздаёт их.

        Мигрировать нечего: в FSM лежат только незаконченные мастера, и они всё равно не
        переживают перезапуск бота осмысленно. Дописать колонку в PRIMARY KEY SQLite не
        даёт, поэтому честнее пересоздать таблицу, чем тащить половинчатую схему.
        """
        for table in ("states", "data"):
            async with conn.execute(f"PRAGMA table_info({table})") as cursor:
                columns = {row[1] for row in await cursor.fetchall()}
            if columns and not set(_KEY_COLUMNS).issubset(columns):
                await conn.execute(f"DROP TABLE {table}")

        await conn.execute(
            """CREATE TABLE IF NOT EXISTS states (
                bot_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                destiny TEXT NOT NULL,
                state TEXT NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (bot_id, chat_id, user_id, destiny)
            );"""
        )
        await conn.execute(
            """CREATE TABLE IF NOT EXISTS data (
                bot_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                destiny TEXT NOT NULL,
                data TEXT NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (bot_id, chat_id, user_id, destiny)
            );"""
        )

    @staticmethod
    async def _drop_stale(conn: aiosqlite.Connection) -> None:
        threshold = int(time.time()) - STALE_AFTER_SECONDS
        for table in ("states", "data"):
            await conn.execute(f"DELETE FROM {table} WHERE updated_at < ?", (threshold,))

    @staticmethod
    def _key(key: StorageKey) -> tuple[int, int, int, str]:
        return (key.bot_id, key.chat_id, key.user_id, key.destiny)

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        conn = await self._get_conn()
        if state is None:
            await conn.execute(f"DELETE FROM states WHERE {_KEY_WHERE}", self._key(key))
        else:
            state_str = cast(str, state.state if isinstance(state, State) else state)
            await conn.execute(
                "REPLACE INTO states (bot_id, chat_id, user_id, destiny, state, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (*self._key(key), state_str, int(time.time())),
            )

    async def get_state(self, key: StorageKey) -> str | None:
        conn = await self._get_conn()
        async with conn.execute(f"SELECT state FROM states WHERE {_KEY_WHERE}", self._key(key)) as cursor:
            result = await cursor.fetchone()
            return cast(str | None, result[0]) if result else None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        conn = await self._get_conn()
        if not data:
            await conn.execute(f"DELETE FROM data WHERE {_KEY_WHERE}", self._key(key))
        else:
            await conn.execute(
                "REPLACE INTO data (bot_id, chat_id, user_id, destiny, data, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (*self._key(key), json.dumps(dict(data)), int(time.time())),
            )

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        conn = await self._get_conn()
        async with conn.execute(f"SELECT data FROM data WHERE {_KEY_WHERE}", self._key(key)) as cursor:
            result = await cursor.fetchone()
            return json.loads(result[0]) if result else {}

    async def close(self) -> None:
        async with self._lock:
            if self._conn:
                await self._conn.close()
                self._conn = None
