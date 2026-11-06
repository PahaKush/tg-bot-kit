"""Контракт error_screen — единственной точки, через которую рисуются экраны ошибок.

Ошибка в нём бьёт сразу по всему слою хендлеров приложения, поэтому контракт проверен
отдельно.
"""

from aiogram.exceptions import TelegramBadRequest
from aiogram.filters.callback_data import CallbackData
from aiogram.methods import SendMessage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tgkit.core.exceptions import NotFoundError
from tgkit.utils.error_screen import error_screen


class ListCB(CallbackData, prefix="list"):
    page: int = 0


class _Recorder:
    """Ловит вызовы respond(text, markup)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, InlineKeyboardMarkup | None]] = []

    async def __call__(self, text: str, markup: InlineKeyboardMarkup | None) -> None:
        self.calls.append((text, markup))


async def test_success_path_is_silent():
    r = _Recorder()
    async with error_screen(r, "не должно появиться") as screen:
        pass
    assert r.calls == []
    assert screen.failed is False


async def test_domain_error_shows_its_own_message_not_fallback():
    """AppError несёт осмысленный текст — fallback его не затирает."""
    r = _Recorder()
    async with error_screen(r, "❌ Общая заглушка.") as screen:
        raise NotFoundError("Запись не найдена.")

    assert screen.failed is True
    text, _ = r.calls[0]
    assert "Запись не найдена." in text
    assert "Общая заглушка" not in text


async def test_unknown_error_falls_back_and_does_not_leak_details():
    """Сырое исключение наружу не уходит — пользователь видит только fallback."""
    r = _Recorder()
    async with error_screen(r, "❌ Не удалось выполнить действие.") as screen:
        raise RuntimeError("connection refused: 10.0.0.5:9090 token=secret")

    assert screen.failed is True
    text, _ = r.calls[0]
    assert text == "❌ Не удалось выполнить действие."
    assert "secret" not in text


async def test_exception_is_not_reraised():
    """Проглатывание намеренное: иначе поверх экрана прилетит алерт глобального роутера."""
    r = _Recorder()
    async with error_screen(r, "❌ Ошибка."):
        raise RuntimeError("boom")
    assert r.calls  # дошли сюда — исключение не пробросилось


async def test_back_target_becomes_back_button():
    r = _Recorder()
    async with error_screen(r, "❌ Ошибка.", ListCB(page=7)):
        raise RuntimeError("boom")

    _, markup = r.calls[0]
    assert markup is not None
    assert markup.inline_keyboard[0][0].callback_data == ListCB(page=7).pack()


async def test_ready_markup_passes_through_untouched():
    """Экран с «Повторить» вместо «Назад» отдаёт готовую клавиатуру — её не переоборачивают."""
    retry = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Повторить", callback_data="retry")]])
    r = _Recorder()
    async with error_screen(r, "❌ Ошибка.", retry):
        raise RuntimeError("boom")

    _, markup = r.calls[0]
    assert markup is retry


async def test_no_back_target_means_no_keyboard():
    r = _Recorder()
    async with error_screen(r, "❌ Ошибка."):
        raise RuntimeError("boom")

    _, markup = r.calls[0]
    assert markup is None


NOT_MODIFIED = TelegramBadRequest(
    method=SendMessage(chat_id=1, text="x"),
    message="Bad Request: message is not modified: specified new message content and reply markup are exactly the same",
)


async def test_unchanged_screen_is_not_an_error():
    """Повторный клик перерисовывает экран тем же текстом — это не повод показывать ошибку."""
    r = _Recorder()
    async with error_screen(r, "❌ Ошибка.", ListCB()) as screen:
        raise NOT_MODIFIED

    assert r.calls == []
    assert screen.failed is False


async def test_unchanged_error_screen_is_not_raised():
    async def respond(text, markup):
        raise NOT_MODIFIED

    async with error_screen(respond, "❌ Ошибка.") as screen:
        raise RuntimeError("boom")

    assert screen.failed is True
