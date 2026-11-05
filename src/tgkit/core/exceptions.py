import logging


class AppError(Exception):
    """
    Базовое исключение приложения.

    Сообщение из таких ошибок безопасно показывать пользователю напрямую.
    Если конкретное сообщение не задано — используется `default_message`.
    """

    default_message = "Произошла ошибка. Попробуйте позже."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.default_message)

    @property
    def user_message(self) -> str:
        return str(self)


class UserInputError(AppError):
    """Ошибка валидации пользовательского ввода (нет текста, неверный формат)."""

    default_message = "Некорректный ввод. Проверьте данные и попробуйте снова."


class NotFoundError(AppError):
    """Запрошенная сущность не найдена."""

    default_message = "Запрошенный объект не найден."


class AccessDeniedError(AppError):
    """Недостаточно прав для выполнения действия."""

    default_message = "Недостаточно прав для этого действия."


def safe_error_text(exc: Exception, fallback: str) -> str:
    """Безопасный текст ошибки для пользователя.

    AppError и его наследники → user_message: он написан для человека и уже безопасен.
    Любая другая ошибка → fallback, а полная трасса уходит в лог. Без этого локальные
    обработчики светят сырой {e}, и в интерфейс утекают пути, SQL и внутренние имена.

    Приложение добавляет свои наследники AppError. Полезный пример — ошибка, которая
    сообщает не только «что не так», но и «что будет дальше»: «изменения сохранены,
    сервер их пока не принял, повторю сам через пару минут». Пользователю нужен
    именно второй ответ.
    """
    if isinstance(exc, AppError):
        return f"⚠️ {exc.user_message}"
    logging.error("Необработанная ошибка действия: %s", exc, exc_info=True)
    return fallback
