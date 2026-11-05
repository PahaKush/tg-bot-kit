"""Работа с FSM-пейлоадами на стороне хендлера."""

from contextlib import suppress

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from tgkit.core.state import FlowPayload, StateHelper


async def load_payload[T: FlowPayload](
    state: FSMContext,
    cls: type[T],
    event: Message | CallbackQuery,
    *,
    error: str = "❌ Контекст потерян. Начните заново: /start",
    clear: bool = True,
) -> T | None:
    """Грузит FSM-пейлоад; если его нет — сам сообщает пользователю и возвращает None.

        payload = await load_payload(state, MyPayload, message)
        if payload is None:
            return

    Без хелпера каждый шаг повторял бы одно и то же: загрузить, проверить на None,
    ответить в правильный канал (alert для кнопки, сообщение для текста), почистить стейт.
    Контекст теряется штатно — например, после /start посреди сценария.
    """
    payload = await StateHelper.load(state, cls)
    if payload is not None:
        return payload

    if isinstance(event, CallbackQuery):
        with suppress(Exception):
            await event.answer(error, show_alert=True)
    else:
        await event.answer(error)

    if clear:
        await state.clear()
    return None
