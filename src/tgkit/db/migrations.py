"""Прогон версионированных миграций.

В каркас вынесен только РАННЕР — сами миграции доменные и живут в приложении.
Приложение собирает список и передаёт его в run_migrations.

Каждая запись: {"version": int, "name": str} плюс "sql" и/или "python_func".
SQL-миграции обязаны быть идемпотентными (IF NOT EXISTS / OR IGNORE), потому что
executescript делает неявный COMMIT: откатить половину применённого скрипта нельзя,
и при падении версия не запишется — значит миграция прогонится снова.
Для неатомарных изменений используйте "python_func" и управляйте транзакцией внутри.
"""

import contextlib
import logging
from typing import Any, TypedDict

import aiosqlite

logger = logging.getLogger(__name__)


class Migration(TypedDict, total=False):
    version: int
    name: str
    sql: str
    python_func: Any  # async callable(conn)


async def run_migrations(conn: aiosqlite.Connection, migrations: list[Migration]) -> None:
    """Применяет недостающие миграции.

    Соединение должно быть в autocommit-режиме (isolation_level=None) и вне транзакции
    приложения. Каждая миграция фиксируется отдельно; уже применённые пропускаются.
    """
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_versions ("
        "version INTEGER PRIMARY KEY, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )

    async with conn.execute("SELECT version FROM schema_versions") as cursor:
        applied = {row[0] for row in await cursor.fetchall()}

    pending = sorted((m for m in migrations if m["version"] not in applied), key=lambda m: m["version"])
    if not pending:
        logger.info("Миграции БД: схема актуальна (применено версий: %d).", len(applied))
        return

    for migration in pending:
        version = migration["version"]
        name = migration.get("name", "unnamed")
        logger.info("Применяю миграцию v%d: %s...", version, name)

        # Номер версии пишется в ТОЙ ЖЕ транзакции, что и сама миграция: иначе падение
        # между ними оставляет схему изменённой, но «непримененной», и следующий старт
        # накатывает её повторно.
        #
        # Оговорка, из-за которой идемпотентность SQL-миграций всё равно обязательна:
        # executescript в sqlite3 делает неявный COMMIT перед запуском скрипта, поэтому
        # для ветки "sql" этот BEGIN заканчивается там же, и откатывать будет нечего.
        # Полную атомарность даёт только "python_func".
        await conn.execute("BEGIN")
        try:
            if sql := migration.get("sql"):
                await conn.executescript(sql)
            if func := migration.get("python_func"):
                await func(conn)
            await conn.execute("INSERT INTO schema_versions (version) VALUES (?)", (version,))
            await conn.commit()
        except Exception:
            with contextlib.suppress(Exception):
                await conn.rollback()
            logger.critical("Миграция v%d (%s) провалена — откат.", version, name, exc_info=True)
            raise
        logger.info("Миграция v%d применена.", version)
