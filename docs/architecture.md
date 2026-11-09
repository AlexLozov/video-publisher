# Архитектура Video Publisher

Приложение состоит из нескольких слоёв. Каждый слой зависит только от слоёв ниже себя.

## core — ядро на Python (`core/video_publisher/`)

- **domain** — модели предметной области: `Video`, `PostText`, `PublishTarget`, `Publication`, перечисления `Platform` и `PostFormat`. Ни от чего внутри приложения не зависит.
- **config** — параметры запуска через pydantic-settings: папка данных, порт и т. п. Задаются переменными окружения с префиксом `VP_` или файлом `.env`.
- **storage** — база SQLite (`%USERPROFILE%\.video-publisher\app.db`) через SQLAlchemy. Структуру базы ведут миграции Alembic (`storage/migrations/versions`). При каждом запуске приложение доводит базу до последней версии, данные сохраняются. Новая таблица или колонка = новая миграция.
- **user_settings** — настройки, которые пользователь меняет в приложении: язык, способ публикации в Telegram, канал, api_id. Хранятся в базе.
- **auth** — секреты (токены, api_hash, сессии) хранятся в Диспетчере учётных данных Windows через `keyring`, а не в базе и не в файлах. API их только принимает (`PUT /api/secrets/{name}`) и сообщает, задан секрет или нет. Обратно значение не отдаёт никогда. В тестах и e2e используется хранилище в памяти (`VP_SECRET_BACKEND=memory`). Здесь же вход в Telegram-аккаунт (`auth/telegram_login.py`): телефон → код → пароль двухэтапной проверки, если он включён. Готовая сессия Telethon (`StringSession`) хранится в keyring как `telegram_session`, файлов `.session` на диске нет. Незавершённый вход живёт только в памяти. Номер, код и пароль не сохраняются. Здесь же вход через Facebook (`auth/facebook_login.py`): OAuth code flow с собственным Meta-приложением пользователя. На время входа поднимается маленький HTTP-сервер на `127.0.0.1:47615` (`VP_FACEBOOK_REDIRECT_PORT`, порт занят эксклюзивно), браузер открывает диалог Facebook, после редиректа код меняется на долгоживущий токен пользователя (нужен App Secret), а по нему `/me/accounts` даёт бессрочные токены страниц. Выбранный токен хранится в keyring как `facebook_page_token`, id и название страницы — в настройках. Остальные токены живут только в памяти до выбора страницы. Позже обмен кода переедет на auth-сервер. Слушатель редиректа общий для Facebook и Google (`auth/oauth_callback.py`). Вход через Google для YouTube (`auth/youtube_login.py`): свой OAuth-клиент пользователя типа «Desktop app», слушатель на `127.0.0.1` на любом свободном порту (Google принимает любой loopback-порт, регистрировать адрес не нужно), PKCE, scope `https://www.googleapis.com/auth/youtube`. Refresh token хранится в keyring как `youtube_refresh_token`, id и название канала (`channels.list?mine=true`) — в настройках, access token живёт одну операцию.

## adapters — адаптеры платформ (`core/video_publisher/platforms/`)

У каждой соцсети свой адаптер: наследник `PlatformAdapter` с методами `authenticate`, `validate`, `publish` и `schedule`. Свойство `supports_native_scheduling` показывает, умеет ли платформа сама откладывать публикацию. Адаптеры регистрируются в `PlatformRegistry`. Так остальной код не знает деталей конкретных API.

У Telegram два адаптера, способ выбирается в настройках:

- `telegram_bot.py` — Bot API через httpx, видео до 50 МБ, без отложенных постов.
- `telegram_account.py` — аккаунт пользователя через Telethon (MTProto), видео до 2 ГБ. Отложенные посты хранит сам Telegram (`schedule_date`, до 365 дней вперёд). Файл сначала загружается (`upload_file`), потом публикуется (`send_file`). Обрыв связи при загрузке — обычная ошибка сети. Обрыв при публикации — `telegram_timeout_uncertain`, без автоповтора, чтобы не было дубля. Если сессию завершили на другом устройстве, она удаляется, и настройки просят войти заново.

Facebook-страница — `facebook_page.py`: `POST /{page-id}/videos` на `graph-video.facebook.com`, файл одним multipart-запросом (до 1 ГБ). Отложенный пост — `published=false` + `scheduled_publish_time` (10 минут — 30 дней, поля адаптера `min_schedule_lead` / `max_schedule_ahead`). Отмена — `DELETE /{video-id}`. Повторяются только запросы без файла и неудачные подключения; обрыв или ответ шлюза при загрузке — `facebook_timeout_uncertain` без повтора. Ошибки Graph API (`error.code`) переводятся в коды `facebook_*`; код 190 удаляет токен страницы.

YouTube — `youtube.py`: `videos.insert` протоколом resumable в простейшем виде — запрос с метаданными создаёт сессию (его можно повторять), затем один `PUT` со всем файлом (до 256 ГБ), без частей и докачки. Обрыв или ответ шлюза во время `PUT` — `youtube_timeout_uncertain` без повтора. Shorts отдельного флага не имеют: вертикальное или квадратное видео до 3 минут YouTube показывает как Shorts, адаптер только даёт ссылку `/shorts/`. Отложенная публикация — `status.privacyStatus=private` + `status.publishAt` (15 минут — 365 дней); отмена — `DELETE videos?id=`. Доступ, «для детей», категория и теги берутся из настроек. Ошибки (`error.errors[].reason`) переводятся в коды `youtube_*`; `invalid_grant` при обновлении токена удаляет refresh token.

Метаданные файла — `media/probe.py` (hachoir, чистый Python): длительность и размер кадра с учётом матрицы поворота (телефоны пишут вертикальное видео горизонтальным кадром с поворотом 90°). Сервис публикации заполняет ими `Video` — это нужно для ссылки Shorts и размеров кадра в Telegram «Мой аккаунт».

Общие части Telethon лежат в `telegram_mtproto.py`: фабрика клиента и перевод ошибок в коды. В тестах вместо Telethon подставляется фейковый клиент (`tests/fake_telegram_account.py`). Проверка времени публикации общая для всех площадок — `platforms/scheduling.py`.

## scheduler — планировщик (`core/video_publisher/scheduler/`)

Ставит публикации в очередь и запускает их в нужное время. Если платформа умеет отложенную публикацию, планировщик вызывает `schedule`. Если нет, он сам ждёт нужного времени и вызывает `publish`.

## api — HTTP API (`core/video_publisher/api/`)

Приложение на FastAPI, через которое с ядром общается интерфейс. Сейчас есть `GET /api/health`, `GET /api/platforms`, `GET/PATCH /api/settings`, `PUT/DELETE /api/secrets/{name}`, публикации (`POST/GET /api/publications`, необязательное поле `scheduled_at` с часовым поясом; `POST /api/publications/batch` — одно видео сразу в несколько соцсетей: сначала проверяются все, при ошибке ничего не запускается, у записей общий `batch_id`; `DELETE /api/publications/{id}` — удалить запись, запланированный пост сначала отменяется на площадке через `cancel_scheduled`; `DELETE /api/publications` — очистить историю от опубликованных и неудачных, запланированные остаются) вход в Telegram-аккаунт (`GET /api/telegram/account`, `POST …/send-code`, `…/sign-in`, `…/password`, `…/cancel`, `…/logout`) и вход через Facebook (`GET /api/facebook/account`, `POST …/login`, `…/select-page`, `…/cancel`, `…/logout`; токены страниц в ответы не попадают), вход через Google для YouTube (`GET /api/youtube/account`, `POST …/login`, `…/cancel`, `…/logout`) и `GET /api/publications/video-info?path=` — длительность, размер кадра и «уйдёт как Shorts» для выбранного файла. Ошибки проверки данных приходят как `{"code": "validation_error", "params": {"fields": {"body.telegram.channel": "invalid_channel"}}}`. В десктоп-режиме API заодно отдаёт собранный интерфейс как статику.

## ui — интерфейс (`ui/`)

React + TypeScript + Vite + Tailwind.

Локализация: i18next + react-i18next. Переводы лежат в `ui/src/locales/<язык>.json` (сейчас `ru` и `en`, по умолчанию `ru`). Переключатель языка — в шапке и на экране настроек. Выбор хранится в настройках на бэкенде. localStorage — только кэш, чтобы первый кадр сразу был на нужном языке. Бэкенд не отдаёт текст для пользователя: ошибки приходят как `{"code": ..., "params": {...}}`, а интерфейс переводит их по ключу `errors.<code>`. Названия платформ тоже переводятся по ключу `platform.<id>`. В разработке запускается через `npm run dev` и проксирует `/api` на бэкенд. Для приложения собирается в `ui/dist`.

## desktop — десктопная оболочка (`desktop/run.py`)

Запускает uvicorn в фоновом потоке на `127.0.0.1` на свободном порту и открывает окно pywebview с интерфейсом. `bridge.py` даёт интерфейсу окна Windows «Открыть файл» (`pick_video`) и «Сохранить как» (`save_csv`, экспорт истории в CSV). Подписи фильтров файлов — только латиница, цифры и пробелы: pywebview отвергает, например, косую черту, и окно молча не открывается.

Запуск для пользователя — `Запуск.bat` в корне (логика в `scripts\launch.ps1`): при первом запуске ставит зависимости, пересобирает интерфейс при изменениях, один раз создаёт ярлык на рабочем столе (`desktop\icon.ico`) и запускает приложение через `pythonw` без консоли.

## auth-server (в будущем)

Небольшой внешний сервис для OAuth-редиректов и хранения client secret'ов там, где платформа не разрешает держать их в десктоп-приложении (например, TikTok, Meta). Десктоп будет получать токены через него.
