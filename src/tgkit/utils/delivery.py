"""Почему сообщение пользователю не дошло.

Бот не может написать первым тому, кто с ним не начинал диалог, и это не сбой, а штатный
исход — особенно когда получателя выбирают из списка контактов, а не он пишет боту сам.

Ошибок при этом ДВЕ разных, и различие выяснено замером, а не по документации:

    никогда не запускал бота  -> 400 Bad Request: chat not found
    запускал, потом заблокировал -> 403 Forbidden: bot was blocked by the user

Первый вариант — самый частый, и обработчик, который ловит только 403, выдаёт его за сбой
отправки там, где нужно просто попросить человека запустить бота.
"""

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

_NEVER_STARTED = "человек не запускал бота"
_BLOCKED = "человек заблокировал бота"


def undeliverable_reason(exc: Exception) -> str | None:
    """Человеческая причина недоставки или None, если ошибка не про это.

    None означает «настоящий сбой» — его нельзя показывать как штатную ситуацию, иначе
    реальные поломки отправки будут выглядеть как «ну он просто не запускал бота».
    """
    if isinstance(exc, TelegramForbiddenError):
        return _BLOCKED
    if isinstance(exc, TelegramBadRequest) and "chat not found" in str(exc).lower():
        return _NEVER_STARTED
    return None
