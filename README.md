# Receipt Bot

Telegram-бот для обработки армянских чеков:

`Telegram photo -> Google Cloud Vision OCR -> CLEAN OCR -> OpenAI -> Russian field review in Telegram -> Obsidian note + original image`

Ручная проверка выполняется не по армянскому OCR, а по русским полям, которые попадут в заметку.

Стек MVP: Python 3.11+, `python-telegram-bot`, `FastAPI`, `google-cloud-vision`, `openai`, `python-dotenv`.

## Project context persistence

Для существенных изменений архитектуры, модели данных, бизнес-логики,
авторизации, хранения, API, интеграций, миграций, деплоя или тестирования нужно
обновлять контекст проекта в `docs/`:

- `docs/PROJECT_STATE.md` — текущее состояние проекта и инварианты;
- `docs/DECISIONS.md` — устойчивые архитектурные и продуктовые решения;
- `docs/TASK_LOG.md` — журнал значимых изменений;
- `docs/NEXT_STEPS.md` — ближайшие задачи, вопросы и риски.

Историю решений и задач не удаляем без причины: если решение устарело, оно
помечается как superseded; если факт важен, но не решён, он фиксируется как
open question.

## Что делает проект

- получает фото чека в Telegram;
- распознаёт текст чека через Google Cloud Vision `DOCUMENT_TEXT_DETECTION` с языковыми подсказками `hy`, `ru`, `en`;
- создаёт CLEAN OCR без смысловых исправлений и перевода;
- отправляет CLEAN OCR в OpenAI для структурирования;
- показывает пользователю только поля будущей заметки на русском;
- ждёт подтверждения, JSON-исправления или отмены;
- запоминает ручные исправления и применяет их к следующим чекам;
- создаёт Markdown-заметку в Obsidian vault;
- сохраняет оригинальное изображение и оба OCR-файла.

## 1. Создание Telegram-бота через BotFather

1. Откройте `@BotFather` в Telegram.
2. Выполните команду `/newbot`.
3. Задайте имя и username бота.
4. Скопируйте токен и вставьте его в `.env` как `TELEGRAM_BOT_TOKEN`.

## 2. Включение Google Cloud Vision API

1. Создайте проект в Google Cloud Console.
2. Включите `Cloud Vision API`.
3. Убедитесь, что для проекта настроен биллинг, если он требуется Google Cloud.

## 3. Авторизация Google Cloud Vision API

Для локальной разработки используйте Application Default Credentials (ADC), без service account JSON key:

```bash
gcloud init
gcloud auth application-default login
gcloud auth application-default set-quota-project PROJECT_ID
```

Где `PROJECT_ID` — ID проекта, в котором включён Cloud Vision API и настроен биллинг.

В локальном `.env` не задавайте `GOOGLE_APPLICATION_CREDENTIALS`, если хотите использовать ADC. Приложение читает из `.env` только свои рабочие настройки и не использует эту переменную оттуда. Код создаёт клиента обычным способом:

```python
from google.cloud import vision

client = vision.ImageAnnotatorClient()
```

Клиентская библиотека сама найдёт доступные ADC.

Если нужно локально работать именно от имени service account, используйте impersonation без скачивания JSON-ключа:

```bash
gcloud auth application-default login \
  --impersonate-service-account=SERVICE_ACCOUNT_NAME@PROJECT_ID.iam.gserviceaccount.com
```

Для этого вашему пользователю нужна роль `roles/iam.serviceAccountTokenCreator`.

Для production предпочтителен keyless-подход:

- в Google Cloud — attached service account;
- вне Google Cloud или в CI — Workload Identity Federation или service account impersonation;
- JSON key — только как fallback, если администратор явно разрешил его использование.

Google рекомендует по возможности выбирать более безопасные альтернативы service account keys; для организаций, созданных `3 мая 2024` или позже, запрет на создание таких ключей может применяться по умолчанию.

## 4. Настройка `.env`

Скопируйте `.env.example` в `.env` и заполните:

```env
TELEGRAM_BOT_TOKEN=
OPENAI_API_KEY=
OBSIDIAN_VAULT=
OPENAI_MODEL=gpt-5.4-mini
ADMIN_TELEGRAM_USER_IDS=123456789
ALLOWED_TELEGRAM_USER_IDS=123456789,987654321
PRIVILEGED_TELEGRAM_USER_IDS=
DATA_DIR=data
DATABASE_URL=sqlite:///data/app.db
DB_BUSY_TIMEOUT_MS=5000
APP_STORAGE_DIR=data/storage
TMP_STORAGE_DIR=data/tmp
EXPORT_STORAGE_DIR=data/exports
DEBUG_STORAGE_DIR=data/debug
STORAGE_RETENTION_TMP_HOURS=24
STORAGE_RETENTION_EXPORT_DAYS=30
STORAGE_RETENTION_DEBUG_DAYS=14
STORAGE_IMAGE_BACKEND=local
S3_BUCKET_NAME=
S3_ENDPOINT_URL=
S3_ACCESS_KEY_ID=
S3_SECRET_ACCESS_KEY=
S3_KEY_PREFIX=receipt-bot
STORAGE_STORED_IMAGE_MAX_EDGE_PX=2000
STORAGE_STORED_IMAGE_JPEG_QUALITY=85
USER_VAULT_ROOT=Users
REGULAR_DAILY_RECEIPT_LIMIT=10
REGULAR_MONTHLY_RECEIPT_LIMIT=100
PRIVILEGED_DAILY_RECEIPT_LIMIT=0
PRIVILEGED_MONTHLY_RECEIPT_LIMIT=0
BOT_MODE=polling
WEBHOOK_URL=
WEBHOOK_SECRET_TOKEN=
WEB_BASE_URL=
WEB_LISTEN=0.0.0.0
WEB_PORT=8081
WEB_MAGIC_LINK_TTL_MINUTES=10
WEB_SESSION_TTL_DAYS=30
WEB_SESSION_COOKIE_NAME=receipt_bot_session
```

`OPENAI_MODEL` можно поменять без изменения кода. По умолчанию используется недорогая современная модель `gpt-5.4-mini`.

SQLite-хранилище создаётся при старте бота. Оно хранит пользователей, заявки,
квоты, активные review-сессии и новые подтверждённые документы/items/files.
Obsidian остаётся человекочитаемым экспортом, а не primary storage.

`STORAGE_IMAGE_BACKEND=local` хранит canonical image-файлы в
`APP_STORAGE_DIR`. Для production можно использовать S3-compatible backend
(`STORAGE_IMAGE_BACKEND=s3`), например Backblaze B2 private bucket. В этом
режиме в SQLite сохраняются bucket/key/checksum metadata, а публичные URL не
становятся canonical reference.

Секреты можно читать напрямую из env или через `*_FILE`:

```env
TELEGRAM_BOT_TOKEN_FILE=/run/secrets/telegram_token
OPENAI_API_KEY_FILE=/run/secrets/openai_key
```

## 5. Установка зависимостей

```bash
cd receipt-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 6. Локальный запуск

```bash
cd receipt-bot
source .venv/bin/activate
python bot.py
```

Перед запуском убедитесь, что:

- все обязательные переменные заполнены;
- `OBSIDIAN_VAULT` существует;
- локальные ADC настроены через `gcloud auth application-default login`;
- для ADC задан quota project через `gcloud auth application-default set-quota-project PROJECT_ID`.

Если у вас остался старый `.env` с `GOOGLE_APPLICATION_CREDENTIALS`, удалите эту строку для ясности. Она больше не нужна локальному ADC-сценарию.

## 7. Доступ к боту

В `.env` задайте администраторов и стартовый allowlist:

```env
ADMIN_TELEGRAM_USER_IDS=123456789
ALLOWED_TELEGRAM_USER_IDS=123456789,987654321
PRIVILEGED_TELEGRAM_USER_IDS=
```

Админы всегда считаются разрешёнными пользователями. Пользователи, роли, заявки на доступ, события квот, активные review-сессии и новые документы хранятся в SQLite:

```text
data/app.db
data/storage/documents/
data/tmp/processing/
data/exports/
data/debug/
```

`data/access.json`, `data/users/users.json`, `data/users/access_requests.json` и `data/sessions/*.json` больше не являются runtime-хранилищем и не импортируются автоматически. `data/corrections.json` используется только как legacy one-time import source, если SQLite `correction_rules` ещё пустая. Доступ задаётся через `.env` bootstrap и меняется через SQLite-backed Telegram approval flow. Незавершённые проверки восстанавливаются из SQLite `processing_sessions`; временные изображения и OCR-файлы лежат в `data/tmp/processing/<session_id>/`. После confirm canonical images сохраняются через выбранный image backend (`local` или `s3`), OCR-файлы сохраняются в `data/storage/documents/<document_id>/`, а temp-dir очищается при финализации session.

Если пользователь не в allowlist, бот не скачивает фото, не вызывает Google Vision, не вызывает OpenAI и не создаёт файлы. Вместо этого создаётся pending-заявка, а всем администраторам приходит сообщение с кнопками `Approve` / `Reject`.

Команды:

- `/whoami` — показать Telegram user_id;
- `/access` — создать заявку на доступ;
- `/users` — список allowed users, только для админа;
- `/revoke <user_id>` — отозвать доступ, только для админа.
- `/web` — получить одноразовую magic-link ссылку для входа в read-only Web MVP.

Роли:

- `admin` — всегда имеет доступ и не ограничен лимитами;
- `privileged` — привилегированная группа, по умолчанию без лимитов;
- `regular` — обычный пользователь с дневным и месячным лимитом попыток обработки.

Лимиты задаются в `.env`:

```env
REGULAR_DAILY_RECEIPT_LIMIT=10
REGULAR_MONTHLY_RECEIPT_LIMIT=100
PRIVILEGED_DAILY_RECEIPT_LIMIT=0
PRIVILEGED_MONTHLY_RECEIPT_LIMIT=0
```

`0` означает `unlimited`. Лимит считается по событиям `receipt_attempt` в SQLite: попытка списывается после проверки доступа и лимита, но до скачивания изображения, OCR и OpenAI. Событие хранит snapshot роли и финальный тип документа, если он был определён автоматически после OCR. Поэтому пользователь с исчерпанным лимитом не запускает OCR/OpenAI и не создаёт файлы. Старые JSON-счётчики в `data/usage` не импортируются и удаляются при инициализации quota storage.

## 8. Структура Obsidian vault

После успешной обработки нового чека бот создаёт DB-записи в `documents`,
`document_items` и `document_files`, сохраняет canonical images через выбранный
storage backend, оставляет OCR-файлы в local app storage и экспортирует
Markdown с изображением в Obsidian:

```text
data/storage/documents/<document_id>/original.jpg
data/storage/documents/<document_id>/stored.jpg
data/storage/documents/<document_id>/clean.hy.txt
data/storage/documents/<document_id>/source.hy.txt
Users/<telegram_user_id>/Receipts/YYYY/MM/<file_name>.md
Users/<telegram_user_id>/Attachments/receipts/YYYY/MM/<file_name>.jpg
data/debug/openai/<telegram_user_id>/YYYY/MM/<temporary_name>.openai.raw.txt
data/exports/<telegram_user_id>/receipts_YYYYMMDD_HHMMSS.zip
```

При `STORAGE_IMAGE_BACKEND=s3` `original.jpg` и `stored.jpg` хранятся как
private S3/B2 objects, например
`<S3_KEY_PREFIX>/documents/<document_id>/original.jpg`; `data/storage` остаётся
local backend для OCR и dev/local image storage.

`data/debug/openai/...` появляется только если OpenAI вернул невалидный JSON.
При старте бот безопасно чистит только ожидаемые старые export ZIP
`receipts_*.zip`, OpenAI debug artifacts `*.openai.raw.txt` и временные
materialized/cache-файлы из `data/tmp/materialized`, `data/tmp/exports` и
`data/tmp/telegram`. Cleanup отказывается запускаться на опасных storage roots.
Retention управляется настройками
`STORAGE_RETENTION_EXPORT_DAYS`, `STORAGE_RETENTION_DEBUG_DAYS` и
`STORAGE_RETENTION_TMP_HOURS`.

Новые `MANIFEST/...` файлы не создаются. Старые manifest-файлы остаются
поддержанным fallback для legacy-чеков.

Каждый пользователь пишет в отдельное пространство внутри vault:

```text
Users/<telegram_user_id>/
```

Обычный пользователь может читать, экспортировать и удалять только свои чеки. Администратор может управлять доступом и отзывать пользователей.

Scoped-правила замен вроде `WT -> шт` хранятся в SQLite `correction_rules`.

Пока финальное имя ещё неизвестно, CLEAN OCR и изображение сохраняются под временным именем. После подтверждения русских полей бот переносит их в финальные пути.

Properties заметки содержат только:

```yaml
date: "2026-04-07"
time: "20:41:00"
merchant: "Zovq Supermarket"
amount: "4465.75"
category: "Grocery"
```

Эти поля нормализуются перед записью: merchant приводится к единому английскому названию, amount использует точку для дробной части, category пишется в едином Title Case формате.

## 9. Как работает ручная проверка

После OCR и разбора OpenAI бот отправляет:

1. оригинальное фото;
2. русские поля, которые попадут в заметку;
3. кнопки:
   - `✅ Подтвердить заметку`
   - `✏️ Исправить поля`
   - `❌ Отменить`

Если пользователь подтверждает поля, бот создаёт заметку.

Если пользователь выбирает исправление, бот отправляет JSON с полями заметки. Пользователь меняет значения и отправляет JSON обратно. Бот создаёт заметку из исправленного JSON.

Если пользователь отменяет обработку, Markdown-заметка не создаётся.

При исправлении бот сравнивает исходные поля OpenAI и исправленные поля. Если, например, было `WT`, а пользователь заменил на `шт`, правило сохраняется в SQLite `correction_rules` и будет применяться к следующим чекам.

## 10. Нестандартные чеки и скриншоты заказов

Бот пытается автоматически отличить фискальный чек от скриншота заказа после OCR. Для этого используются локальные признаки текста: фискальные маркеры вроде `ՀՎՀՀ`, `Ֆիսկալ`, `Կտրոն`, `НДС`, `касса`, а также order-маркеры вроде `заказ`, `delivery`, `корзина`, `ingredients`, `состав`, `Բաղադրություն`, `x2`.

Если бот ошибается или документ спорный, используйте ручной override:

```text
/order
```

После команды отправьте скриншот заказа. Также можно отправить фото сразу с caption `/order`.

В этом режиме OCR остаётся тем же, но OpenAI получает отдельную инструкцию для order-сценариев. Бот извлекает только полезные строки заказа:

- название товара;
- количество;
- цену за единицу;
- сумму строки.

Текст вроде ингредиентов, состава, описаний, рекламных блоков, кнопок приложения, адресов доставки и прочего UI-шума должен пропускаться. Итоговый документ сохраняется в SQLite с `document_type: "order"`, а экспортная заметка в Obsidian получает заголовок `Заказ`.

## 11. Формат результата OpenAI

OpenAI должен вернуть строгий JSON:

```json
{
  "date": "",
  "time": "",
  "merchant": "",
  "amount": "",
  "currency": "AMD",
  "category": "",
  "armenian_text": "",
  "russian_translation": "",
  "english_translation": "",
  "summary_ru": "",
  "items": [
    {
      "name_original": "",
      "name_ru": "",
      "name_en": "",
      "unit_price": "",
      "quantity": "",
      "unit": "",
      "line_total": ""
    }
  ],
  "possible_errors": []
}
```

В Markdown-заметке сначала выводится чек на русском, затем таблица товаров, затем английская версия и таблица товаров на английском. Исходный OCR не выводится в теле заметки; canonical OCR сохраняется в `data/storage/documents/<document_id>/` и записывается в SQLite `document_files`. Для новых DB-first документов Markdown attachment создаётся из `stored_image`, а canonical image может храниться локально или в private S3/B2 bucket.

Если JSON невалиден, заметка не создаётся, а сырой ответ сохраняется в
`data/debug/openai/...`.

## 12. Удаление чека

Чтобы удалить Markdown-заметку вместе с изображением и OCR-файлами, отправьте боту:

```text
/delete_receipt <receipt_id или file.md>
```

Если имя файла уникально, можно указать только его:

```text
/delete_receipt 2025-11-24_at_torg_1318AMD.md
```

Для новых DB-first чеков удаление использует SQLite `document_files`: бот
удаляет canonical local files, удаляет все версии recorded S3 image objects,
удаляет Obsidian export-файлы и помечает документ как deleted в SQLite. DB row
остаётся для аудита и скрывается из списков.

Для старых чеков остаётся fallback через manifest JSON или wikilinks из Markdown.
В обоих режимах проверяется scope: обычный пользователь не может удалить чужой
чек, а записанные пути не могут выходить за пределы настроенных storage roots.

Команды для получения своих чеков:

- `/my_receipts` — показать последние сохранённые чеки;
- `/receipt <receipt_id>` — получить краткую карточку, изображение и Markdown-файл чека;
- `/order` — обработать следующее фото как скриншот заказа;
- `/export_receipts` — получить ZIP-архив своих чеков: readable Obsidian files
  плюс canonical DB-файлы под `Canonical/<receipt_id>/`.
- `/grant_receipt <user_id> <receipt_id>` — только для админа, deep-copy
  DB-first чека пользователю или legacy-copy старого manifest-backed чека.
- `/storage_health` — только для админа, read-only отчёт по `documents` /
  `document_files`, missing files, checksum drift, unsafe refs и orphan app
  files.

Web MVP запускается отдельным процессом:

```bash
python web.py
```

Telegram `/web` создаёт одноразовую ссылку с TTL
`WEB_MAGIC_LINK_TTL_MINUTES`. После входа web-сессия хранится в HttpOnly cookie
`WEB_SESSION_COOKIE_NAME` до `WEB_SESSION_TTL_DAYS`. Web MVP read-only и
показывает только DB-first документы текущего пользователя; legacy manifest
чеки остаются доступны через Telegram/Obsidian fallback.

## 13. Production и Docker

Локальный MVP и production polling запускаются одинаково:

```bash
python bot.py
```

Production mode по умолчанию:

```env
BOT_MODE=polling
```

Docker-файлы находятся в `deploy/docker`:

```bash
docker compose -f deploy/docker/docker-compose.yml up -d --build
```

Compose поднимает два процесса из одного образа: `receipt-bot` для Telegram и
`receipt-web` для FastAPI Web MVP.

Webhook mode опционален:

```env
BOT_MODE=webhook
WEBHOOK_URL=https://your-domain.example/telegram-webhook
WEBHOOK_SECRET_TOKEN=<random-secret>
WEBHOOK_LISTEN=0.0.0.0
WEBHOOK_PORT=8080
WEB_BASE_URL=https://your-domain.example
WEB_PORT=8081
```

В webhook mode используется проверка Telegram secret token через заголовок `X-Telegram-Bot-Api-Secret-Token`. Caddyfile в `deploy/docker/Caddyfile` проксирует `/telegram-webhook` в Telegram bot и остальные запросы в Web MVP.

## 14. Тесты

```bash
python -m pytest -q
```

Тесты покрывают path safety, JSON parsing, Markdown rendering, access control, SQLite migrations, scoped correction rules, manifest deletion, user isolation, DB-first documents/items/files/delete/copy/export, per-user receipt index/export, processing sessions, user quotas, magic-link auth и read-only Web MVP API.

## 15. Ограничения MVP

- без внешней базы данных; локальное SQLite-хранилище используется для доступа пользователей, квот, review-сессий и новых документов/items/files;
- Web MVP пока read-only: без web delete/export/grant/correction-rule management;
- без очереди задач и фоновых воркеров;
- незавершённые review-сессии хранятся в SQLite `processing_sessions`;
- используется один прямой поток обработки на пользователя;
- правила исправлений хранятся в SQLite и пока остаются простыми scoped exact replacements.
