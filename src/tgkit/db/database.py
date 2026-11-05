import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, cast

import aiosqlite

from tgkit.db.migrations import Migration, run_migrations

logger = logging.getLogger(__name__)

# Соединение активной транзакции для текущего async-контекста.
# None -> транзакция не открыта (режим чтения).
_tx_conn: ContextVar[aiosqlite.Connection | None] = ContextVar("_tx_conn", default=None)

# Ключевые слова мутаций. Используются как защита read-методов от случайной записи.
_WRITE_KEYWORDS = frozenset({"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP"})

# Размер read-пула по умолчанию. WAL допускает много конкурентных читателей; для SQLite
# 4-5 достаточно — больше упирается в диск, а не снимает блокировки.
_DEFAULT_READ_POOL_SIZE = 4


class Database:
    """
    Единая точка доступа к SQLite.

    Принципы:
      * Чтение (`fetch_all` / `fetch_one`) идёт через ПУЛ read-соединений и НЕ берёт блокировок.
        Каждый запрос арендует соединение из пула на своё время (checkout/checkin), поэтому
        параллельные читатели не стоят в очереди к одному соединению.
      * Запись (`execute` / `execute_insert`) разрешена ТОЛЬКО внутри `transaction()`.
      * `transaction()` — атомарный блок (BEGIN IMMEDIATE -> COMMIT / авто-ROLLBACK),
        сериализованный через write-lock. Вложенные вызовы переиспользуют внешнюю транзакцию.
      * Внутри транзакции чтение идёт на write-соединение (read-your-writes), а не в пул.

    Благодаря WAL чтение и запись не блокируют друг друга на уровне SQLite.
    """

    def __init__(
        self,
        db_path: str = "db_data/app.db",
        read_pool_size: int = _DEFAULT_READ_POOL_SIZE,
        migrations: list[Migration] | None = None,
    ) -> None:
        self._migrations = migrations or []
        self._db_path = Path(db_path)
        self._write_conn: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

        self._read_pool_size = max(1, read_pool_size)
        self._read_pool: asyncio.Queue[aiosqlite.Connection] | None = None
        self._read_conns: list[aiosqlite.Connection] = []

    # --- Жизненный цикл ---

    async def connect(self) -> None:
        """Открывает write-соединение и пул read-соединений. Идемпотентно."""
        if self._write_conn is not None:
            return

        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_conn = await self._open_connection()

        self._read_pool = asyncio.Queue()
        for _ in range(self._read_pool_size):
            conn = await self._open_connection()
            self._read_conns.append(conn)
            self._read_pool.put_nowait(conn)

        logger.info("База данных подключена: %s (read-пул: %d)", self._db_path, self._read_pool_size)

    async def _open_connection(self) -> aiosqlite.Connection:
        # isolation_level=None -> ручное управление транзакциями (явные BEGIN/COMMIT),
        # одиночные операторы выполняются в autocommit.
        conn = await aiosqlite.connect(self._db_path, timeout=10.0, isolation_level=None)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL;")
        await conn.execute("PRAGMA synchronous=NORMAL;")
        await conn.execute("PRAGMA foreign_keys=ON;")
        await conn.execute("PRAGMA busy_timeout=10000;")
        return conn

    async def disconnect(self) -> None:
        """Закрывает write-соединение и все соединения пула."""
        for conn in (self._write_conn, *self._read_conns):
            if conn is not None:
                await conn.close()
        self._write_conn = None
        self._read_conns = []
        self._read_pool = None

    async def run_migrations(self, migrations: list[Migration] | None = None) -> None:
        """Применяет миграции схемы. Вызывается один раз при старте, вне транзакций приложения.

        Список берётся из аргумента либо из переданного в конструктор: сами миграции
        доменные, каркас знает только как их прогонять.
        """
        await run_migrations(self._require(self._write_conn), migrations or self._migrations)

    # --- Транзакции (только для записи) ---

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """
        Атомарная транзакция на запись.

        Оборачивайте сюда блок, который должен выполниться целиком или не выполниться вовсе.
        Вложенные `transaction()` присоединяются к внешней — второго COMMIT не будет.

        ВНИМАНИЕ: не запускайте операции БД через `asyncio.gather` ВНУТРИ транзакции. Дочерние
        Task'и копируют контекст и увидят `_tx_conn` выставленным → конкурентное использование
        одного write-соединения. Внутри транзакции — только последовательные await.
        """
        existing = _tx_conn.get()
        if existing is not None:
            yield existing
            return

        conn = self._require(self._write_conn)
        async with self._write_lock:
            # BEGIN IMMEDIATE сразу резервирует write-lock SQLite,
            # исключая "database is locked" при конкурентной записи.
            await conn.execute("BEGIN IMMEDIATE")
            token = _tx_conn.set(conn)
            try:
                yield conn
                await conn.commit()
            except BaseException:
                await conn.rollback()
                raise
            finally:
                _tx_conn.reset(token)

    # --- Чтение (без блокировок, через пул) ---

    @asynccontextmanager
    async def _acquire_read(self) -> AsyncIterator[aiosqlite.Connection]:
        """Арендует соединение из read-пула на время одного запроса и возвращает его обратно."""
        if self._read_pool is None:
            raise RuntimeError("База данных не подключена. Сначала вызовите Database.connect().")
        conn = await self._read_pool.get()
        try:
            yield conn
        finally:
            self._read_pool.put_nowait(conn)

    async def fetch_all(self, query: str, params: tuple[Any, ...] = ()) -> list[aiosqlite.Row]:
        """Возвращает все строки. Работает и вне, и внутри транзакции."""
        self._guard_read(query)
        tx = _tx_conn.get()
        if tx is not None:
            async with tx.execute(query, params) as cursor:
                return cast(list[aiosqlite.Row], await cursor.fetchall())
        async with self._acquire_read() as conn, conn.execute(query, params) as cursor:
            return cast(list[aiosqlite.Row], await cursor.fetchall())

    async def fetch_one(self, query: str, params: tuple[Any, ...] = ()) -> aiosqlite.Row | None:
        """Возвращает первую строку или None."""
        self._guard_read(query)
        tx = _tx_conn.get()
        if tx is not None:
            async with tx.execute(query, params) as cursor:
                return await cursor.fetchone()
        async with self._acquire_read() as conn, conn.execute(query, params) as cursor:
            return await cursor.fetchone()

    # --- Запись (только внутри transaction()) ---

    async def execute(self, query: str, params: tuple[Any, ...] = ()) -> int:
        """Выполняет INSERT/UPDATE/DELETE и возвращает число затронутых строк."""
        conn = self._require_tx(query)
        async with conn.execute(query, params) as cursor:
            return cursor.rowcount

    async def execute_insert(self, query: str, params: tuple[Any, ...] = ()) -> int:
        """Выполняет INSERT и возвращает lastrowid."""
        conn = self._require_tx(query)
        async with conn.execute(query, params) as cursor:
            return cursor.lastrowid or 0

    # --- Внутреннее ---

    @staticmethod
    def _guard_read(query: str) -> None:
        head = query.split(None, 1)
        if head and head[0].upper() in _WRITE_KEYWORDS:
            raise RuntimeError(
                "fetch_*() предназначены только для чтения. Для мутаций используйте "
                "execute()/execute_insert() внутри `async with db.transaction()`."
            )

    @staticmethod
    def _require_tx(query: str) -> aiosqlite.Connection:
        conn = _tx_conn.get()
        if conn is None:
            raise RuntimeError(
                "Попытка записи вне транзакции. Оберните вызов в `async with db.transaction():`. "
                f"Запрос: {query.strip()[:60]}..."
            )
        return conn

    @staticmethod
    def _require(conn: aiosqlite.Connection | None) -> aiosqlite.Connection:
        if conn is None:
            raise RuntimeError("База данных не подключена. Сначала вызовите Database.connect().")
        return conn
