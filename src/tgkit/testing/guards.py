"""Переиспользуемые стражи: тесты, которые не дают вернуться уже исправленным ошибкам.

Здесь не сами тесты, а их начинка — приложение подставляет свой модуль коллбэков и свои
пределы. Каждая проверка ловит ошибку, которая иначе всплывает только в проде:

1. `collect_callbacks` + `assert_every_callback_declares_access` — deny-by-default
   на разметке доступа. Без неё новый коллбэк может молча остаться без проверки, и так
   появляется cross-tenant IDOR.
2. `assert_callback_fits_telegram_limit` — худший случай упаковки callback_data.
   Telegram режет её на 64 байтах, и поломка проявляется только на длинном значении.
3. `assert_no_function_level_imports`, `assert_no_private_imports_between_handlers`,
   `assert_error_screens_go_through_one_helper` — импорты внутри функций, приватные
   имена между хендлерами и собственные try/except вокруг общего экрана ошибки.

Полезная привычка: проверять, что страж УМЕЕТ падать. Внесите дефект, убедитесь
в красном, откатите. Тест, который не умеет падать, хуже отсутствующего — он создаёт
ложное спокойствие.
"""

import ast
import inspect
import pathlib
from types import ModuleType, NoneType, UnionType
from typing import Any, Union, get_args, get_origin

from aiogram.filters.callback_data import CallbackData

CALLBACK_DATA_LIMIT = 64

# Telegram-идентификатор приходит извне и по Bot API занимает до 52 бит; внутренние id —
# это AUTOINCREMENT-строки SQLite, где 10 цифр уже фантастика. Путать их нельзя: разница
# в 6 символов на поле решает, влезет ли упаковка в лимит.
TELEGRAM_ID_MAX = 4_503_599_627_370_495  # 2^52 - 1
INTERNAL_ID_MAX = 9_999_999_999


def collect_callbacks(module: ModuleType) -> list[type[CallbackData]]:
    """Все классы CallbackData, объявленные в модуле приложения."""
    return [
        obj
        for obj in vars(module).values()
        if inspect.isclass(obj) and issubclass(obj, CallbackData) and obj is not CallbackData
    ]


def assert_every_callback_declares_access(klass: type[CallbackData], levels: Any) -> None:
    """Коллбэк обязан объявить `access: ClassVar[...]` значением из перечисления приложения."""
    access = getattr(klass, "access", None)
    assert isinstance(access, levels), (
        f"{klass.__name__} не объявил `access`. AccessControlMiddleware отвергнет такой "
        f"коллбэк (deny-by-default). Выберите уровень осознанно."
    )


def worst_case_value(name: str, annotation: Any, string_limits: dict[str, int], telegram_id_fields: set[str]) -> Any:
    """Самое длинное значение, которое поле может принять в проде.

    Строковые поля не угадываются: их предел объявляет приложение. Новое строковое поле
    обязано попасть в string_limits — иначе тест не пройдёт и автор задумается, чем оно
    вообще ограничено.
    """
    if annotation is bool:
        return True
    if annotation is int:
        return TELEGRAM_ID_MAX if name in telegram_id_fields else INTERNAL_ID_MAX
    if annotation is str:
        assert name in string_limits, (
            f"Строковое поле {name!r} не объявило свой предел. Без него нельзя посчитать "
            f"худший случай упаковки callback_data."
        )
        return "a" * string_limits[name]
    args = get_args(annotation)
    if isinstance(annotation, UnionType) or get_origin(annotation) is Union:
        values = [worst_case_value(name, a, string_limits, telegram_id_fields) for a in args if a is not NoneType]
        return max(values, key=_packed_len)
    return max(args or list(annotation), key=_packed_len)


def _packed_len(value: Any) -> int:
    return len(str(getattr(value, "value", value)))


def assert_callback_fits_telegram_limit(
    klass: type[CallbackData],
    string_limits: dict[str, int],
    telegram_id_fields: set[str] | None = None,
    limit: int = CALLBACK_DATA_LIMIT,
) -> None:
    """Худший случай упаковки укладывается в лимит Telegram."""
    tg_fields = telegram_id_fields or {"uid", "user_id"}
    values = {
        name: worst_case_value(name, f.annotation, string_limits, tg_fields) for name, f in klass.model_fields.items()
    }
    try:
        packed = klass(**values).pack()
    except ValueError as e:  # aiogram сам отказывается паковать больше 64 байт
        raise AssertionError(f"{klass.__name__} в худшем случае не влезает в лимит {limit} байт: {e}") from e

    size = len(packed.encode())
    assert size <= limit, f"{klass.__name__} в худшем случае даёт {size} байт при лимите {limit}: {packed}"


def handler_modules(handlers_dir: pathlib.Path) -> list[pathlib.Path]:
    """Все модули слоя хендлеров, ВКЛЮЧАЯ пакеты внутри него.

    rglob, а не glob: как только большой модуль распиливают на пакет, поиск только по
    верхнему уровню выводит все новые файлы из-под стражей сразу — молча и ровно тогда,
    когда их стало больше.
    """
    return sorted(p for p in handlers_dir.rglob("*.py") if p.name != "__init__.py")


def assert_no_function_level_imports(handlers_dir: pathlib.Path) -> None:
    """Импорт внутри функции почти всегда означает цикл, который спрятали, а не развязали."""
    offenders = []
    for path in handler_modules(handlers_dir):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        top_level = {id(node) for node in tree.body}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import | ast.ImportFrom) and id(node) not in top_level:
                names = ", ".join(a.name for a in node.names)
                offenders.append(f"{path.name}:{node.lineno} — {names}")

    assert not offenders, "Импорт внутри функции — маркер цикла, который спрятали, а не развязали:\n  " + "\n  ".join(
        offenders
    )


def assert_no_private_imports_between_handlers(handlers_dir: pathlib.Path, package: str = "handlers") -> None:
    """Хендлеры не лезут в приватные имена друг друга — общий код выносится публично."""
    offenders = []
    for path in handler_modules(handlers_dir):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(f"{package}."):
                for alias in node.names:
                    if alias.name.startswith("_"):
                        offenders.append(f"{path.name}:{node.lineno} — {node.module}.{alias.name}")

    assert not offenders, (
        "Импорт приватного имени из соседнего хендлера. Общий кусок надо вынести в "
        "отдельный модуль публично:\n  " + "\n  ".join(offenders)
    )


def assert_error_screens_go_through_one_helper(handlers_dir: pathlib.Path, symbol: str = "safe_error_text") -> None:
    """Экран ошибки собирается в одном месте.

    Иначе каждый хендлер заново решает, какую кнопку «назад» показать, и экраны
    расходятся с навигацией того места, откуда пришёл пользователь.
    """
    offenders = [p.name for p in handler_modules(handlers_dir) if symbol in p.read_text(encoding="utf-8")]

    assert not offenders, f"Свой обработчик ошибки вместо общего экрана в: {', '.join(offenders)}."
