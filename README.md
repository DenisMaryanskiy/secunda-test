# Сервис процессинга платежей

Тестовое задание для Secunda. API принимает платёж и отвечает сразу; поход в шлюз
(эмуляция: 2–5 с, 90% успеха) выполняется фоном, статус обновляется, клиенту уходит webhook.

## Как устроено

```schema
  клиент
    │  POST /api/v1/payments
    ▼
┌───────┐   одна транзакция:    ┌────────────┐
│  api  │──── payment + outbox ─▶│ PostgreSQL │
└───────┘                       └────────────┘
    │ 202 Accepted                     ▲
    ▼                                  │ SELECT … FOR UPDATE SKIP LOCKED
  клиент                     ┌──────────────────┐
                             │ outbox-publisher │
                             └──────────────────┘
                                       │ publish, persist + confirm
                                       ▼
                                 ┌──────────┐
                                 │ RabbitMQ │  payments.new
                                 └──────────┘
                                       │
                                       ▼
                                ┌──────────┐   2–5 с      ┌────────┐
                                │ consumer │─────────────▶│  шлюз  │
                                └──────────┘              └────────┘
                                       │ подписанный webhook
                                       ▼
                                    клиент
```

Три процесса: `api`, `consumer`, `outbox-publisher`. Разделены из-за разного профиля нагрузки:
API отвечает за миллисекунды, consumer большую часть времени ждёт шлюз.

## Структура

```schema
src/payment_service/
├── api/                  HTTP-транспорт, нужен только процессу api
│   ├── app.py            фабрика приложения
│   ├── errors.py         обработчики исключений, единый формат ошибок
│   ├── dependencies/     файл на зависимость: settings, database, services,
│   │                     security, idempotency
│   └── routers/          файл на ресурс; __init__.py собирает api_router с /api/v1
├── schemas/              контракты API, файл на сущность: payment, health, error;
│                         base.py задаёт общие правила, common.py - общие типы полей
├── models/               таблицы SQLAlchemy
├── repositories/         доступ к данным
├── services/             бизнес-логика: платежи, сценарий обработки, идемпотентность
├── adapters/             клиенты наружу: платёжный шлюз, вебхуки, SSRF-проверка
├── messaging/            RabbitMQ: топология, consumer, retry, события
├── outbox/               publisher, отдельный процесс
└── config.py             настройки окружения
```

## Запуск

Нужен только Docker.

```bash
make up
curl -s localhost:8000/health
```

`.env` не обязателен, в compose заданы рабочие значения по умолчанию. Для локального запуска
без Docker есть `.env.example`.

| | Адрес |
|---|---|
| API | <http://localhost:8000>, схема на `/docs` (только вне production) |
| RabbitMQ UI | <http://localhost:15672>, `guest` / `guest` |
| Postgres | `localhost:5432`, `payments` / `payments` |
| Приёмник вебхуков | <http://localhost:8080>, печатает запросы в лог |

`make down` гасит всё вместе с томами.

## Примеры

Ключ статический, по умолчанию `local-dev-key`.

```bash
curl -i -X POST localhost:8000/api/v1/payments \
  -H 'X-API-Key: local-dev-key' \
  -H 'Idempotency-Key: demo-1' \
  -H 'Content-Type: application/json' \
  -d '{
        "amount": "100.50",
        "currency": "RUB",
        "description": "Заказ 42",
        "metadata": {"order_id": "42"},
        "webhook_url": "http://webhook-sink:8080/hook"
      }'
```

Ответ: `202` с `payment_id` и статусом `pending`. Через несколько секунд:

```bash
curl -s localhost:8000/api/v1/payments/<payment_id> -H 'X-API-Key: local-dev-key'
```

статус станет `succeeded` или `failed`, а в `docker compose logs webhook-sink` будет виден webhook
с заголовком `X-Payment-Signature`.

Повтор с тем же `Idempotency-Key` вернёт тот же `payment_id` и заголовок
`Idempotent-Replay: true`. Тот же ключ с другим телом: `409 idempotency_conflict`.

Ошибки приведены к одному формату, включая те, что генерирует FastAPI:

```json
{"code": "payment_not_found", "message": "Платёж 88fff74e-… не найден"}
```

Коды: `unauthorized`, `validation_error`, `idempotency_conflict`, `payment_not_found`,
`internal_error`.

## Механики

### Outbox

API ничего не публикует: в одной транзакции пишутся платёж и строка в `outbox_messages`.
Отдельный процесс фетчит неопубликованные события:

```sql
SELECT … FROM outbox_messages
 WHERE published_at IS NULL
 ORDER BY created_at
 LIMIT :batch
   FOR UPDATE SKIP LOCKED
```

`SKIP LOCKED` нужен, чтобы второй экземпляр publisher'а не ждал на заблокированных записях,
а брал следующие. `published_at` проставляется после подтверждения брокера, поэтому ошибка
между publish и commit даёт повторную публикацию, а не потерю: доставка at-least-once,
идемпотентность на стороне consumer'а.

Канал открыт с `on_return_raises=True`. Иначе немаршрутизируемое сообщение брокер вернул бы
через `Basic.Return`, publish отработал бы "успешно", и событие пропало бы, будучи помеченным
как опубликованное.

### Идемпотентность

`Idempotency-Key` обязателен при создании платежа. Сначала проверить, нет ли уже такого ключа,
а потом вставить недостаточно: между проверкой и вставкой успевает пролезть параллельный
запрос. Поэтому от дублей защищает `UNIQUE` на колонке. Вставка идёт как есть, а если ключ
уже занят, `IntegrityError` перехватывается и клиент получает ранее созданный платёж.

`request_fingerprint` — sha256 канонизированного тела, отличает ретрай от ошибки клиента.
Порядок ключей в JSON на отпечаток не влияет, изменившаяся сумма — влияет.

### Повторы и DLQ

```schema
payments.new ──✗──▶ payments.new.retry.1  (TTL 2 с) ──┐
                                                      │ по DLX обратно
payments.new ──✗──▶ payments.new.retry.2  (TTL 4 с) ──┤
                                                      │
payments.new ──✗──▶ payments.new.dlq                  ▼
                                                 payments.new
```

TTL в RabbitMQ задаётся на очередь целиком, а задержка растёт экспоненциально.
Номер попытки видно в `x-attempt`, после третьей сообщение уходит в
`payments.new.dlq` с текстом ошибки в `x-last-error`. Логика в middleware, обработчик
про повторы не знает.

У вебхуков два уровня повторов: три быстрые попытки внутри процесса (сетевой блип) и,
если не помогло, круг через retry-очереди — он переживает перезапуск контейнера.

### Подпись вебхука

```bash
X-Payment-Timestamp: 1786467244
X-Payment-Signature: sha256=9f86d0818…
```

HMAC-SHA256 от `{timestamp}.{тело}`, схема как у Stripe и GitHub. Timestamp в подписываемых
данных не даёт переигрывать перехваченный запрос.

```python
import hashlib
import hmac
import time


def verify(secret: str, headers, body: bytes, tolerance: int = 300) -> bool:
    timestamp = int(headers["X-Payment-Timestamp"])
    if abs(time.time() - timestamp) > tolerance:
        return False
    signed = f"{timestamp}.".encode() + body
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", headers["X-Payment-Signature"])
```

Сравнение через `compare_digest`; подписывать нужно сырые байты тела, а не пересобранный JSON.

Тело:

```json
{
  "payment_id": "…",
  "status": "succeeded",
  "amount": "100.50",
  "currency": "RUB",
  "failure_reason": null,
  "processed_at": "2026-08-11T20:10:06.316084+00:00"
}
```

### SSRF

`webhook_url` приходит от клиента, а запрос делает consumer изнутри периметра. Перед
отправкой хост резолвится и проверяется, что адрес публичный; схема только `https`; редиректы
не выполняются. Проверка повторяется непосредственно перед запросом, а не только при создании
платежа, потому что DNS-запись могла перепривязаться.

Для локального запуска есть `WEBHOOK__ALLOW_INSECURE_TARGETS`; с `ENVIRONMENT=production`
такая конфигурация не поднимется.

Проверка адреса и сам запрос резолвят хост независимо, поэтому DNS с коротким TTL может
отдать проверке публичный адрес, а httpx — внутренний. Полностью это закрывается только
привязкой соединения к уже проверенному IP или egress-политикой на уровне сети; см.
«Ограничения».

### Схема API

`/docs`, `/redoc` и `/openapi.json` публикуются только в окружениях из списка
`DOCS_ENVIRONMENTS`; в production все три отдают `404`. Список разрешающий, а не
запрещающий: новое окружение по умолчанию закрыто, пока его не добавят туда явно.

Выключается именно `openapi_url`, а не только UI: без этого схема со всеми ручками, полями и
заголовками авторизации возвращается одним запросом в обход выключенного `/docs`.

## Конфигурация

Переменные окружения, вложенность через `__`. Полный список в `.env.example`.

| Переменная | По умолчанию | Что делает |
|---|---|---|
| `ENVIRONMENT` | `local` | `production` закрывает схему API и запрещает небезопасные webhook-цели |
| `API_KEY` | `local-dev-key` | Значение заголовка `X-API-Key` |
| `LOG_FORMAT` | `json` в compose | `console` — человекочитаемо |
| `CONSUMER__MAX_ATTEMPTS` | `3` | Попыток на сообщение, дальше DLQ |
| `CONSUMER__RETRY_BASE_DELAY_SECONDS` | `2` | Задержка повтора: `base × 2^(попытка−1)` |
| `GATEWAY__SUCCESS_RATE` | `0.9` | Доля успешных ответов эмулятора |
| `GATEWAY__MIN/MAX_DELAY_SECONDS` | `2` / `5` | Задержка эмулятора, `0` отключает ожидание |
| `WEBHOOK__SIGNING_SECRET` | — | Обязателен |
| `WEBHOOK__MAX_ATTEMPTS` | `3` | Быстрые повторы внутри процесса |
| `OUTBOX__POLL_INTERVAL_SECONDS` | `1` | Пауза, когда публиковать нечего |
| `OUTBOX__BATCH_SIZE` | `100` | Размер пачки |

## Разработка

```bash
make install     # зависимости и pre-commit
make test        # все тесты, интеграционные поднимают контейнеры сами
make test-unit   # быстрые, без Docker
make check       # то же, что в CI: линтер, типы, тесты
```

123 теста, покрытие 99%, порог 90%. Интеграционные работают на настоящих
Postgres и RabbitMQ через testcontainers, схему накатывает alembic, миграция заодно проверяется.
Без Docker'а пропускаются.

CI: ruff и mypy, тесты, gitleaks по всей истории, pip-audit, trivy по образу. Trivy нашёл
22 исправимых HIGH/CRITICAL в базовом образе (openssl, gnutls, libcap2), поэтому в Dockerfile
добавлен `apt-get upgrade`.

## Отклонения от ТЗ

| Поле | Зачем |
|---|---|
| `failure_reason` | Статус `failed` без причины бесполезен клиенту и в отладке |
| `webhook_delivered_at` | At-least-once означает повторную доставку события; без отметки клиент получит дубль уведомления |
| `request_fingerprint` | Тот же ключ с другим телом иначе молча вернёт чужой платёж. Единственное расширение контракта: ответ `409` |
| `attempts`, `last_error` в outbox | Без них застрявшее событие не диагностировать |

Webhook уходит с заголовками подписи; тело запроса не меняется.

В базе колонка называется `metadata`, в Python-модели атрибут `meta` — имя `metadata`
занято SQLAlchemy. В API поле остаётся `metadata`.

## Ограничения

**DLQ никто не разгребает.** Ни алерта, ни ручки для повторной отправки.

**Застрявшее событие в outbox блокирует пачку.** Событие с постоянной ошибкой публикации
будет повторяться вечно; наберётся больше `OUTBOX__BATCH_SIZE` — свежие перестанут уезжать.
Нужен счётчик попыток и карантин.

**Постоянные и временные ошибки вебхука неразличимы.** Приватный адрес в `webhook_url`
и недоступный DNS проходят одинаковый путь в девять попыток до DLQ.

**Смена настроек повторов ломает старт.** `CONSUMER__RETRY_BASE_DELAY_SECONDS` превращается
в `x-message-ttl`, а RabbitMQ не даёт переобъявить очередь с другими аргументами:

```bash
PRECONDITION_FAILED - inequivalent arg 'x-message-ttl' for queue 'payments.new.retry.1':
received '5000' but current is '2000'
```

Лечится удалением очередей или версионированием имён.

**Ключ к API один и статический.** Без ротации и без разграничения прав.

**SSRF-проверка не привязана к соединению.** `ensure_safe_webhook_target` резолвит хост и
отбраковывает внутренние адреса, но запрос httpx делает по имени и резолвит его заново.
Владелец домена с TTL=0 может отдать проверке публичный адрес, а соединению — `10.0.0.5`
или `169.254.169.254`. Окно между проверкой и коннектом сокращено до микросекунд, но
враждебный DNS-сервер ровно для этого и существует. Закрывается коннектом на уже
проверенный IP с сохранением SNI и `Host` либо, что надёжнее в проде, egress-политикой,
режущей RFC1918 и link-local.

**Нет ограничения частоты запросов.** Ключ статический, троттлинга нет; лимит на размер
тела запроса тоже держит только обратный прокси, которого в compose нет.
