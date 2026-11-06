"""Экран ошибки с сохранением навигации.

Глобальный роутер ошибок умеет только алерт: он не знает, на какой экран возвращать
пользователя, и на всякий случай чистит FSM. Поэтому действия, у которых есть осмысленное
«назад», ловят ошибку у себя, и этот блок, иначе повторяемый в каждом хендлере, живёт здесь.

Исключение НЕ пробрасывается дальше: иначе поверх экрана ошибки прилетел бы ещё и алерт
глобального роутера.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from aiogram.exceptions import TelegramBadRequest
from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup, Message

from tgkit.core.exceptions import safe_error_text
from tgkit.kb import back_btn

# Куда писать ответ. Разные вызовы отвечают по-разному (правка текущего экрана против
# нового сообщения), поэтому способ ответа передаётся, а не угадывается.
Respond = Callable[[str, InlineKeyboardMarkup | None], Awaitable[Any]]

# Обычно это цель для back_btn, но иногда экран ошибки предлагает не «назад», а «повторить» —
# тогда передаётся готовая клавиатура.
BackTarget = str | CallbackData | InlineKeyboardMarkup


class ErrorScreen:
    """Итог блока. `failed` нужен там, где после ошибки флоу обязан прерваться.

    Без него пришлось бы оставлять сырой `try/except` ради одного `return`: проглотить
    ошибку и поехать дальше нельзя — следующая строка работает с результатом, которого нет.
    """

    __slots__ = ("failed",)

    def __init__(self) -> None:
        self.failed = False


def edit(message: Message) -> Respond:
    """Показать ошибку правкой текущего сообщения — обычный путь callback-хендлера."""
    return lambda text, markup: message.edit_text(text, reply_markup=markup)


def reply(message: Message) -> Respond:
    """Показать ошибку новым сообщением — путь message-хендлера (править нечего)."""
    return lambda text, markup: message.answer(text, reply_markup=markup)


@asynccontextmanager
async def error_screen(respond: Respond, fallback: str, back: BackTarget | None = None) -> AsyncIterator[ErrorScreen]:
    """Ошибка внутри блока → безопасный текст и кнопка «назад» вместо падения флоу.

    `fallback` показывается только для НЕ-доменных ошибок; у AppError и наследников
    берётся их собственный user_message (см. safe_error_text). `back=None` — для мест,
    где возвращать некуда и достаточно текста.

    «message is not modified» — не ошибка: экран уже такой, каким его хотели нарисовать.
    """
    screen = ErrorScreen()
    try:
        yield screen
    except Exception as e:
        if _not_modified(e):
            return
        screen.failed = True
        if back is None:
            markup = None
        elif isinstance(back, InlineKeyboardMarkup):
            markup = back
        else:
            markup = back_btn(back)
        try:
            await respond(safe_error_text(e, fallback), markup)
        except TelegramBadRequest as err:
            if not _not_modified(err):
                raise


def _not_modified(e: BaseException) -> bool:
    return isinstance(e, TelegramBadRequest) and "message is not modified" in str(e)
