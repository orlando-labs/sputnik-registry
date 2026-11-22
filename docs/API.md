# Sputnik Registry HTTP API v1

Статус: нормативный design для реализации. Актуализация: 2026-10-20.

## 1. Общие правила

Base `/api/v1`, JSON `application/vnd.sputnik.registry.v1+json`, artifact
`application/vnd.sputnik.package.v1` (raw tar.gz bytes, без HTTP Content-Encoding). UTF-8; timestamps RFC3339 UTC, digests
`sha256:` + 64 lowercase hex. Unknown write fields отклоняются (`unknown: :reject`
в Orbit params); method/HEAD/content rules следуют Orbit HTTP contract.
Reference/version grammar — [CLIENT](CLIENT.md). Endpoints не используют
percent-encoded slash для объединения scoped segments.

Root aliases закрепляются только оператором: `registry promote --ref publisher/name`.
GET/HEAD `/packages/name` и вложенные read endpoints используют те же releases,
owners и ограничения quarantine, что и `/namespaces/publisher/packages/name`.
Package response содержит `package_ref: "publisher/name"`, `requested_ref: "name"`.
Index, release и artifact metadata всегда содержат канонический `package_ref`;
artifact download URL остаётся scoped. Redirect не нужен. Поиск добавляет
`root_name` (или null) к canonical package row, без дубликата пакета в каталоге.
Публикация или изменение через root alias даёт `409 ROOT_ALIAS_READ_ONLY`.
Promotion идемпотентен; занятое имя даёт `409 ROOT_NAME_RESERVED`.

Ошибки: `{"error":{"code":"INVALID_REQUEST","message":"...","request_id":"..."}}`.
Message безопасен, bounded; client ветвится по code. Responses включают request id.
IDs — opaque URL-safe random strings, не пути. Metadata URLs absolute HTTPS
на canonical registry origin, кроме explicitly configured loopback development.

Write endpoints, кроме raw upload и device-login bootstrap (§4.1), требуют
registry API bearer и `Idempotency-Key`
(16–128 printable ASCII bytes). Key scope: account + action + target; canonical
request hash вычисляется после schema validation по типизированным полям.
Одинаковые key/body возвращают прежний resource/result; другой body — 409.
Record хранится 7 days, response секретного token — исключение ниже.
Параллельные retries сериализуются unique key. Auth rechecked перед replay;
replay не должен раскрыть ресурс после revoke. TTL key не снимает artifact-variant uniqueness.

## 2. Discovery и reads

`{base}` означает `/namespaces/{publisher}/packages/{name}` либо `/packages/{name}`.

| Метод | Путь после `/api/v1` | Содержание |
|---|---|---|
| GET | `/capabilities` | protocol/features/limits/trust profile |
| GET | `/me` | Authenticated identity/effective scopes/profile grants |
| GET | `/me/usage` | Own authoritative quotas/reservations/remaining usage |
| GET | `/packages?query=&cursor=&limit=` | Search: default 20, max 100 |
| GET | `{base}` | Package metadata, canonical ref, index URL |
| GET | `{base}/index?cursor=&limit=` | Все published active/yanked entries; default 100, max 100 |
| GET | `{base}/versions/{version}` | Common release metadata + список variants/availability |
| GET | `{base}/versions/{version}/artifacts/{variant}` | Metadata одного variant |
| GET/HEAD | `{base}/versions/{version}/artifacts/{variant}/download` | Exact bytes после release/variant availability check |
| GET | `{base}/stats/downloads?from=&to=&version=&variant=` | Daily full origin transfers/bytes, freshness/known gaps |
| GET | `{base}/stats/publications?from=&to=&version=&variant=` | Owner/publisher aggregates, requires stats scope |

Capability response:

```json
{
  "schema": "sputnik.registry.capabilities.v1",
  "protocol": 1,
  "features": ["scoped-references", "exact-dependencies", "latest-stable", "application-lock-v1", "module-source", "module-bytecode", "artifact-variants", "prebuilt-native"],
  "artifact_formats": ["sputnik.pkg.v1"],
  "trust_profile": "private",
  "read_access": "authenticated",
  "registration": "closed",
  "login_providers": [],
  "native_targets": ["macos-aarch64", "linux-x86_64-gnu"],
  "limit_profiles": {
    "standard": {"artifact_bytes": 67108864, "expanded_tar_bytes": 268435456, "member_bytes": 67108864, "upload_seconds": 600, "session_seconds": 3600},
    "native-large": {"artifact_bytes": 2147483648, "expanded_tar_bytes": 8589934592, "member_bytes": 4294967296, "upload_seconds": 7200, "session_seconds": 14400}
  },
  "limits": {"page_entries": 100, "release_variants": 32, "receiving_uploads_global": 2, "receiving_uploads_account": 1, "receiving_native_large_global": 1, "active_sessions_global": 64, "active_sessions_account": 3}
}
```

Поле `latest` на package page informational; client выбирает версию по index.
Если короткое имя не найдено, `404 NOT_FOUND` содержит `error.suggestions`.
В массиве до 20 полных имён с тем же именем пакета и доступными релизами.
Отсутствие совпадений обозначается пустым массивом.
Для чтения каталога в режиме `read_access=authenticated` нужны активная
учётная запись и разрешение `registry:read`. В режиме `read_access=public`
поиск, метаданные пакетов и вариантов, скачивание и статистика скачиваний
обрабатываются как анонимные запросы. Сервер игнорирует заголовок
`Authorization` на этих маршрутах. `/capabilities` доступен в обоих режимах
без авторизации. Публикация, `/me`, квоты и статистика публикаций требуют
авторизации в обоих режимах.
Scope/profile grants и точный usage schema — [ACCESS_AND_USAGE](ACCESS_AND_USAGE.md).
`registration=closed|open` не выводится из read/trust policy; при включённом
GitHub login `login_providers` содержит `github`. Provider secrets не публикуются.
При готовом CLI login capabilities включает feature `device-login`.
Stats ответы имеют `Cache-Control: no-store`, собственный aggregation watermark
и максимум 366 daily rows/1 MiB. `from/to` — UTC даты, интервал `[from,to)`;
default — последние 30 UTC дней с текущим partial day. Version/variant — filters,
не произвольный group-by. `/me` и `/me/usage` всегда `no-store`.
Index: `schema: sputnik.registry.index.v1`, `package_ref`, `revision`, `versions`,
`next_cursor` (string/null). Entries отсортированы по version descending.
Cursor HMAC-bound к query/ref/revision/last key, expires через 15 minutes;
смена revision — `409 INDEX_CHANGED`, client начинает заново с bounded retries.
Search использует порядок canonical_ref и свой catalog revision, те же правила.
CLI ограничивает суммарный index 10,000 versions/16 MiB и 3 restart attempts.
Page ограничена также 1 MiB: server может вернуть меньше `limit` entries,
сохраняя next_cursor. Одна normalized release metadata ограничена 768 KiB.

Один release имеет форму (illustrative values):

```json
{
  "schema": "sputnik.registry.release.v1",
  "package_ref": "slowpilot/example",
  "version": "1.2.0",
  "published_at": "2026-10-19T12:00:00Z",
  "yanked": false,
  "yank_reason": null,
  "dependencies": [
    {
      "package_ref": "slowpilot/base",
      "version": "1.0.0"
    }
  ],
  "root_module": "slowpilot.example",
  "modules": [
    "slowpilot.example"
  ],
  "capabilities": [],
  "artifacts": [
    {
      "variant": "macos-aarch64-cpu",
      "target": {
        "os": "macos",
        "arch": "aarch64",
        "min_os": "13.0",
        "cpu_features": [],
        "compute_backend": "none"
      },
      "native_mode": "prebuilt",
      "format": "sputnik.pkg.v1",
      "sha256": "sha256:<64-lowercase-hex>",
      "size": 12345,
      "url": "https://registry.example.test/api/v1/namespaces/slowpilot/packages/example/versions/1.2.0/artifacts/macos-aarch64-cpu/download",
      "compatibility": {
        "bytecode_versions": [],
        "profiles": [],
        "sputnik_requirement": ">=1.0.0 <2.0.0",
        "bytecode_languages": [],
        "sputnik_versions": ["1.0.0"]
      },
      "signatures": []
    }
  ]
}
```

Release common declarations immutable после первого variant. Поле artifacts
может дополняться новыми variants; existing variant metadata/digest неизменяемы.
Index использует compact variant records: id, target, native mode, compatibility,
digest/size/download URL; full module representations/imports/signatures отдаёт
exact artifact metadata endpoint. Common metadata не дублируется для каждого variant. Compact artifact record
также содержит exact `dependencies` delta из `[artifact.dependencies]`;
selected graph объединяет common и variant dependencies.

Exact artifact response schema `sputnik.registry.artifact.v1`: package_ref,
version, variant, target, native_mode (`none`, `source`, `prebuilt`, `mixed`),
format, sha256, size, url, dependencies (variant delta), module_representations,
compatibility, signatures.
Representations имеют name/source-or-bytecode/path/digest; target/runtime/import
requirements определяет [NATIVE_ARTIFACTS](NATIVE_ARTIFACTS.md). Inspector сверяет
manifest и binary headers без execution; semantic source compilation — client gate.
Quarantined release скрывает все variants; quarantine отдельного artifact скрывает
только его. Exact metadata/download/HEAD заблокированного ресурса возвращает 423.
Public metadata не содержит absolute checkout paths, secrets или binary bytes.

Capabilities limits/targets — фактические enabled profiles/platform adapters,
пример не обещает уже реализованные host gates. Native-large profile публикуется
только при operator enable; scope/grant на его использование проверяет create.
При отсутствии подходящего variant client сообщает incompatibility. Download
endpoint без variant не определяется: server не выбирает platform вместо client.

Metadata ETag: revision + representation/page hash; `If-None-Match` → 304 без body.
`Cache-Control: public, max-age=30, must-revalidate`; bearer/private ответы `no-store`.
Yank/quarantine/добавление variant увеличивает revision.
Download: 200/HEAD, Content-Type artifact, Content-Length, ETag artifact digest,
`X-Sputnik-SHA256`, `Cache-Control: no-store`, attachment filename из safe
components. GET/HEAD проверяют DB availability даже при conditional request;
quarantine всегда 423. V1 не реализует Range/resume и external redirects:
`Range` игнорируется с полным 200, `Accept-Ranges: none`. Обрыв требует retry.
Yanked exact URL доступен; разрешение новой установки проверяет CLI.

## 3. Publish sessions

### POST `/publish-sessions`

Body: `package_ref`, `version`, **required** `variant`, `expected_size` и
`expected_sha256`, optional `limit_profile` (default standard) и
`publisher_signature` по SECURITY. Client уже имеет
готовый artifact tar.gz v1, поэтому может вычислить оба значения.
Text prototype не является поддерживаемым package format. Проверяются grammar,
quota, права создания в namespace/ownership и наличие unscoped grant.
Metadata из body не становится release metadata: её извлекает inspector.

201: `id`, `state: created`, `expires_at`, `upload_method: PUT`, `upload_url`,
`upload_headers` с artifact Content-Type, `variant`, `limit_profile`, `status_url`.
TTL standard 1 hour, native-large 4 hours. 409 `ARTIFACT_EXISTS` при уже published
variant; `ARTIFACT_RESERVED` при другой активной session на тот же variant.
Reservation создаётся атомарно, package identity не захватывается бесконечным
неуспешным upload. Grant consumed первым successful release. Новый variant
existing release допускается, если common metadata совпадает; его изменение
даёт `RELEASE_METADATA_CONFLICT`. Quarantined/yanked release дополнить нельзя.

### PUT `/publish-sessions/{id}/artifact`

Bearer token того же account + текущие publish права. Raw body; Content-Length
или bounded chunked, без Content-Encoding. Idempotency-Key не нужен: session,
expected digest/size и upload lease задают identity. Body stream пишется только
в quarantine. Сверяются declared/actual/expected size и server SHA-256.
200: `id`, `state: uploaded`, `sha256`, `size`.

Повтор PUT завершённой session принимает только те же bytes (stream/hash без
замены файла) и возвращает те же upload metadata. Другие bytes — 409
`UPLOAD_CONFLICT`. Concurrent upload — 409 `UPLOAD_IN_PROGRESS`.
При disconnect temp удаляется, lease reclaim возвращает created.
Для validating/promoting/published upload отклоняется `SESSION_STATE_CONFLICT`.

### POST `/publish-sessions/{id}/commit`

Пустой JSON object; bearer + idempotency key. `uploaded -> validating` и outbox
в одной transaction. 202 с `id`, `state`, `status_url`. Повтор commit текущей
validating/promoting/published session возвращает текущее состояние без новой job;
для published 200. Commit incomplete upload — 409.

### GET `/publish-sessions/{id}`

Доступ только initiating account/operator, `Cache-Control: no-store`.
States: `created`, `receiving`, `uploaded`, `validating`, `promoting`, `published`,
`rejected`, `expired`, `cancelled`, `failed`. Published содержит release URL,
variant, artifact URL и digest.
Terminal failure: bounded `reason_code`, безопасное description, `retryable: false`
для **этой session**. Transient retry остаётся validating/promoting с
`next_attempt_at`. Новая session допустима после освобождения reservation.

### DELETE `/publish-sessions/{id}`

Bearer + idempotency key. Created/receiving/uploaded → cancelled, fences upload,
reservation освобождается. 200 с session state. Terminal cancel replay идемпотентен;
validating/promoting/published — 409. Session audit не удаляется.

## 4. Управление

| Метод | Путь | Семантика |
|---|---|---|
| POST | `{base}/versions/{version}/yank` | Body `{reason}`; bounded 512 bytes |
| POST | `{base}/versions/{version}/unyank` | Пустой body; quarantine запрещает |
| POST | `{base}/owners` | `{account_id, role: owner/publisher}` |
| DELETE | `{base}/owners/{account_id}` | Last active owner удалить нельзя |
| GET | `/tokens` | Только metadata tokens текущего account |
| POST | `/tokens` | `{label, scopes, expires_at}`; secret один раз |
| DELETE | `/tokens/{id}` | Немедленный revoke |
| GET | `/publisher-keys` | Metadata собственных public keys/bindings/status |
| POST | `/publisher-keys` | `{public_key, allowed_refs, allowed_namespaces}`; Ed25519 Base64 32 bytes, server-generated key id |
| DELETE | `/publisher-keys/{id}` | Revoke собственного publisher key, audit; новые публикации этим key запрещены |

Все mutation responses 200, создание token 201. На `/api/v1/tokens` требуется
explicit `token:manage` token; scopes/expiry нового token — подмножество
выдающего и live permissions. Первый token создаётся через CLI device login
(раздел 4.1) либо local operator command через тот же application layer.
Публичного operator-session endpoint нет.
Token list/create/revoke требуют `token:manage`; exception — revoke текущего
token им самим для `pkg logout`. Publisher key API требует `publisher-key:manage`
и own-account check. Key bindings ограничены live permissions account;
private keys не принимаются, public key не расширяет account/token permissions.
Token creation idempotency хранит metadata **без plaintext**: initial response
показывает secret, replay возвращает `409 TOKEN_SECRET_ALREADY_ISSUED` + token id.
При потере ответа token отзывается и создаётся новый. Plaintext не сохраняется
ради generic replay; это явное исключение, а не скрытое повторное создание token.

Минимальные scopes: `package:publish:<ref>`, `package:yank:<ref>`,
`package:owners:<ref>`, `package:stats:<ref>`, `registry:read`,
`namespace:create-package:<publisher>`, `namespace:publish:<publisher>`,
`token:manage`, `publisher-key:manage`.
Namespace publish покрывает exact package publish scopes в том же namespace,
но не даёт package ownership: для каждого write проверяется live package ACL.
Auth = scopes ∩ current permissions ∩ account/namespace/package active status.
Publisher/profile admission grants проверяются дополнительно; размеры, traffic
и storage budgets не выводятся из владения package. Tokens имеют default TTL
30 days/max 90 days, delegation не расширяет scopes/expiry выдающего token.
401 возвращает Bearer challenge; tokens принимаются только в Authorization
header по HTTPS. Default private read требует `registry:read`.
Create scope не даёт publish чужих существующих packages.
Для первой публикации новой собственной reservation `namespace:create-package`
разрешает create и initial publish; последующие releases требуют package publish
scope. Reserved package доступен только своей initiating session/owner; public
read его не показывает. Для первого unscoped publish operator выдаёт exact ref
publish scope вместе с grant.

Operator admin commands используют тот же application layer, local protected
control channel. V1 не выставляет публичный `/admin` до реализации step-up auth.
Команды: account/namespace provision/suspend, grant/revoke unused unscoped name
с allowed module prefixes, quarantine/release с reason, key import/revoke.
Изменение ownership, yank и quarantine транзакционно записывается в audit.
No-op mutation не увеличивает revision повторно.

### 4.1. CLI login и регистрация без frontend

Все пути ниже относительно `/api/v1`. Сценарий и limits —
[ACCESS_AND_USAGE, §1](ACCESS_AND_USAGE.md). Registry отдаёт JSON;
подтверждение code происходит на странице GitHub, не registry.

| Метод | Путь | Семантика |
|---|---|---|
| POST | `/auth/device` | `{provider: github, expected_account_id?}`; 201 с `attempt_id`, одноразово показанным `login_secret`, `user_code`, `verification_uri`, `expires_at`, `interval` |
| POST | `/auth/device/{id}/poll` | 200 `pending/verified/denied/expired/failed`, `retry_after`; verified содержит provider identity и existing account/personal namespace, без API token |
| POST | `/auth/device/{id}/complete` | `{namespace?, token: {label, scopes, expires_at}}`; 201 account/namespace/token metadata и token secret один раз |
| DELETE | `/auth/device/{id}` | Cancel; revoke только token, выданный этим attempt, если он уже completed; account/namespace сохраняются |

Start не требует credentials; остальные три пути требуют
`Authorization: Bearer <login_secret>` конкретного attempt. Этот secret не является
API token и отклоняется на всех остальных endpoints. User code не является
credential для poll/complete. Ни callback routes, ни cookies, ни HTML не нужны.
Start/poll/cancel не требуют idempotency key; repeated cancel — no-op.
Complete требует `Idempotency-Key`, scoped по attempt/action, и поддерживает
secret-once/409 replay semantics token creation. Start с потерянным ответом
повторяется новым attempt; прежний освобождает capacity по TTL.

Backend сам получает device code, опрашивает GitHub с per-attempt fencing и
provider interval и проверяет `/user` при каждом login. CLI не передаёт доверенное
утверждение «я пользователь X». Expected account — только ограничение:
несовпадение identity завершает attempt ошибкой, не переключает account молча.
В closed mode неизвестный subject получает `403 REGISTRATION_CLOSED`;
suspended/closed identity не создаёт новый account.

Verified attempt позволяет только complete/cancel, не package mutations.
Для нового account namespace обязателен; conflict/reserved slug —
`409 NAMESPACE_UNAVAILABLE`, без account/token creation и без consumption attempt.
Complete атомарно создаёт account/identity/namespace/owner/token; registration
mode и текущие права проверяются перед commit. Повторный login находит existing
account и не создаёт новый namespace. Scopes completion ограничены его live
permissions; exact scope на будущий package требует namespace create authority.
После consumed attempt повторная выдача secret запрещена; для revoke потерянного
token cancel доступен до attempt expiry, затем нужен новый login.
Все auth responses `no-store`; login secrets/provider codes/tokens redacted.

## 5. Operations и ошибки

Вне `/api/v1`, вне публичного CDN: `/health/live`, `/health/ready`, `/metrics`.
Protected edge/internal listener; readiness 200/503 без секретов. Metrics без
unbounded ref/user labels. HTML catalog и личный кабинет — optional future work,
не dependencies регистрации, публикации или public v1 gate.

| HTTP | Codes |
|---:|---|
| 400 | `INVALID_REQUEST`, `INVALID_REFERENCE`, `INVALID_VERSION`, `CURSOR_INVALID` |
| 401 | `AUTH_REQUIRED`, `TOKEN_INVALID` |
| 403 | `SCOPE_DENIED`, `NAMESPACE_DENIED`, `UNSCOPED_GRANT_REQUIRED`, `REGISTRATION_CLOSED` |
| 404 | `NOT_FOUND` |
| 409 | `ARTIFACT_EXISTS`, `ARTIFACT_RESERVED`, `RELEASE_METADATA_CONFLICT`, `IDEMPOTENCY_CONFLICT`, `INDEX_CHANGED`, `SESSION_STATE_CONFLICT`, `UPLOAD_CONFLICT`, `UPLOAD_IN_PROGRESS`, `LAST_OWNER`, `TOKEN_SECRET_ALREADY_ISSUED`, `NAMESPACE_UNAVAILABLE` |
| 413 | `ARTIFACT_TOO_LARGE` |
| 415 | `UNSUPPORTED_MEDIA_TYPE` |
| 422 | `ARTIFACT_INVALID`, `IDENTITY_MISMATCH`, `DIGEST_MISMATCH`, `POLICY_REJECTED`, `SIGNATURE_INVALID`, `COMPATIBILITY_UNSUPPORTED` |
| 423 | `RELEASE_QUARANTINED` |
| 429 | `RATE_LIMITED`, `QUOTA_EXCEEDED` |
| 503 | `DEPENDENCY_UNAVAILABLE`, `VALIDATOR_UNAVAILABLE`, `STORAGE_UNAVAILABLE` |

429/503 возвращают bounded `Retry-After`. Async validation errors находятся
в terminal session result; они не меняют ранее выданный 202 задним числом.
Unknown response fields допускаются клиентом; required fields/types, known
schema version и limits проверяются. До первого фактического применения
breaking changes включаются в v1; после фиксации опубликованного contract
несовместимое изменение требует отдельного решения о версии.

Language compatibility uses the shared compiler/VM SemVer release. Every compact artifact advertises `compatibility.sputnik_requirement`, verified from `requires.sputnik`. `bytecode_languages` and native `sputnik_versions` contain producer SemVer strings. Clients check all of these before selection/download. Legacy artifacts without a requirement are ineligible. Release common metadata includes `requires`; variants cannot disagree about the language requirement.
