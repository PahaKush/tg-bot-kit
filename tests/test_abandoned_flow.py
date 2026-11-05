"""AbandonedFlowMiddleware: навигация закрывает брошенный сценарий.

Сценарий, ради которого она есть: «Отмена ❌» ведёт на обычный экран, бот остаётся
в ожидании текста, и следующее сообщение срабатывает как ввод сценария.
Проверяется на настоящем aiogram-диспетчере: важна не сама мидлварь, а то, как она
видит фильтры и флаги сматчившегося хендлера.
"""

import datetime

import pytest
from aiogram import Bot, Dispatcher, F, Router, flags
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from tgkit.middlewares import AbandonedFlowMiddleware, bound_to_state

USER = User(id=42, is_bot=False, first_name="Owner")
CHAT = Chat(id=42, type="private")
_NOW = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)


class Flow(StatesGroup):
    waiting_text = State()


def _message(text: str, update_id: int) -> Update:
    return Update(
        update_id=update_id,
        message=Message(message_id=update_id, date=_NOW, chat=CHAT, from_user=USER, text=text),
    )


def _callback(data: str, update_id: int) -> Update:
    host = Message(message_id=1, date=_NOW, chat=CHAT, from_user=USER, text="screen")
    return Update(
        update_id=update_id,
        callback_query=CallbackQuery(id=str(update_id), from_user=USER, chat_instance="ci", data=data, message=host),
    )


@pytest.fixture
def harness():
    calls: list[str] = []
    router = Router()

    @router.callback_query(F.data == "start_flow")
    async def start_flow(callback: CallbackQuery, state: FSMContext) -> None:
        await state.update_data(payload="draft")
        await state.set_state(Flow.waiting_text)
        calls.append("start_flow")

    @router.callback_query(Flow.waiting_text, F.data == "flow_button")
    async def flow_button(callback: CallbackQuery) -> None:
        calls.append("flow_button")

    @router.callback_query(StateFilter(Flow.waiting_text), F.data == "flow_button_sf")
    async def flow_button_state_filter(callback: CallbackQuery) -> None:
        calls.append("flow_button_sf")

    @router.callback_query(F.data == "screen")
    async def navigation_screen(callback: CallbackQuery) -> None:
        calls.append("screen")

    @router.callback_query()
    @flags.keep_state
    async def stale(callback: CallbackQuery) -> None:
        calls.append("stale")

    @router.message(Command("menu"))
    async def command(message: Message) -> None:
        calls.append("command")

    @router.message(Flow.waiting_text)
    async def flow_input(message: Message) -> None:
        calls.append(f"input:{message.text}")

    @router.message()
    async def unknown(message: Message) -> None:
        calls.append("unknown")

    dp = Dispatcher(storage=MemoryStorage())
    middleware = AbandonedFlowMiddleware()
    dp.callback_query.middleware(middleware)
    dp.message.middleware(middleware)
    dp.include_router(router)
    return dp, Bot(token="42:TEST"), calls, router


async def _state(dp: Dispatcher, bot: Bot) -> tuple[str | None, dict]:
    ctx = dp.fsm.get_context(bot, chat_id=CHAT.id, user_id=USER.id)
    return await ctx.get_state(), await ctx.get_data()


async def test_cancel_to_screen_ends_the_flow(harness):
    dp, bot, calls, _ = harness
    await dp.feed_update(bot, _callback("start_flow", 1))
    await dp.feed_update(bot, _callback("screen", 2))  # «Отмена ❌» ведёт на обычный экран

    assert await _state(dp, bot) == (None, {})  # и состояние, и пейлоад
    await dp.feed_update(bot, _message("test", 3))
    assert calls == ["start_flow", "screen", "unknown"]  # текст не ушёл в шаг сценария


async def test_steps_of_the_flow_keep_it(harness):
    """Шаг узнаётся и по State, и по StateFilter."""
    dp, bot, calls, _ = harness
    await dp.feed_update(bot, _callback("start_flow", 1))
    await dp.feed_update(bot, _callback("flow_button", 2))
    await dp.feed_update(bot, _callback("flow_button_sf", 3))
    await dp.feed_update(bot, _message("name", 4))

    assert calls == ["start_flow", "flow_button", "flow_button_sf", "input:name"]
    assert (await _state(dp, bot))[0] == Flow.waiting_text.state


async def test_keep_state_flag_does_not_end_the_flow(harness):
    dp, bot, calls, _ = harness
    await dp.feed_update(bot, _callback("start_flow", 1))
    await dp.feed_update(bot, _callback("some_dead_button", 2))

    assert calls == ["start_flow", "stale"]
    assert await _state(dp, bot) == (Flow.waiting_text.state, {"payload": "draft"})


async def test_command_in_the_middle_ends_the_flow(harness):
    dp, bot, calls, _ = harness
    await dp.feed_update(bot, _callback("start_flow", 1))
    await dp.feed_update(bot, _message("/menu", 2))
    await dp.feed_update(bot, _message("test", 3))

    assert calls == ["start_flow", "command", "unknown"]
    assert await _state(dp, bot) == (None, {})


def test_bound_to_state_reads_handler_filters(harness):
    _, _, _, router = harness
    by_name = {h.callback.__name__: h for h in router.callback_query.handlers}

    assert bound_to_state(by_name["flow_button"])
    assert bound_to_state(by_name["flow_button_state_filter"])
    assert not bound_to_state(by_name["navigation_screen"])
