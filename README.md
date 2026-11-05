# tg-bot-kit

Каркас Telegram-бота на aiogram 3 + dishka + SQLite: доступ к базе с транзакциями,
FSM-хранилище, мидлвари, экран ошибки, клавиатуры, шифрование полей и стражи для тестов.
Пакет импортируется как `tgkit`.

Почти каждое решение закрывает конкретную ошибку, и причина записана в докстринге рядом
с кодом. Готовый бот на каркасе — [tg-bot-template](https://github.com/PahaKush/tg-bot-template).

## Установка

Python 3.12+.

```bash
pip install "tg-bot-kit @ https://github.com/PahaKush/tg-bot-kit/archive/refs/tags/v0.1.0.tar.gz"
```

Та же строка без кавычек подходит для `requirements.txt`. Архив тега не требует git, поэтому
ставится и в slim-образе Docker. Версию закрепляйте тегом, а не веткой: иначе бот и каркас
незаметно разъезжаются.

## Что внутри

| Модуль | Что решает |
| --- | --- |
| `tgkit.db.database` | Чтение через пул без блокировок; запись **только** внутри `transaction()`. `fetch_*` отказываются выполнять `INSERT/UPDATE/DELETE` — опечатка ловится сразу |
| `tgkit.db.migrations` | Раннер версионированных миграций. Сами миграции доменные и живут в приложении |
| `tgkit.storage` | FSM-хранилище на aiosqlite с ключом по **полному** `StorageKey`. В личке `chat_id == user_id`, поэтому ключ по одному `chat_id` выглядит рабочим, а в группе состояния участников схлопываются в одну строку |
| `tgkit.core.state` | Типизированный FSM: состояние отвечает только «что мы ждём», данные шага — в pydantic-пейлоаде под своим `flow_key` |
| `tgkit.core.exceptions` | `AppError` несёт текст для человека и показывается как есть; остальное логируется с трассой и заменяется общим сообщением, чтобы в интерфейс не утекали пути, SQL и внутренние имена |
| `tgkit.utils.error_screen` | Экран ошибки с кнопкой «назад». Глобальный роутер умеет только алерт: он не знает, куда возвращать пользователя |
| `tgkit.middlewares` | Семь мидлварей, каждая делает одну вещь (см. «Подключение») |
| `tgkit.kb` | Примитивы клавиатур: `build_kb`, `confirm_kb`, пагинация |
| `tgkit.crypto` | Fernet для данных в покое и шифрование полей, которые модель объявляет сама через `secret_fields` |
| `tgkit.utils.delivery` | Различает «не запускал бота» (400 `chat not found`) и «заблокировал» (403). Ловить только 403 мало: самый частый случай проходит мимо |
| `tgkit.testing.guards` | Начинка стражей: разметка доступа, 64 байта `callback_data`, правила слоя хендлеров |

## Подключение

Порядок мидлварей важен:

```python
dp = Dispatcher(storage=SQLiteStorage(), admins=admin_ids)
setup_dishka(container=container, router=dp, auto_inject=True)

# Самая внешняя на callback: гасит «часики», даже если мидлварь ниже отбросит апдейт.
dp.callback_query.outer_middleware(CallbackAnswerMiddleware())
dp.update.middleware(LoggingMiddleware())

# Троттлинг раньше остальных — до походов в БД. Один инстанс на оба типа апдейтов:
# иначе клик и сообщение за 0,5 с проходят оба и конкурентно правят FSM.
throttling = ThrottlingMiddleware()
dp.message.middleware(throttling)
dp.callback_query.middleware(throttling)

# Классы, зарегистрированные в dishka: каркас не знает репозиториев приложения.
user_context = UserContextMiddleware(store_type=UserRepository, db_type=Database)
dp.message.middleware(user_context)
dp.callback_query.middleware(user_context)
dp.callback_query.middleware(ValidCallbackMiddleware())
dp.callback_query.middleware(AccessControlMiddleware(admin_level=Access.ADMIN))

# Последней: сценарий закрывается только у сматчившегося и разрешённого хендлера.
abandoned_flow = AbandonedFlowMiddleware()
dp.callback_query.middleware(abandoned_flow)
dp.message.middleware(abandoned_flow)
```

## Разметка доступа: deny-by-default

Каждый `CallbackData` объявляет `access`, неразмеченный коллбэк мидлварь **отвергает**.
С неявным правилом («проверим, если у `callback_data` окажется нужное поле») новый коллбэк
молча остаётся без проверки, а проверка одного идентификатора пропускает соседний — так
появляется cross-tenant IDOR.

Владение конкретным ресурсом намеренно не уровень доступа: его нельзя проверить, не зная
предметной области. Такие уровни отдаются `resolver`, а скоуп владения живёт в `WHERE`
каждого запроса, включая чтение.

```python
AccessControlMiddleware(
    admin_level=Access.ADMIN,
    open_levels=(Access.USER, Access.HANDLER),
    resolver=resolve_owner_access,  # уровни, которые знает только приложение
)
```

## Брошенный сценарий закрывается навигацией

«Отмена ❌» и «⬅ Назад» обычно ведут на те же экраны, что и меню, а те состояние FSM не
трогают, и следующее сообщение сработало бы как шаг сценария. `AbandonedFlowMiddleware`
считает навигацией любой хендлер без фильтра состояния и перед ним вызывает `state.clear()`.
Отсюда два правила для приложения:

* хендлер, который **читает пейлоад сценария**, стоит под фильтром состояния, иначе
  пейлоад будет стёрт до его вызова;
* catch-all хендлеры, которым незачем рвать ввод (декоративная кнопка, «меню устарело»),
  помечаются `@flags.keep_state`.

## Шифрование полей

Политика `CryptoService` задаётся наследованием:

```python
class AppCrypto(CryptoService):
    required = False  # без ключа бот работает, шифрование выключено
    tolerate_plaintext = True  # строки, записанные до ключа, читаются как есть
```

Если зашифровано то, без чего приложение бесполезно, обе настройки обратные: `InvalidToken`
означает «не тот ключ», и выдавать его за старое значение нельзя.

Поля, которые модель объявила в `secret_fields`, шифруются с **явной** политикой:

```python
sealed = encrypt_fields(creds, crypto=AppCrypto)
creds = decrypt_fields(sealed, crypto=AppCrypto)
```

## Стражи

`tgkit.testing.guards` — не тесты, а их начинка: приложение подставляет свои коллбэки,
пределы и каталог хендлеров.

```python
ALL = guards.collect_callbacks(models.callbacks)


@pytest.mark.parametrize("klass", ALL, ids=lambda k: k.__name__)
def test_access_declared(klass):
    guards.assert_every_callback_declares_access(klass, Access)


@pytest.mark.parametrize("klass", ALL, ids=lambda k: k.__name__)
def test_fits_telegram_limit(klass):
    guards.assert_callback_fits_telegram_limit(klass, {"flow_id": 8})


def test_no_function_level_imports():
    guards.assert_no_function_level_imports(HANDLERS_DIR)
```

Стоит проверять, что страж **умеет падать**: внести дефект, увидеть красный тест, откатить.

## Что намеренно не включено

Вебхуки, i18n, платежи, Redis-хранилище, ORM, доменная логика. Всё это добавляется под
конкретную задачу.

## Разработка

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"   # Windows: .venv\Scripts\pip
pytest -q && ruff check . && ruff format --check . && mypy
```

Вместе с приложением каркас удобно править установленным в режиме `pip install -e ../tg-bot-kit`.

## Лицензия

MIT
