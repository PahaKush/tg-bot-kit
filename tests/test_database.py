import pytest

from tgkit.db.database import Database


@pytest.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "app.db"))
    await database.connect()
    async with database.transaction():
        await database.execute("CREATE TABLE items (name TEXT)")
    yield database
    await database.disconnect()


async def test_write_outside_transaction_is_refused(db):
    with pytest.raises(RuntimeError, match="вне транзакции"):
        await db.execute("INSERT INTO items VALUES ('x')")


async def test_fetch_refuses_writes(db):
    with pytest.raises(RuntimeError, match="только для чтения"):
        await db.fetch_all("DELETE FROM items")


async def test_error_rolls_back_the_whole_block(db):
    with pytest.raises(ValueError, match="boom"):
        async with db.transaction():
            await db.execute("INSERT INTO items VALUES ('lost')")
            raise ValueError("boom")

    assert await db.fetch_all("SELECT * FROM items") == []


async def test_reads_inside_transaction_see_its_writes(db):
    async with db.transaction():
        await db.execute("INSERT INTO items VALUES ('new')")
        row = await db.fetch_one("SELECT count(*) FROM items")

    assert row[0] == 1


async def test_two_databases_do_not_share_a_transaction(tmp_path):
    first, second = Database(str(tmp_path / "a.db")), Database(str(tmp_path / "b.db"))
    for database in (first, second):
        await database.connect()
    async with second.transaction():
        await second.execute("CREATE TABLE only_in_second (x)")

    async with first.transaction():
        row = await second.fetch_one("SELECT count(*) FROM sqlite_master WHERE name = 'only_in_second'")

    assert row[0] == 1
    for database in (first, second):
        await database.disconnect()
