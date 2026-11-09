# WIP: feature/facebook

Публикация видео на Facebook-страницу (Meta Graph API).

## Ждём решения Alex (заданы 2026-11-09)
1. Доступ: А — вход через Facebook (App ID + App Secret в Настройках, localhost-редирект, бессрочный токен страницы в keyring) / Б — вставка готового токена страницы.
2. Загрузка: А — частями (start/transfer/finish) / Б — одним запросом.
3. Формат: А — только обычное видео, Reels и Instagram позже / Б — сразу Reels.

## Сделано
- Ветка создана.

## Следующий шаг
- После ответа Alex: адаптер `platforms/facebook_page.py` + тесты respx, затем вход, затем UI.
