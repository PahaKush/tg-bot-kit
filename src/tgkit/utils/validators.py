from aiogram.types import Message

from tgkit.core.exceptions import UserInputError


def get_valid_text(message: Message, error_msg: str = "⚠️ Пожалуйста, отправьте текст.") -> str:
    """Извлекает текст/подпись из сообщения или выбрасывает UserInputError."""
    text = message.text or message.caption
    if not text:
        raise UserInputError(error_msg)
    return text.strip()
