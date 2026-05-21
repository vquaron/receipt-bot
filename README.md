# Receipt Bot

Telegram-бот для обработки армянских чеков:

`Telegram photo -> Google Cloud Vision OCR -> CLEAN OCR -> OpenAI -> Russian field review in Telegram -> Obsidian note + original image`

Ручная проверка выполняется не по армянскому OCR, а по русским полям, которые попадут в заметку.

Стек MVP: Python 3.11+, `python-telegram-bot`, `google-cloud-vision`, `openai`, `python-dotenv`.

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
USER_VAULT_ROOT=Users
REGULAR_DAILY_RECEIPT_LIMIT=10
REGULAR_MONTHLY_RECEIPT_LIMIT=100
PRIVILEGED_DAILY_RECEIPT_LIMIT=0
PRIVILEGED_MONTHLY_RECEIPT_LIMIT=0
BOT_MODE=polling
WEBHOOK_URL=
WEBHOOK_SECRET_TOKEN=
```

`OPENAI_MODEL` можно поменять без изменения кода. По умолчанию используется недорогая современная модель `gpt-5.4-mini`.

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

Админы всегда считаются разрешёнными пользователями. Runtime-состояние хранится в:

```text
data/users/users.json
data/users/access_requests.json
data/sessions/
data/corrections.json
data/usage/YYYY-MM/<user_id>.json
```

Если пользователь не в allowlist, бот не скачивает фото, не вызывает Google Vision, не вызывает OpenAI и не создаёт файлы. Вместо этого создаётся pending-заявка, а всем администраторам приходит сообщение с кнопками `Approve` / `Reject`.

Команды:

- `/whoami` — показать Telegram user_id;
- `/access` — создать заявку на доступ;
- `/users` — список allowed users, только для админа;
- `/revoke <user_id>` — отозвать доступ, только для админа.

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

`0` означает `unlimited`. Лимит считается по попыткам отправить фото чека: попытка списывается после проверки доступа и лимита, но до скачивания изображения, OCR и OpenAI. Поэтому пользователь с исчерпанным лимитом не запускает OCR/OpenAI и не создаёт файлы.

## 8. Структура Obsidian vault

После успешной обработки бот создаёт:

```text
Users/<telegram_user_id>/Receipts/YYYY/MM/<file_name>.md
Users/<telegram_user_id>/Attachments/receipts/YYYY/MM/<file_name>.jpg
Users/<telegram_user_id>/OCR/YYYY/MM/<file_name>.clean.hy.txt
Users/<telegram_user_id>/OCR_VERIFIED/YYYY/MM/<file_name>.verified.hy.txt
Users/<telegram_user_id>/DEBUG/openai/YYYY/MM/<temporary_name>.openai.raw.txt
Users/<telegram_user_id>/MANIFEST/receipts/YYYY/MM/<file_name>.manifest.json
```

`DEBUG/openai/...` появляется только если OpenAI вернул невалидный JSON.

`MANIFEST/...` создаётся для безопасного удаления всех файлов конкретного чека.

Каждый пользователь пишет в отдельное пространство внутри vault:

```text
Users/<telegram_user_id>/
```

Обычный пользователь может читать, экспортировать и удалять только свои чеки. Администратор может управлять доступом и отзывать пользователей.

`data/corrections.json` появляется после ручных исправлений и хранит scoped-правила замен вроде `WT -> шт`.

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

При исправлении бот сравнивает исходные поля OpenAI и исправленные поля. Если, например, было `WT`, а пользователь заменил на `шт`, правило сохраняется в `data/corrections.json` и будет применяться к следующим чекам.

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

Текст вроде ингредиентов, состава, описаний, рекламных блоков, кнопок приложения, адресов доставки и прочего UI-шума должен пропускаться. Итоговая заметка сохраняется в той же структуре `Receipts`, но в manifest добавляется `document_type: "order"`, а заголовок заметки становится `Заказ`.

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

В Markdown-заметке сначала выводится чек на русском, затем таблица товаров, затем английская версия и таблица товаров на английском. Исходный OCR не выводится в теле заметки, но сохраняется отдельными файлами и доступен через служебные OCR-ссылки.

Если JSON невалиден, заметка не создаётся, а сырой ответ сохраняется в `DEBUG/openai/...`.

## 12. Удаление чека

Чтобы удалить Markdown-заметку вместе с изображением и OCR-файлами, отправьте боту:

```text
/delete_receipt <receipt_id или file.md>
```

Если имя файла уникально, можно указать только его:

```text
/delete_receipt 2025-11-24_at_torg_1318AMD.md
```

Удаление связанных файлов сначала использует manifest JSON. Если manifest отсутствует, бот использует fallback по wikilinks из разделов `Оригинал` и `Контроль OCR`. В обоих случаях бот удаляет только файлы внутри `OBSIDIAN_VAULT`.

Для пользователя дополнительно проверяется scope: manifest не может удалить файлы за пределами `Users/<telegram_user_id>/...`.

Команды для получения своих чеков:

- `/my_receipts` — показать последние сохранённые чеки;
- `/receipt <receipt_id>` — получить краткую карточку, изображение и Markdown-файл чека;
- `/order` — обработать следующее фото как скриншот заказа;
- `/export_receipts` — получить ZIP-архив своей папки чеков.
- `/grant_receipt <user_id> <receipt_id>` — только для админа, скопировать существующий чек в отдельную папку пользователя.

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

Webhook mode опционален:

```env
BOT_MODE=webhook
WEBHOOK_URL=https://your-domain.example/telegram-webhook
WEBHOOK_SECRET_TOKEN=<random-secret>
WEBHOOK_LISTEN=0.0.0.0
WEBHOOK_PORT=8080
```

В webhook mode используется проверка Telegram secret token через заголовок `X-Telegram-Bot-Api-Secret-Token`. Caddyfile в `deploy/docker/Caddyfile` нужен только для webhook-сценария.

## 14. Тесты

```bash
python -m pytest -q
```

Тесты покрывают path safety, JSON parsing, Markdown rendering, access control, scoped correction rules, manifest deletion, user isolation, per-user receipt index/export и user quotas.

## 15. Ограничения MVP

- без базы данных;
- без веб-интерфейса;
- без очереди задач и фоновых воркеров;
- незавершённые review-сессии хранятся в файлах `data/sessions`;
- пользователи, роли, заявки и usage-лимиты хранятся в JSON-файлах в `data`;
- используется один прямой поток обработки на пользователя;
- правила исправлений простые и основаны на точных заменах значений.
