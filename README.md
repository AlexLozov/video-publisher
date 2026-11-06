# Video Publisher

Десктопное приложение (ядро на Python + интерфейс на React в окне pywebview), которое публикует одно готовое видео сразу в YouTube/Shorts, TikTok, на страницу Facebook, в Instagram и в Telegram-канал, с возможностью отложенной публикации по расписанию.

> Правило разработки: каждая фича и каждый баг — в отдельной ветке.

Устройство проекта описано в [docs/architecture.md](docs/architecture.md), правила — в [docs/rules.md](docs/rules.md), текущее состояние и план — в [docs/STATUS.md](docs/STATUS.md).

## Что нужно установить

- Python 3.11+
- Node.js 18+ и npm
- Windows: WebView2 Runtime (в Windows 10/11 обычно уже установлен)

## Установка

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
cd ui
npm install
```

## Запуск в режиме разработки

В первом терминале запустите бэкенд (из корня репозитория, с активным venv):

```bash
uvicorn video_publisher.api.app:create_app --factory --reload --port 8000
```

Во втором терминале запустите интерфейс:

```bash
cd ui
npm run dev
```

Откройте адрес, который покажет Vite (обычно http://localhost:5173). Запросы к `/api` проксируются на бэкенд.

## Запуск приложения

```bash
cd ui
npm run build
cd ..
python desktop/run.py
```

## Проверки

Правила тестирования описаны в [docs/rules.md](docs/rules.md). Для e2e-тестов один раз установите браузер:

```bash
cd ui && npm run e2e:install
```

Все обязательные проверки (ruff, mypy, pytest с покрытием, Vitest с покрытием, сборка интерфейса) запускаются одной командой из корня репозитория:

```bash
powershell -ExecutionPolicy Bypass -File scripts\check.ps1
```

Флаг `-E2E` добавляет браузерные тесты Playwright (их запускаем перед крупными этапами). Отдельные проверки:

- Python: `pytest` (покрытие ядра — не ниже 70%), `ruff check .`, `mypy`.
- Интерфейс (в `ui/`): `npm test`, `npm run test:coverage`, `npm run e2e`.
- Интеграционные тесты с реальными API: `pytest -m integration` (по умолчанию не запускаются).
