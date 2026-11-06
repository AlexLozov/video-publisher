# WIP: feature/telegram-account

Временный файл прогресса. Удаляется последним коммитом перед слиянием в `main`.

## Сделано
- Правила тестирования: порог покрытия 70%, Playwright только по флагу `-E2E`.
- Бэкенд (готов, pytest 355 зелёных, покрытие 99,6%):
  - `platforms/telegram_mtproto.py` — фабрика клиента Telethon (StringSession, без .session-файлов), `run()`, общие коды ошибок. Клиент создаётся внутри event loop.
  - `auth/telegram_login.py` — вход: телефон → код → (пароль 2FA) → сессия в keyring; выход; ошибки кодами.
  - `platforms/telegram_account.py` — адаптер: MP4 до 2 ГБ, подпись без Markdown, `supports_streaming`, `schedule` (до 365 дней); отозванная сессия удаляется.
  - `platforms/scheduling.py` — проверка времени публикации.
  - `publishing/service.py` — режим «аккаунт», `scheduled_at`, статус `scheduled`.
  - `api/telegram_account_routes.py` — `/api/telegram/account` (+ send-code, sign-in, password, cancel, logout).
  - Переводы новых кодов ошибок и строк интерфейса (ru/en).
  - Тесты: `tests/fake_telegram_account.py`, `test_telegram_login.py`, `test_telegram_account.py`, `test_publishing_account.py`.

## Осталось
1. ~~UI~~ готов: вход в настройках, режим аккаунта и расписание на экране публикации, «запланировано на…».
2. ~~Vitest~~ готов (122 теста).
3. Документация (architecture.md, README), `scripts\check.ps1` зелёный, удалить этот файл, слияние `--no-ff`, STATUS.md через `docs/status-telegram-account`.

## Следующий шаг
Документация (architecture.md, README), затем `scriptseck.ps1`.

## Падающие тесты
- нет
