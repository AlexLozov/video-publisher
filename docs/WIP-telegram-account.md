# WIP: feature/telegram-account

Временный файл прогресса. Удаляется последним коммитом перед слиянием в `main`.

## Сделано
- Правила тестирования: порог покрытия 70%, Playwright только по флагу `-E2E` (коммит `chore: lower coverage gate…`).
- Бэкенд:
  - `platforms/telegram_mtproto.py` — фабрика клиента Telethon (StringSession, без .session-файлов), `run()`, общие коды ошибок.
  - `auth/telegram_login.py` — вход: телефон → код → (пароль 2FA) → сессия в keyring; выход; ошибки кодами.
  - `platforms/telegram_account.py` — адаптер: MP4 до 2 ГБ, подпись, `supports_streaming`, `schedule` (до 365 дней).
  - `platforms/scheduling.py` — проверка времени публикации.
  - `publishing/service.py` — режим «аккаунт», `scheduled_at`, статус `scheduled`.
  - `api/telegram_account_routes.py` — `/api/telegram/account` (+ send-code, sign-in, password, cancel, logout).
  - Зависимости: telethon, cryptg.

## Осталось
1. ~~Переводы~~ готовы; pytest входа готов (`tests/test_telegram_login.py`, фейк `tests/fake_telegram_account.py`).
2. pytest: вход, адаптер, расписание, сервис, API (Telethon полностью замокан).
3. UI: вход на экране настроек, подсказка про my.telegram.org, режим аккаунта и расписание на экране публикации, статус «запланировано на…».
4. Vitest: ключевые сценарии.
5. Документация (architecture.md), `scripts\check.ps1` зелёный, слияние, STATUS.md.

## Следующий шаг
pytest для адаптера, расписания и сервиса публикации.

## Падающие тесты
- `test_publishing.py::test_factory_account_mode_not_available_yet` (устарел, заменить).
