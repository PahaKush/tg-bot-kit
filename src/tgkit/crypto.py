"""Шифрование чувствительных полей в покое (Fernet).

Два уровня:
  * `CryptoService` — операции и fail-fast проверка ключа при старте;
  * `encrypt_fields` / `decrypt_fields` — шифрование ОТДЕЛЬНЫХ полей pydantic-модели,
    которые она объявляет сама через `secret_fields`.

Второй уровень и есть то, ради чего это в каркасе. Модель знает, что в ней секретно,
поэтому вызывающему коду не нужно помнить состав полей — а именно на этом «помнить»
и появляются записи, уехавшие в БД открытым текстом.

Политика задаётся двумя атрибутами класса, потому что у разных приложений она разная:

  * `required` — обязателен ли ключ. Если зашифровано то, без чего приложение
    бесполезно (например, токены доступа к серверам), отсутствие ключа — отказ
    запускаться: расшифровать базу потом будет нечем. Приложению, которое должно
    подниматься «из коробки», подходит опциональное шифрование, а включение —
    осознанный шаг.
  * `tolerate_plaintext` — считать ли InvalidToken при расшифровке значением,
    записанным ДО включения шифрования. Для опциональной схемы это обязательно: иначе
    один старый ряд роняет весь экран. Для обязательной — наоборот вредно: там
    InvalidToken означает «не тот ключ» или порчу данных, и молчать об этом нельзя.

Переключаются наследованием:

    class AppCrypto(CryptoService):
        required = False
        tolerate_plaintext = True
"""

import logging
import os
from collections.abc import Mapping
from typing import Any, ClassVar, Protocol, Self, runtime_checkable

from cryptography.fernet import Fernet, InvalidToken


class CryptoService:
    """Операции шифрования на одном процессном ключе из переменной окружения."""

    env_var = "DB_ENCRYPTION_KEY"
    required = True
    tolerate_plaintext = False

    _fernet: Fernet | None = None
    _checked = False

    @classmethod
    def generate_key(cls) -> str:
        """Готовый ключ для .env."""
        return Fernet.generate_key().decode()

    @classmethod
    def validate_key(cls) -> None:
        """Проверка на старте.

        Битый ключ обязан ронять бот сразу, а не при первой записи: иначе поломка
        всплывает посреди пользовательского флоу, где её не с чем связать.
        """
        key = os.getenv(cls.env_var)
        if not key:
            if cls.required:
                raise ValueError(f"Критическая ошибка: {cls.env_var} не задан в окружении!")
            logging.warning("%s не задан — шифрование выключено.", cls.env_var)
            return
        try:
            Fernet(key.encode("utf-8"))
        except Exception as e:
            raise ValueError(
                f"Критическая ошибка: невалидный {cls.env_var}! Ожидается сгенерированный Fernet-ключ. {e}"
            ) from e

    @classmethod
    def _cipher(cls) -> Fernet | None:
        if not cls._checked:
            key = os.getenv(cls.env_var)
            if not key and cls.required:
                raise ValueError(f"{cls.env_var} не задан в окружении")
            cls._fernet = Fernet(key.encode("utf-8")) if key else None
            cls._checked = True
        return cls._fernet

    @classmethod
    def reset(cls) -> None:
        """Сбрасывает закэшированный шифратор. Нужен тестам, которые подменяют ключ."""
        cls._fernet = None
        cls._checked = False

    @classmethod
    def encrypt(cls, data: str | None) -> str | None:
        cipher = cls._cipher()
        if not data or cipher is None:
            return data
        return cipher.encrypt(data.encode("utf-8")).decode("utf-8")

    @classmethod
    def decrypt(cls, data: str | None) -> str | None:
        cipher = cls._cipher()
        if not data or cipher is None:
            return data
        try:
            return cipher.decrypt(data.encode("utf-8")).decode("utf-8")
        except InvalidToken:
            if cls.tolerate_plaintext:
                # Строка записана до включения шифрования — отдаём как есть.
                return data
            raise


@runtime_checkable
class HasSecretFields(Protocol):
    """Самоописательная модель: знает свои секретные поля сама.

    secret_fields — ClassVar, потому что так его объявляют модели
    (`secret_fields: ClassVar[...]`). С обычным атрибутом в протоколе такая модель ему
    не соответствует, и вызовы encrypt_fields/decrypt_fields не проходят проверку типов.
    """

    secret_fields: ClassVar[tuple[str, ...]]

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self: ...


# Политика (`crypto`) передаётся ЯВНО и без значения по умолчанию. Умолчанием мог бы быть
# только базовый, строгий CryptoService, и приложение с мягкой политикой, забыв аргумент,
# молча получило бы чужую: без ключа — ValueError вместо работы «из коробки», на старой
# открытой строке — InvalidToken вместо значения. Обязательный аргумент превращает такую
# ошибку в TypeError на месте вызова.


def encrypt_fields[T: HasSecretFields](model: T, *, crypto: type[CryptoService]) -> T:
    """Копия модели, в которой зашифрованы поля из secret_fields."""
    updates = {f: crypto.encrypt(getattr(model, f)) for f in model.secret_fields}
    return model.model_copy(update=updates) if updates else model


def decrypt_fields[T: HasSecretFields](model: T, *, crypto: type[CryptoService]) -> T:
    """Обратная операция к encrypt_fields."""
    updates = {f: crypto.decrypt(getattr(model, f)) for f in model.secret_fields}
    return model.model_copy(update=updates) if updates else model
