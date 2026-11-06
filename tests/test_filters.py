import datetime

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, Message, Update, User

from tgkit.filters import IsAdmin

NOW = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)


async def test_is_admin_hides_the_handler_from_others():
    seen = []
    router = Router()

    @router.message(Command("settings"), IsAdmin())
    async def settings(message: Message) -> None:
        seen.append(f"settings:{message.chat.id}")

    @router.message()
    async def fallback(message: Message) -> None:
        seen.append(f"fallback:{message.chat.id}")

    dp = Dispatcher(storage=MemoryStorage(), admins=[1])
    dp.include_router(router)
    bot = Bot(token="42:TEST")
    for user_id in (1, 2):
        user = User(id=user_id, is_bot=False, first_name="U")
        chat = Chat(id=user_id, type="private")
        message = Message(message_id=user_id, date=NOW, chat=chat, from_user=user, text="/settings")
        await dp.feed_update(bot, Update(update_id=user_id, message=message))

    assert seen == ["settings:1", "fallback:2"]
