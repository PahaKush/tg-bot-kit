import enum
from types import SimpleNamespace
from typing import ClassVar

from aiogram.filters.callback_data import CallbackData
from aiogram.types import User

from tgkit.middlewares import AccessControlMiddleware, ThrottlingMiddleware

USER = User(id=42, is_bot=False, first_name="U")


class Level(enum.Enum):
    USER = "user"
    ADMIN = "admin"


class UserCB(CallbackData, prefix="usr"):
    access: ClassVar[Level] = Level.USER


class AdminCB(CallbackData, prefix="adm"):
    access: ClassVar[Level] = Level.ADMIN


async def test_rate_limit_flag_without_number_uses_default():
    """`@flags.rate_limit` без числа даёт True, а True — это int: лимит стал бы 1 с."""
    middleware = ThrottlingMiddleware(default_rate_limit=0)
    data = {"event_from_user": USER, "handler": SimpleNamespace(flags={"rate_limit": True})}
    calls = []

    async def handler(event, data):
        calls.append(event)

    await middleware(handler, "first", data)
    await middleware(handler, "second", data)

    assert calls == ["first", "second"]


async def test_plain_enum_levels_are_compared_by_value():
    middleware = AccessControlMiddleware(admin_level=Level.ADMIN, open_levels=(Level.USER,))
    passed = []

    async def handler(event, data):
        passed.append(type(data["callback_data"]).__name__)

    await middleware(handler, object(), {"callback_data": UserCB()})
    await middleware(handler, object(), {"callback_data": AdminCB(), "is_admin": True})
    await middleware(handler, object(), {"callback_data": AdminCB(), "is_admin": False})

    assert passed == ["UserCB", "AdminCB"]
