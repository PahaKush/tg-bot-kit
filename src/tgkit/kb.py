"""Примитивы клавиатур: ни одного доменного имени.

Клавиатура описывается списком пар (подпись, callback), а не сборкой Builder'а руками
в каждом хендлере. `Btn` принимает и строку, и CallbackData — типизированные коллбэки
не приходится паковать на каждом вызове.
"""

from collections.abc import Callable, Sequence

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton as Button
from aiogram.types import InlineKeyboardMarkup as Markup
from aiogram.utils.keyboard import InlineKeyboardBuilder as Builder

Btn = str | CallbackData
BtnList = list[tuple[str, Btn]]


def build_kb(*buttons: tuple[str, Btn], adjust: tuple[int, ...] = (1,)) -> Markup:
    """Клавиатура из пар (подпись, callback). adjust задаёт раскладку по рядам."""
    kb = Builder()
    for text, cb_data in buttons:
        kb.button(text=text, callback_data=cb_data)

    kb.adjust(*adjust)
    return kb.as_markup()


def back_btn(callback_data: Btn) -> Markup:
    return build_kb(("⬅ Назад", callback_data))


def cancel_btn(callback_data: Btn) -> Markup:
    return build_kb(("Отмена ❌", callback_data))


def confirm_kb(yes: Btn, no: Btn, *, yes_text: str = "Да ✅", no_text: str = "Нет ❌") -> Markup:
    """Экран подтверждения разрушительного действия.

    Вынесен отдельной функцией, чтобы «спросить перед разрушением» стоило одну строку:
    подтверждение, которое собирают руками, рано или поздно забывают.
    """
    return build_kb((yes_text, yes), (no_text, no), adjust=(2,))


def refresh_back_kb(refresh_cb: CallbackData, back_cb: Btn, refresh_text: str = "🔄 Обновить") -> Markup:
    """Стандартная пара «обновить (тот же колбэк) + назад» для экранов с живыми данными."""
    return build_kb((refresh_text, refresh_cb), ("⬅ Назад", back_cb), adjust=(1,))


def page_bounds(total: int, page: int, limit: int) -> tuple[int, int, int]:
    """Клемпит страницу в допустимый диапазон. Возвращает (page, total_pages, start_idx)."""
    total_pages = (total + limit - 1) // limit if total > 0 else 1
    page = max(0, min(page, total_pages - 1))
    return page, total_pages, page * limit


def pagination_row(
    page: int,
    total_pages: int,
    cb_for_page: Callable[[int], CallbackData],
    prev_label: str = "⬅️",
    next_label: str = "➡️",
) -> list[Button]:
    """Ряд навигации пагинации. cb_for_page(page) -> CallbackData нужной страницы.
    Пустой список, если страница одна — рисовать нечего."""
    row: list[Button] = []
    if page > 0:
        row.append(Button(text=prev_label, callback_data=cb_for_page(page - 1).pack()))
    if total_pages > 1:
        row.append(Button(text=f"{page + 1}/{total_pages}", callback_data="ignore"))
    if page < total_pages - 1:
        row.append(Button(text=next_label, callback_data=cb_for_page(page + 1).pack()))
    return row


def paginated_kb[T](
    items: Sequence[T],
    page: int,
    limit: int,
    row_for: Callable[[T], Button],
    cb_for_page: Callable[[int], CallbackData],
    *,
    empty_label: str | None = None,
) -> tuple[Builder, int]:
    """Список с пагинацией: страница элементов + ряд навигации. Возвращает (builder, page).

    Без пагинации экран перестаёт открываться, когда элементов становится около сотни:
    Telegram отвергает разметку целиком, и по тексту ошибки причину не понять.

    Generic по типу элемента: с `row_for: Callable[[object], Button]` mypy отвергает любую
    конкретную функцию строки (`row(item: Note)`) — параметры функций контравариантны.
    """
    kb = Builder()
    page, total_pages, start = page_bounds(len(items), page, limit)

    for item in items[start : start + limit]:
        kb.row(row_for(item))

    if not items and empty_label:
        kb.row(Button(text=empty_label, callback_data="ignore"))

    if nav := pagination_row(page, total_pages, cb_for_page):
        kb.row(*nav)

    return kb, page
