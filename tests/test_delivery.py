"""Классификация недоставленного сообщения.

Различие выяснено замером по живому Bot API, а не по документации: самый частый случай —
«никогда не запускал бота» — приходит как 400 «chat not found», а не 403, и обработчик,
ловящий только 403, выдаёт его за сбой отправки.
"""

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError
from aiogram.methods import SendMessage

from tgkit.utils.delivery import undeliverable_reason

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
