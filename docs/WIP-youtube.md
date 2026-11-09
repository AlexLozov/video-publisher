# WIP: feature/youtube

Решения Alex (9 ноября 2026): 1А вход через браузер (свой OAuth-клиент Google, httpx, localhost),
2Б файл одним запросом (без частей и докачки), 3А метаданные через hachoir (подсказка «уйдёт как Shorts»),
4Б поля: заголовок, описание, доступ, «для детей», теги, категория (умолчания в Настройках),
5А удаление записи до выхода удаляет видео с YouTube.

## Сделано
- media/probe.py (hachoir + учёт поворота с телефона), тестовые ролики tests/media, метаданные идут в Video (Telethon получает размеры кадра).

## Дальше
- auth/youtube_login.py (OAuth loopback 127.0.0.1, PKCE, refresh token в keyring, канал через channels.list)
- platforms/youtube.py (resumable-сессия + один PUT, publishAt, delete)
- настройки youtube, роуты, UI-раздел, подсказка Shorts, переводы, тесты
