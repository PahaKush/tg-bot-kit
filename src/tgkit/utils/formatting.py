"""Форматирование текста для сообщений. Только то, что не зависит от предметной области."""

import ipaddress
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
    filled = max(0, min(width, round(width * percent / 100)))
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
    """Прячет хвост адреса клиента: `1.2.*.*:51820`, `[2001:…]:51820`.

    Для экранов, где видны адреса других пользователей: точный IP — это уже геоданные,
    и показывать их целиком незачем. Порт необязателен.
    """
    if not endpoint or endpoint == placeholder:
        return endpoint or placeholder

    if endpoint.startswith("["):
        host, _, rest = endpoint[1:].partition("]")
        port = rest.removeprefix(":")
    elif endpoint.count(":") == 1:
        host, port = endpoint.split(":")
    else:
        host, port = endpoint, ""

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        masked = f"{host[:4]}…"
    else:
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if ip.version == 4:
            a, b, *_ = str(ip).split(".")
            masked = f"{a}.{b}.*.*"
        else:
            masked = f"[{str(ip).split(':')[0]}:…]"
    return f"{masked}:{port}" if port else masked
