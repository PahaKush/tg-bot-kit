"""Почему сообщение пользователю не дошло.

Бот не может написать первым тому, кто с ним не начинал диалог, и это не сбой, а штатный
исход — особенно когда получателя выбирают из списка контактов, а не он пишет боту сам.

Ошибок при этом ДВЕ разных, и различие выяснено замером, а не по документации:

    никогда не запускал бота  -> 400 Bad Request: chat not found
    запускал, потом заблокировал -> 403 Forbidden: bot was blocked by the user

Первый вариант — самый частый, и обработчик, который ловит только 403, выдаёт его за сбой
отправки там, где нужно просто попросить человека запустить бота.
"""

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

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


@dataclass
class BroadcastReport:
    delivered: int = 0
    unreachable: int = 0  # не запускал бота или заблокировал — штатный исход
    failed: int = 0
    aborted: str | None = None  # Telegram отверг сам текст — слать дальше бессмысленно


async def broadcast(bot: Bot, chat_ids: Iterable[int], text: str, *, pause: float = 0.05) -> BroadcastReport:
    """Шлёт текст по списку и раскладывает исходы.

    Пауза держит рассылку ниже лимита Bot API (~30 сообщений/с); на 429 ждём, сколько
    просит Telegram, и повторяем. Ошибка разметки одинакова для всех получателей, поэтому
    на ней рассылка прерывается сразу.
    """
    report = BroadcastReport()
    for chat_id in chat_ids:
        try:
            try:
                await bot.send_message(chat_id, text)
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
                await bot.send_message(chat_id, text)
        except Exception as e:
            if isinstance(e, TelegramBadRequest) and "can't parse entities" in str(e).lower():
                report.aborted = str(e)
                break
            if undeliverable_reason(e) is None:
                report.failed += 1
                logging.warning("Рассылка: не удалось отправить %s: %s", chat_id, e)
            else:
                report.unreachable += 1
        else:
            report.delivered += 1
        await asyncio.sleep(pause)
    return report
