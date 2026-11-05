"""Форматирование текста для сообщений. Только то, что не зависит от предметной области."""

import re
import time
from datetime import UTC, datetime
from html import escape


def esc(value: object | None) -> str:
    """Экранирование под parse_mode=HTML. None → "".

    Прогонять через него ВСЁ, что пришло от пользователя: имя, заголовок, введённый
    текст. Иначе один символ `<` ломает разметку всего сообщения, и Telegram отвергает
    отправку — а выглядит это как «бот молчит».
    """
    if value is None:
        return ""
    return escape(str(value), quote=False)


def truncate(text: str, limit: int = 64) -> str:
    """Обрезка для подписи кнопки: у Telegram лимит, и длинная подпись ломает клавиатуру."""
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def format_bytes(size: int | float | None) -> str:
    if not size:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024.0:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{size:.1f} PB"


def format_time_ago(timestamp: int) -> str:
    """«N мин назад» из unix-времени.

    Проверка `<= 0`, а не только на ноль: источником бывает `.Unix()` нулевого
    `time.Time` из Go, и это -62135596800 — на нём наивная реализация рисует
    «739826 дн назад» вместо «Никогда».
    """
    if not timestamp or timestamp <= 0:
        return "Никогда"
    diff = int(time.time()) - timestamp
    if diff < 60:
        return "Только что"
    if diff < 3600:
        return f"{diff // 60} мин назад"
    if diff < 86400:
        return f"{diff // 3600} ч назад"
    return f"{diff // 86400} дн назад"


def format_uptime(seconds: int) -> str:
    if not seconds:
        return "Неизвестно"
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    return f"{days} дн, {hours} ч"


def get_progress_bar(percent: float, width: int = 10) -> str:
    """Полоска загрузки из символов — дешевле картинки и не требует загрузки файла."""
    filled = int(round(width * percent / 100))
    return f"[{'▬' * filled}{'▭' * (width - filled)}]"


def format_db_timestamp(ts: str | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """CURRENT_TIMESTAMP в SQLite — НАИВНАЯ строка UTC вида '2026-07-31 10:15:00'.

    Переводим в локальное время процесса (TZ из окружения). Без явного tzinfo=UTC
    наивная строка трактуется как локальная, и время уезжает на смещение зоны —
    ошибка правдоподобная и потому незаметная.
    """
    if not ts:
        return "—"
    try:
        dt = datetime.strptime(str(ts), "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return str(ts)
    return dt.astimezone().strftime(fmt)


_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$")


def is_valid_domain(domain: str) -> bool:
    """FQDN вида www.example.com. Отвергает IP, пробелы, кавычки, одиночные метки."""
    return bool(_DOMAIN_RE.match(domain))


def mask_endpoint(endpoint: str | None, placeholder: str = "Не подключен") -> str:
    """Прячет две последние группы IPv4 в адресе клиента.

    Для экранов, где видны адреса других пользователей: точный IP — это уже геоданные,
    и показывать их целиком незачем.
    """
    if not endpoint or endpoint == placeholder:
        return endpoint or placeholder
    if ":" not in endpoint:
        return endpoint
    ip, port = endpoint.rsplit(":", 1)
    parts = ip.split(".")
    if len(parts) == 4:  # IPv4
        return f"{parts[0]}.{parts[1]}.*.*:{port}"
    return f"{ip[:4]}…:{port}"  # IPv6 / нестандартный формат
