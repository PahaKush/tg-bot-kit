"""Мидлвари: каждая делает одну вещь и снимается по отдельности.

Порядок регистрации задаётся приложением и важен: CallbackAnswerMiddleware ставится
самой внешней, чтобы гасить «часики» даже когда мидлварь ниже отбросила апдейт, а
AbandonedFlowMiddleware — самой внутренней, чтобы закрывать сценарий только у
сматчившегося и разрешённого хендлера.

Параметризация. Каркас не знает ни о репозиториях приложения, ни о его перечислении
уровней доступа, поэтому и UserContextMiddleware, и AccessControlMiddleware получают
нужные типы и правила в конструкторе.

Сигнатуры `__call__` повторяют BaseMiddleware (`dict[str, Any]`): сужение до
AppMiddlewareData в параметре нарушает контракт базового класса. Типизированный доступ
к данным — через `cast(AppMiddlewareData, data)` внутри.
"""

import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from typing import Any, NotRequired, Protocol, Required, cast, runtime_checkable

from aiogram import BaseMiddleware
from aiogram.dispatcher.event.handler import HandlerObject
from aiogram.dispatcher.flags import get_flag
from aiogram.dispatcher.middlewares.data import MiddlewareData
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, TelegramObject, Update
from dishka import AsyncContainer

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class AppMiddlewareData(MiddlewareData, total=False):
    """Типизированный словарь данных мидлварей.

    Без него `data["is_admin"]` — просто строковый ключ, о котором знает только автор:
    опечатка находится в проде, а не в редакторе.
    """

    dishka_container: Required[AsyncContainer]
    is_admin: NotRequired[bool]
    admins: NotRequired[Sequence[int]]  # workflow_data диспетчера: Dispatcher(admins=[...])


@runtime_checkable
class UserStore(Protocol):
    """Минимум, который нужен UserContextMiddleware от репозитория пользователей."""

    async def get_user(self, user_id: int) -> dict[str, Any] | None: ...

    async def add_user(self, user_id: int, username: str, full_name: str) -> None: ...


@runtime_checkable
class Transactional(Protocol):
    def transaction(self) -> Any: ...


class LoggingMiddleware(BaseMiddleware):
    """Логирует входящие апдейты на уровне DEBUG."""

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        update = data.get("event_update")
        if isinstance(update, Update):
            if update.message:
                logging.debug("Update %s | Message: %s", update.update_id, update.message.text or "<media>")
            elif update.callback_query:
                logging.debug("Update %s | Callback: %s", update.update_id, update.callback_query.data)
        return await handler(event, data)


class UserContextMiddleware(BaseMiddleware):
    """Держит профиль пользователя актуальным и выставляет is_admin.

    Запись идёт ТОЛЬКО когда что-то изменилось: иначе каждый апдейт открывал бы
    транзакцию ради UPDATE с теми же значениями.

    store_type/db_type — классы, которые приложение зарегистрировало в dishka.
    """

    def __init__(self, store_type: type[UserStore], db_type: type[Transactional]):
        self.store_type = store_type
        self.db_type = db_type

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        typed = cast(AppMiddlewareData, data)
        user = typed.get("event_from_user")
        if not user:
            return None

        container = typed["dishka_container"]
        store = await container.get(self.store_type)
        db = await container.get(self.db_type)

        stored = await store.get_user(user.id)
        username, full_name = user.username or "", user.full_name or ""

        if stored is None or stored.get("username") != username or stored.get("full_name") != full_name:
            async with db.transaction():
                await store.add_user(user.id, username, full_name)

        typed["is_admin"] = user.id in typed.get("admins", [])
        return await handler(event, data)


# Решение по уровню доступа, который каркас сам разобрать не может (владение ресурсом).
# Возвращает True — пропустить хендлер; False — отказать. Волен дописать data.
ResourceResolver = Callable[[str, TelegramObject, AppMiddlewareData], Awaitable[bool]]


class AccessControlMiddleware(BaseMiddleware):
    """Проверяет право нажать коллбэк по ЯВНОЙ разметке на самом CallbackData.

    Deny-by-default: коллбэк без атрибута `access` ОТВЕРГАЕТСЯ. Это дороже ровно один
    раз — при добавлении нового коллбэка, — и дешевле навсегда потом: забыть проверку
    нельзя, потому что забывание видно сразу.

    Почему так. У неявного правила («проверим, если у callback_data окажется нужное
    поле») два независимых провала:
      1. новый коллбэк без этого поля молча теряет проверку целиком;
      2. проверка ОДНОГО идентификатора создаёт ложное чувство защищённости, и
         соседний уходит в SQL без скоупа — так появляется cross-tenant IDOR.

    Чего эта мидлварь НЕ делает: не проверяет владение конкретным ресурсом. Его нельзя
    проверить, не зная предметной области, поэтому такие уровни отдаются `resolver`,
    а скоуп владения всё равно обязан стоять в WHERE каждого запроса.
    """

    def __init__(
        self,
        *,
        admin_level: str = "admin",
        open_levels: Sequence[str] = ("public", "user", "handler"),
        resolver: ResourceResolver | None = None,
        unmarked_text: str = "⚠️ Действие недоступно. Обновите меню: /start",
        denied_text: str = "Недостаточно прав.",
    ):
        self.admin_level = admin_level
        self.open_levels = set(open_levels)
        self.resolver = resolver
        self.unmarked_text = unmarked_text
        self.denied_text = denied_text

    @staticmethod
    async def _deny(event: TelegramObject, text: str, *, reason: str, **ctx: Any) -> None:
        logging.warning("🔒 ОТКАЗ В ДОСТУПЕ (%s): %s", reason, ctx)
        if isinstance(event, CallbackQuery):
            with suppress(Exception):
                await event.answer(text, show_alert=True)

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        cb_data = data.get("callback_data")

        # Строковые коллбэки (F.data == "menu") и catch-all не несут CallbackData:
        # такие экраны скоупятся по event_from_user.id внутри самих хендлеров.
        if cb_data is None:
            return await handler(event, data)

        access = getattr(type(cb_data), "access", None)
        user = data.get("event_from_user")
        cb_name = type(cb_data).__name__

        if access is None:
            await self._deny(
                event,
                self.unmarked_text,
                reason="коллбэк не объявил access",
                cb=cb_name,
                user=getattr(user, "id", None),
            )
            return None

        level = str(access)

        if level in self.open_levels:
            return await handler(event, data)

        if level == self.admin_level:
            if not data.get("is_admin"):
                await self._deny(event, self.denied_text, reason="не админ", cb=cb_name, user=getattr(user, "id", None))
                return None
            return await handler(event, data)

        if self.resolver is None:
            await self._deny(event, self.unmarked_text, reason=f"уровень {level!r} некому разобрать", cb=cb_name)
            return None

        if not await self.resolver(level, event, cast(AppMiddlewareData, data)):
            return None  # resolver сам отвечает пользователю: только он знает, что именно не так
        return await handler(event, data)


class ValidCallbackMiddleware(BaseMiddleware):
    """Отсекает недоступные сообщения и кладёт валидный Message как `callback_msg`.

    Без этого каждый callback-хендлер начинался бы с проверки isinstance: у слишком
    старых сообщений Telegram отдаёт InaccessibleMessage, у которого нет ни текста,
    ни возможности правки.
    """

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        if isinstance(event, CallbackQuery) and isinstance(event.message, Message):
            data["callback_msg"] = event.message
            return await handler(event, data)
        return None


class ThrottlingMiddleware(BaseMiddleware):
    """Антиспам с посекундным окном на пользователя.

    Настраивается на конкретном хендлере флагом `@flags.rate_limit(2.0)`, а `False`
    отключает лимит совсем — это нужно для длинных операций, где второй клик осмыслен.

    Один инстанс на callback+message осознанно: иначе клик и сообщение за <0.5 с
    проходят оба и конкурентно трогают FSM (потеря записи в update_data). Это
    митигация, а не полная защита — настоящий фикс гонок FSM это per-chat lock.
    """

    def __init__(self, default_rate_limit: float = 0.5, gc_threshold: int = 5000):
        self.default_rate_limit = default_rate_limit
        self.gc_threshold = gc_threshold
        self.users: dict[int, float] = {}

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        user = data.get("event_from_user")
        if not user:
            return await handler(event, data)

        rate_limit = get_flag(data, "rate_limit")
        if rate_limit is False:
            return await handler(event, data)

        limit = rate_limit if isinstance(rate_limit, int | float) else self.default_rate_limit
        now = time.time()

        if user.id in self.users and now < self.users[user.id]:
            if isinstance(event, CallbackQuery):
                with suppress(Exception):
                    await event.answer()  # гасим «часики» у отброшенного клика
            return None

        self.users[user.id] = now + limit

        if len(self.users) > self.gc_threshold:
            self.users = {k: v for k, v in self.users.items() if v > now}

        return await handler(event, data)


class CallbackAnswerMiddleware(BaseMiddleware):
    """Подстраховка ответа на callback: гасит «часики» на любом пути, даже если хендлер
    забыл .answer().

    Хендлеры, которые отвечают сами (рано или с алертом), не страдают: их ответ уже
    прошёл, а наш повторный молча гасится.

    Важно: на ИСКЛЮЧЕНИИ мы НЕ отвечаем — строка с answer недостижима, исключение летит
    выше, и alert показывает error-роутер. (Ответили бы в finally — error-роутер уже не
    достучался бы со своим сообщением.) Регистрируется самой внешней callback-мидлварью,
    поэтому страхует и short-circuit'ы мидлварей ниже.
    """

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        result = await handler(event, data)
        if isinstance(event, CallbackQuery):
            with suppress(Exception):
                await event.answer()
        return result


def bound_to_state(handler: HandlerObject) -> bool:
    """Хендлер — шаг сценария: среди его фильтров есть состояние FSM."""
    for flt in handler.filters or ():
        cb = flt.callback
        if isinstance(cb, State | StatesGroup | StateFilter):
            return True
        if isinstance(cb, type) and issubclass(cb, StatesGroup):
            return True
    return False


class AbandonedFlowMiddleware(BaseMiddleware):
    """Любой хендлер, не привязанный к состоянию, сначала закрывает брошенный сценарий.

    Кнопки «Отмена ❌» и «⬅ Назад» обычно ведут на те же экраны, что и меню, а те
    состояние не трогают. Бот остаётся в ожидании ввода, и следующее же сообщение
    срабатывает как шаг сценария: создаёт то, от чего пользователь только что отказался.
    То же — после команды посреди сценария.

    Помнить про state.clear() в каждом экране ненадёжно, поэтому правило структурное:
    шаги сценария помечены фильтром состояния, всё остальное — навигация. Флаг
    `keep_state` — для catch-all хендлеров, которым незачем рвать сценарий (декоративная
    кнопка, устаревшее меню, подсказка «жду другое»).

    Следствие для приложения: хендлер, который ЧИТАЕТ пейлоад сценария, обязан стоять
    под фильтром состояния — иначе пейлоад будет стёрт до его вызова.

    Регистрируется inner-мидлварью ПОСЛЕДНЕЙ на callback_query и message: её зовут только
    для сматчившегося хендлера (он уже известен), и только если доступ разрешён —
    отказ в доступе или клик, отброшенный троттлингом, не должны рвать чужой ввод.
    """

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        state: FSMContext | None = data.get("state")
        handler_obj: HandlerObject | None = data.get("handler")
        if (
            state is not None
            and handler_obj is not None
            and not get_flag(data, "keep_state")
            and not bound_to_state(handler_obj)
            and await state.get_state() is not None
        ):
            await state.clear()
        return await handler(event, data)
