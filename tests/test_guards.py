import enum
import sys
from typing import ClassVar, Literal

import pytest
from aiogram.filters.callback_data import CallbackData

from tgkit.testing import guards


class Access(enum.StrEnum):
    USER = "user"


class Color(enum.StrEnum):
    RED = "red"
    TURQUOISE = "turquoise"


class AllKindsCB(CallbackData, prefix="all"):
    access: ClassVar[Access] = Access.USER
    item_id: int
    alive: bool
    page: int | None = None
    query: str | None = None
    color: Color
    mode: Literal["a", "long"]


class UnmarkedCB(CallbackData, prefix="unmarked"):
    item_id: int


class LongQueryCB(CallbackData, prefix="long_query"):
    access: ClassVar[Access] = Access.USER
    query: str


class CyrillicCB(CallbackData, prefix="c"):
    access: ClassVar[Access] = Access.USER
    mode: Literal["яяяяяяяяяя"]


def test_collects_callbacks_of_a_module():
    found = set(guards.collect_callbacks(sys.modules[__name__]))
    assert found == {AllKindsCB, UnmarkedCB, LongQueryCB, CyrillicCB}


def test_access_must_be_declared():
    guards.assert_every_callback_declares_access(AllKindsCB, Access)
    with pytest.raises(AssertionError, match="access"):
        guards.assert_every_callback_declares_access(UnmarkedCB, Access)


def test_every_field_kind_has_a_worst_case():
    guards.assert_callback_fits_telegram_limit(AllKindsCB, {"query": 8})


def test_worst_case_takes_the_longest_option():
    assert guards.worst_case_value("color", Color, {}, set()) is Color.TURQUOISE
    assert guards.worst_case_value("mode", Literal["a", "long"], {}, set()) == "long"
    assert guards.worst_case_value("user_id", int | None, {}, {"user_id"}) == guards.TELEGRAM_ID_MAX


def test_string_field_without_limit_fails():
    with pytest.raises(AssertionError, match="предел"):
        guards.assert_callback_fits_telegram_limit(LongQueryCB, {})


def test_overflow_fails():
    with pytest.raises(AssertionError, match="лимит"):
        guards.assert_callback_fits_telegram_limit(LongQueryCB, {"query": 60})


def test_limit_is_counted_in_bytes():
    """12 символов, но 22 байта: Telegram считает байты."""
    with pytest.raises(AssertionError, match="22 байт"):
        guards.assert_callback_fits_telegram_limit(CyrillicCB, {}, limit=15)
