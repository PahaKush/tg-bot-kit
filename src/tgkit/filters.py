"""Фильтры роутера."""

from collections.abc import Sequence

from aiogram.filters import Filter
from aiogram.types import TelegramObject, User


class IsAdmin(Filter):
    """Хендлер виден только админам из workflow_data диспетчера: `Dispatcher(admins=[...])`.

    Читает `admins`, а не `is_admin`: его ставит inner-мидлварь, а фильтры работают раньше.
    """

    async def __call__(
        self, event: TelegramObject, event_from_user: User | None = None, admins: Sequence[int] = ()
    ) -> bool:
        return event_from_user is not None and event_from_user.id in admins
