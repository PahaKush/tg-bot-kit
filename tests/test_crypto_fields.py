"""Шифрование полей модели: политика передаётся явно и соблюдается.

Умолчания у `crypto` нет намеренно: им был бы базовый, строгий CryptoService, и
приложение с мягкой политикой, забыв аргумент, молча получило бы чужую — без ключа
ValueError, на старой открытой строке InvalidToken.
"""

from typing import ClassVar

import pytest
from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel

from tgkit.crypto import CryptoService, decrypt_fields, encrypt_fields


class Creds(BaseModel):
    secret_fields: ClassVar[tuple[str, ...]] = ("password",)
    login: str
    password: str


class SoftCrypto(CryptoService):
    required = False
    tolerate_plaintext = True


class StrictCrypto(CryptoService):
    required = True
    tolerate_plaintext = False


@pytest.fixture(autouse=True)
def _reset_ciphers():
    for cls in (CryptoService, SoftCrypto, StrictCrypto):
        cls.reset()
    yield
    for cls in (CryptoService, SoftCrypto, StrictCrypto):
        cls.reset()


def test_policy_is_required():
    with pytest.raises(TypeError):
        encrypt_fields(Creds(login="a", password="p"))  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        decrypt_fields(Creds(login="a", password="p"))  # type: ignore[call-arg]


def test_soft_policy_without_key_passes_through(monkeypatch):
    monkeypatch.delenv(CryptoService.env_var, raising=False)
    creds = Creds(login="a", password="p")

    assert encrypt_fields(creds, crypto=SoftCrypto).password == "p"
    assert decrypt_fields(creds, crypto=SoftCrypto).password == "p"


def test_soft_policy_reads_plaintext_written_before_the_key(monkeypatch):
    monkeypatch.setenv(CryptoService.env_var, Fernet.generate_key().decode())
    legacy = Creds(login="a", password="written-before-encryption")

    assert decrypt_fields(legacy, crypto=SoftCrypto).password == "written-before-encryption"


def test_strict_policy_refuses_to_run_without_key(monkeypatch):
    monkeypatch.delenv(CryptoService.env_var, raising=False)
    with pytest.raises(ValueError):
        encrypt_fields(Creds(login="a", password="p"), crypto=StrictCrypto)


def test_strict_policy_does_not_pass_garbage_off_as_value(monkeypatch):
    monkeypatch.setenv(CryptoService.env_var, Fernet.generate_key().decode())
    with pytest.raises(InvalidToken):
        decrypt_fields(Creds(login="a", password="not-a-fernet-token"), crypto=StrictCrypto)


def test_subclass_does_not_reuse_the_base_cache(monkeypatch):
    monkeypatch.setenv(CryptoService.env_var, Fernet.generate_key().decode())
    monkeypatch.delenv("OTHER_KEY", raising=False)

    class OtherKey(CryptoService):
        env_var = "OTHER_KEY"

    CryptoService.encrypt("base cipher is now cached")
    with pytest.raises(ValueError, match="OTHER_KEY"):
        OtherKey.encrypt("x")


def test_roundtrip_encrypts_only_secret_fields(monkeypatch):
    monkeypatch.setenv(CryptoService.env_var, Fernet.generate_key().decode())
    creds = Creds(login="alice", password="hunter2")

    sealed = encrypt_fields(creds, crypto=StrictCrypto)

    assert sealed.login == "alice"
    assert sealed.password != "hunter2"
    assert decrypt_fields(sealed, crypto=StrictCrypto).password == "hunter2"
