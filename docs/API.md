# Sputnik Registry HTTP API v1

Статус: proposed contract, без реализации.  
Дата: 2026-07-26.

## 1. Общие правила

- базовый prefix: `/api/v1`;
- JSON media type: `application/vnd.sputnik.registry.v1+json`;
- artifact media type: `application/vnd.sputnik.package.v1`;
- UTF-8, timestamps в UTC/RFC 3339;
- обычный package reference имеет вид `publisher/package_name`;
- unscoped `package_name` принимается только для operator-approved packages;
- publisher и package name канонизируются как отдельные компоненты;
- bootstrap может поддерживать только exact version; version ranges и
  latest selection включаются после принятия общего SemVer-контракта;
- неизвестные JSON fields отклоняются на write endpoints;
- каждый error response содержит стабильные `code`, `message`, `request_id`;
- секреты и детали parser/SQL ошибок наружу не возвращаются.

Write endpoints требуют bearer token и `Idempotency-Key`. Повтор с тем же
ключом и тем же canonical request возвращает тот же результат. Повтор с
другим содержимым возвращает `409 IDEMPOTENCY_CONFLICT`.

## 2. Public read API

В таблицах ниже `{package-base}` означает один из двух route prefixes:

- scoped: `/namespaces/{publisher}/packages/{package}`;
- curated unscoped: `/packages/{package}`.

Slash внутри `{package}` не кодируется: scoped route имеет отдельный path
segment для publisher, что соответствует правилам Ember.

| Метод и путь | Назначение |
|---|---|
| `GET /packages?query=&cursor=&limit=` | поиск и cursor pagination |
| `GET {package-base}` | карточка пакета, owners и latest stable |
| `GET {package-base}/index` | компактный машинный индекс видимых версий |
| `GET {package-base}/versions/{version}` | metadata одной версии |
| `GET {package-base}/versions/{version}/artifact` | redirect/stream `.sputnikpkg` |
| `HEAD {package-base}/versions/{version}/artifact` | digest, size и availability |

Index entry содержит как минимум:

- canonical package reference;
- version;
- yanked flag и безопасную публичную причину;
- publish timestamp;
- artifact SHA-256 и byte size;
- artifact URL;
- dependency strings в порядке canonical representation;
- root module и список экспортируемых module names;
- required capabilities;
- признак native extensions и их target triples;
- минимальную совместимую версию Sputnik, когда manifest format начнёт её
  объявлять.

Yanked release остаётся доступен по exact URL и digest, но не предлагается
обычному resolver. Quarantined release возвращает недоступность и не
показывается в публичном index.

Read responses поддерживают `ETag` и `If-None-Match`. Cursor является opaque,
подписанным и bounded. Offset pagination не входит в публичный контракт.

## 3. Publish sessions

### `POST /publish-sessions`

Создаёт reservation для `package reference + version`.

Поля запроса:

- `package_ref`;
- `version`;
- `expected_size` — необязательный ранний limit check;
- `expected_sha256` — необязательная проверка клиента.

Ответ содержит:

- `id`;
- `state`;
- `upload_method`;
- `upload_url`;
- обязательные upload headers;
- `expires_at`;
- status URL.

Для `publisher/package_name` subject должен иметь право создавать packages в
namespace `publisher` либо быть owner/publisher существующего package.
Первичная scoped публикация атомарно создаёт package.

Для unscoped `package_name` требуется не только privileged subject, но и
неиспользованный operator grant/reservation именно этого имени. Grant
поглощается первой успешной публикацией. Опубликованное короткое имя не
перераспределяется.

### `PUT /publish-sessions/{id}/artifact`

Bootstrap upload endpoint. Тело — только raw `.sputnikpkg`. Сервер:

- применяет byte/time/rate limits;
- пишет только в quarantine;
- вычисляет SHA-256 сам;
- не доверяет filename, client digest или metadata;
- не удерживает полный artifact в памяти.

Production может вернуть signed object-storage URL вместо этого endpoint, но
последующий commit и server-side verification остаются обязательны.

### `POST /publish-sessions/{id}/commit`

Переводит полностью загруженную сессию в `validating`. Повтор идемпотентен.
Ответ `202` содержит status URL.

### `GET /publish-sessions/{id}`

Возвращает состояние:

- `created`;
- `receiving`;
- `uploaded`;
- `validating`;
- `published`;
- `rejected`;
- `expired`;
- `failed`.

Для `published` возвращаются version URL и SHA-256. Для `rejected` возвращается
стабильный reason code и безопасное описание. Внутренние scanner/parser
сообщения остаются в защищённом аудите.

### `DELETE /publish-sessions/{id}`

Отменяет только незавершённую сессию. Уже опубликованную версию удалить или
заменить через этот endpoint нельзя.

## 4. Управление релизом

| Метод и путь | Назначение |
|---|---|
| `POST {package-base}/versions/{version}/yank` | исключить релиз из resolver |
| `POST {package-base}/versions/{version}/unyank` | вернуть релиз |
| `POST {package-base}/owners` | добавить owner/publisher |
| `DELETE {package-base}/owners/{account}` | удалить роль, кроме последнего owner |

Yank request содержит bounded public reason. Операция не изменяет artifact и
создаёт audit event. Unyank не разрешён, если действует operator quarantine.

Административный quarantine расположен в отдельном `/api/v1/admin` namespace,
требует отдельного scope и повышенной аутентификации. Он не является обычным
инструментом владельца.

## 5. Tokens

| Метод и путь | Назначение |
|---|---|
| `POST /tokens` | создать scoped token, plaintext показывается один раз |
| `GET /tokens` | список metadata без secret |
| `DELETE /tokens/{id}` | немедленный revoke |

Token scopes package-specific по умолчанию. Глобальный publish token не
выдаётся без явно привилегированной operator policy.

Операции с curated именами находятся в `/api/v1/admin/unscoped-grants`.
Grant содержит конкретные `subject` и `package_name`, reason и expiry до
первой публикации. Уже опубликованный reference grant API не освобождает.

## 6. Operations

Operations endpoints находятся вне публичного CDN:

- `GET /health/live`;
- `GET /health/ready`;
- `GET /metrics`.

Их авторизует trusted edge. Они не раскрывают connection strings, filesystem
paths, scanner responses, package contents или exception messages.

## 7. Основные status/error codes

| HTTP | Code | Смысл |
|---:|---|---|
| 400 | `INVALID_REQUEST` | shape/encoding не соответствует контракту |
| 401 | `AUTH_REQUIRED` | token отсутствует или недействителен |
| 403 | `SCOPE_DENIED` | token валиден, но права недостаточны |
| 403 | `NAMESPACE_DENIED` | нет права создавать пакет в publisher namespace |
| 403 | `UNSCOPED_GRANT_REQUIRED` | короткое имя не разрешено этому subject |
| 404 | `NOT_FOUND` | ресурс не существует или не виден subject |
| 409 | `VERSION_EXISTS` | `package reference + version` уже занята |
| 409 | `IDEMPOTENCY_CONFLICT` | ключ повторён с другим запросом |
| 413 | `ARTIFACT_TOO_LARGE` | превышен лимит |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | неверный media type |
| 422 | `ARTIFACT_INVALID` | формат, identity, digest или signature невалидны |
| 422 | `POLICY_REJECTED` | artifact валиден, но запрещён registry policy |
| 423 | `RELEASE_QUARANTINED` | скачивание отключено оператором |
| 429 | `RATE_LIMITED` | превышен bounded quota |
| 503 | `DEPENDENCY_UNAVAILABLE` | обязательное хранилище/validator недоступны |

Response schema versioned независимо от HTML-сайта. Несовместимое изменение
создаёт `/api/v2`, а не меняет смысл существующих полей v1.
