"""Классификация недоставленного сообщения.

Различие выяснено замером по живому Bot API, а не по документации: самый частый случай —
«никогда не запускал бота» — приходит как 400 «chat not found», а не 403, и обработчик,
ловящий только 403, выдаёт его за сбой отправки.
"""

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import SendMessage

from tgkit.utils.delivery import broadcast, undeliverable_reason

_METHOD = SendMessage(chat_id=1, text="x")


def test_never_started_is_recognised():
    """Реальный ответ Telegram для незнакомого чата: 400, а не 403."""
    exc = TelegramBadRequest(method=_METHOD, message="Bad Request: chat not found")

    reason = undeliverable_reason(exc)

    assert reason is not None
    assert "не запускал" in reason


def test_blocked_by_user_is_recognised():
    exc = TelegramForbiddenError(method=_METHOD, message="Forbidden: bot was blocked by the user")

    reason = undeliverable_reason(exc)

    assert reason is not None
    assert "заблокировал" in reason


def test_other_bad_request_is_not_disguised_as_normal():
    """Иначе настоящая поломка отправки выглядела бы как «он просто не запускал бота»."""
    exc = TelegramBadRequest(method=_METHOD, message="Bad Request: message text is empty")

    assert undeliverable_reason(exc) is None


def test_network_error_is_not_a_delivery_reason():
    assert undeliverable_reason(TelegramNetworkError(method=_METHOD, message="timeout")) is None


def test_arbitrary_exception_is_not_a_delivery_reason():
    assert undeliverable_reason(RuntimeError("boom")) is None


class FakeBot:
    def __init__(self, failures: dict[int, list[Exception]]):
        self.failures = failures
        self.sent: list[int] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        queue = self.failures.get(chat_id)
        if queue:
            raise queue.pop(0)
        self.sent.append(chat_id)


async def test_broadcast_counts_outcomes_separately():
    bot = FakeBot(
        {
            2: [TelegramForbiddenError(method=_METHOD, message="Forbidden: bot was blocked by the user")],
            3: [TelegramBadRequest(method=_METHOD, message="Bad Request: chat not found")],
            4: [RuntimeError("network down")],
        }
    )
    report = await broadcast(bot, [1, 2, 3, 4, 5], "hi", pause=0)

    assert bot.sent == [1, 5]
    assert (report.delivered, report.unreachable, report.failed, report.aborted) == (2, 2, 1, None)


async def test_unparsable_text_aborts_the_broadcast():
    """Ошибка разметки одинакова для всех: дальше не шлём."""
    parse_error = TelegramBadRequest(method=_METHOD, message="Bad Request: can't parse entities: unexpected <")
    bot = FakeBot({1: [parse_error]})
    report = await broadcast(bot, [1, 2, 3], "версия < 2.5", pause=0)

    assert bot.sent == []
    assert report.aborted is not None
    assert "parse entities" in report.aborted


async def test_flood_limit_is_waited_out_and_retried():
    bot = FakeBot({1: [TelegramRetryAfter(method=_METHOD, message="Too Many Requests", retry_after=0)]})
    report = await broadcast(bot, [1], "hi", pause=0)

    assert bot.sent == [1]
    assert report.delivered == 1
