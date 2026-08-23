from sqlalchemy import text

from platform_core.db import Database


async def test_check_passes(pg_url):
    db = Database(pg_url)
    try:
        await db.check()  # raises if unreachable
    finally:
        await db.dispose()


async def test_session_roundtrip(pg_url):
    db = Database(pg_url)
    try:
        async with db.engine.begin() as conn:
            await conn.execute(text("CREATE TEMP TABLE t_ping (id int primary key, note text)"))
            await conn.execute(text("INSERT INTO t_ping (id, note) VALUES (1, 'hello')"))
            note = (await conn.execute(text("SELECT note FROM t_ping WHERE id = 1"))).scalar_one()
            assert note == "hello"

        async with db.session() as session:
            assert (await session.execute(text("SELECT 1"))).scalar_one() == 1
    finally:
        await db.dispose()
