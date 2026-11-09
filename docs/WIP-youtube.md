# WIP: feature/youtube

Решения Alex (9 ноября 2026): 1А вход через браузер (свой OAuth-клиент Google, httpx, localhost),
2Б файл одним запросом (без частей и докачки), 3А метаданные через hachoir (подсказка «уйдёт как Shorts»),
4Б поля: заголовок, описание, доступ, «для детей», теги, категория (умолчания в Настройках),
5А удаление записи до выхода удаляет видео с YouTube.

## Сделано
- media/probe.py (hachoir + учёт поворота с телефона), тестовые ролики tests/media, метаданные идут в Video (Telethon получает размеры кадра).

- auth/oauth_callback.py (общий слушатель), auth/youtube_login.py (127.0.0.1:любой порт, PKCE, scope youtube, refresh token в keyring, канал из channels.list), /api/youtube/account.
- platforms/youtube.py: resumable-сессия + один PUT всего файла, без повторов после отправки; publishAt (private → public), удаление видео при удалении записи; ссылка /shorts/ для вертикальных ≤3 мин.
- Настройки youtube (client_id, privacy, made_for_kids, category_id, tags), секрет youtube_client_secret; GET /api/publications/video-info.
- Тесты бэкенда: test_youtube.py, test_youtube_login.py, test_publishing_youtube.py, test_media_probe.py.

## Дальше
- Переводы ошибок (ru/en) — сейчас падает test_error_codes_translated.
- UI: раздел YouTube в Настройках (по образцу FacebookSection), подсказка Shorts на экране публикации, platforms.ts.
- check.ps1, слияние, STATUS.md.
