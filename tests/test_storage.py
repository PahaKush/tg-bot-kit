"""SQLiteStorage: ключ записи и обслуживание файла.

Ключ — полный StorageKey. Ключ по одному chat_id работает в приватном чате (там
chat_id == user_id), а в группе один chat_id общий для всех участников: записи
собеседников ложились бы в одну строку и затирали друг друга.
"""

import asyncio
import time

import aiosqlite
import pytest
from aiogram.fsm.storage.base import StorageKey

from tgkit.storage import STALE_AFTER_SECONDS, SQLiteStorage

GROUP_CHAT = -100_500  # chat_id группы не равен ни одному user_id


def _key(*, chat_id: int, user_id: int, bot_id: int = 1, destiny: str = "default") -> StorageKey:
    return StorageKey(bot_id=bot_id, chat_id=chat_id, user_id=user_id, destiny=destiny)


@pytest.fixture
def storage(tmp_path):
    return SQLiteStorage(str(tmp_path / "fsm.db"))


async def test_same_chat_different_users_do_not_collide(storage):
    """Групповой чат: два участника, один chat_id — второй не затирает первого."""
    alice, bob = _key(chat_id=GROUP_CHAT, user_id=111), _key(chat_id=GROUP_CHAT, user_id=222)

    await storage.set_state(alice, "Form:title")
    await storage.set_data(alice, {"form": {"title": "Alice"}})
    await storage.set_state(bob, "Form:body")
    await storage.set_data(bob, {"form": {"title": "Bob"}})

    assert await storage.get_state(alice) == "Form:title"
    assert await storage.get_state(bob) == "Form:body"
    assert await storage.get_data(alice) == {"form": {"title": "Alice"}}
    assert await storage.get_data(bob) == {"form": {"title": "Bob"}}
    await storage.close()


async def test_clearing_one_user_leaves_the_other(storage):
    alice, bob = _key(chat_id=GROUP_CHAT, user_id=111), _key(chat_id=GROUP_CHAT, user_id=222)
    await storage.set_state(alice, "A:one")
    await storage.set_state(bob, "B:two")

    await storage.set_state(alice, None)

    assert await storage.get_state(alice) is None
    assert await storage.get_state(bob) == "B:two"
    await storage.close()


async def test_bot_id_and_destiny_are_part_of_the_key(storage):
    """Разные боты и разные destiny — независимые записи."""
    base = _key(chat_id=777, user_id=777)
    other_bot = _key(chat_id=777, user_id=777, bot_id=2)
    other_destiny = _key(chat_id=777, user_id=777, destiny="scene")

    await storage.set_state(base, "S:base")
    await storage.set_state(other_bot, "S:bot2")
    await storage.set_state(other_destiny, "S:scene")

    assert await storage.get_state(base) == "S:base"
    assert await storage.get_state(other_bot) == "S:bot2"
    assert await storage.get_state(other_destiny) == "S:scene"
    await storage.close()


async def test_table_with_chat_id_key_is_recreated(tmp_path):
    """Файл, где таблицы ключуются одним chat_id, не роняет бота: таблицы пересоздаются."""
    db_file = tmp_path / "fsm.db"
    async with aiosqlite.connect(db_file) as conn:
        await conn.execute("CREATE TABLE states (chat_id INTEGER PRIMARY KEY, state TEXT);")
        await conn.execute("CREATE TABLE data (chat_id INTEGER PRIMARY KEY, data TEXT);")
        await conn.execute("INSERT INTO states VALUES (1, 'Old:state');")
        await conn.commit()

    storage = SQLiteStorage(str(db_file))
    key = _key(chat_id=1, user_id=1)
    await storage.set_state(key, "New:state")
    assert await storage.get_state(key) == "New:state"
    await storage.close()


async def test_stale_records_are_dropped_on_open(tmp_path):
    """Брошенные сценарии не копятся вечно — иначе fsm.db только растёт."""
    db_file = tmp_path / "fsm.db"
    storage = SQLiteStorage(str(db_file))
    fresh, ancient = _key(chat_id=1, user_id=1), _key(chat_id=2, user_id=2)
    await storage.set_state(fresh, "Fresh:state")
    await storage.set_state(ancient, "Ancient:state")
    await storage.close()

    # Состариваем одну запись руками — ждать неделю в тесте нечем.
    async with aiosqlite.connect(db_file) as conn:
        await conn.execute(
            "UPDATE states SET updated_at = ? WHERE chat_id = 2",
            (int(time.time()) - STALE_AFTER_SECONDS - 1,),
        )
        await conn.commit()

    reopened = SQLiteStorage(str(db_file))
    assert await reopened.get_state(fresh) == "Fresh:state"
    assert await reopened.get_state(ancient) is None
    await reopened.close()


async def test_concurrent_first_use_opens_one_connection(storage):
    """Без лока конкурентные апдейты на холодном старте открыли бы каждый своё соединение,
    и лишние остались бы висеть незакрытыми."""
    conns = await asyncio.gather(*(storage._get_conn() for _ in range(8)))

    assert len({id(c) for c in conns}) == 1
    await storage.close()
