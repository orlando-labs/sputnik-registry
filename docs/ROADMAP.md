# Roadmap Sputnik Registry

Статус: proposed.  
Срез существующих зависимостей: 2026-07-26.

## 1. Что уже существует

| Компонент | Состояние и применимость |
|---|---|
| Sputnik package tooling | `sputnik package-build/inspect/verify/install/publish`, reproducible `.sputnikpkg`, lockfile и filesystem registry реализованы |
| Sputnik stdlib | HTTP, JSON, URL, SHA-256/HMAC, secure random, time и filesystem primitives доступны; Ed25519 пока отсутствует |
| Ember | `1.0.0-rc.1`, routing, streaming, security, telemetry, lifecycle и ORM integration; canary и реальный 72-hour soak ещё открыты |
| `sputnik-orm` | модели, validation, queries, transactions и pools; production adapter сейчас только SQLite |
| `sqlite3-sputnik` | SQLite, transactions, prepared statements и task-aware pool |

Это позволяет начать domain/API работу и собрать private single-node
bootstrap. Полноценный public production registry без пакетов ниже не
объявляется готовым.

## 2. Необходимые, но пока несуществующие пакеты

Все имена в таблице — рабочие названия будущих отдельных Sputnik-пакетов.
Соответствующих готовых пакетов/репозиториев в текущем workspace нет. Не все
они блокируют exact-version bootstrap.

| Пакет | Приоритет | Зачем нужен | Минимальный критерий готовности |
|---|---:|---|---|
| `sputnik-package` | P0 | Безопасный parse/inspect/verify `.sputnikpkg` из Sputnik без shell-out в `sputnik` | bounded file/stream API, digest/signature verification, stable errors, hostile corpus и fuzz |
| `sputnik-semver` | P1 | Сравнение версий и dependency ranges после exact-version bootstrap | SemVer 2.0 parse/compare, constraint algebra, conformance corpus |
| `postgres-sputnik` | P1 | Надёжный multi-instance metadata store | TLS, prepared statements, transactions, cancellation, pool, PostgreSQL error mapping |
| `sputnik-orm-postgres` | P1 | PostgreSQL adapter при обязательном использовании `sputnik-orm` | schema binding, CRUD/query compiler, transactions, pool integration, parity suite |
| `sputnik-blobstore` | P1 | Единый порт для local CAS и object storage | bounded streaming put/get, quarantine/finalize, stat, delete-orphan, digest contract |
| `s3-sputnik` | P1 | S3-compatible production storage | SigV4, TLS, multipart upload, retry/idempotency, checksums, presigned URL |
| `sputnik-jobs` | P1 | Outbox workers, validation, cleanup и reconciliation | DB-backed leases, at-least-once delivery, retry/backoff, idempotency и dead-letter |
| `opentelemetry-sputnik` | P2 | Экспорт registry/ORM/job traces и metrics из локального Ember telemetry | OTLP/HTTP exporter, batching/backpressure, redaction, bounded queue, shutdown flush |
| `clamav-sputnik` | P2 | Admission scanning public packages, особенно native blobs | streaming daemon protocol, time/size limits, stable verdicts, fail-open запрещён |
| `oidc-sputnik` | P2 | Безопасный публичный account lifecycle без собственной password базы | Authorization Code + PKCE, issuer/JWKS validation, nonce/state, key rotation |
| `spdx-sputnik` | P2 | Нормализованные license expressions и policy | SPDX expression parser, current license list snapshot, validation/update process |

### Обязательность по профилям

- Private exact-version bootstrap: `sputnik-package` и сетевой режим в `sputnik`
  обязательны; `sputnik-semver` ещё не нужен.
- Resolver с ranges/latest: дополнительно `sputnik-semver` либо эквивалентный
  SemVer module в stdlib.
- Public single-node beta: `sputnik-blobstore`, `sputnik-jobs`, `clamav-sputnik`,
  Ed25519 в stdlib/toolchain и завершённый Ember soak.
- Multi-instance production: все P0/P1 плюс telemetry exporter.
- Public self-service accounts: дополнительно `oidc-sputnik`.
- License policy/search: дополнительно `spdx-sputnik` и расширение package
  manifest.

`redis-sputnik`, отдельный search-engine client и message broker намеренно не
являются обязательными: rate limits, outbox и начальный search должны работать
на основной базе. Их можно добавить только после измеренного bottleneck.

## 3. Что означали спорные рабочие названия

### `sputnik-semver`

Это маленькая pure-библиотека для:

- разбора `1.2.3`, prerelease и build metadata;
- стабильного сравнения версий;
- проверки constraints вроде exact, caret, tilde и ranges;
- выбора подходящей версии resolver.

Registry может хранить и отдавать exact opaque versions без неё. Она нужна
только когда `sputnik` должен выбрать «последнюю совместимую» версию и проверить
dependency ranges. Поэтому пакет понижен с P0 до P1. Если SemVer станет
частью языка, рабочее имя `sputnik-semver` исчезает, а API переезжает в stdlib;
важен общий contract и conformance corpus, а не отдельный repository.

### Миграции вместо `sputnik-migrate`

Registry нужны версионированные изменения database schema: создать таблицу,
добавить индекс, выполнить backfill и знать, какая версия schema установлена.
Но это не означает, что уже нужен общий пакет `sputnik-migrate`.

На первом этапе checksummed SQL migrations и маленький runner живут внутри
Sputnik Registry. Выделение reusable package оправдано только после появления
второго Sputnik-сервиса с теми же требованиями.

### Сетевой клиент вместо `sputnik-registry-client`

Под этим именем подразумевалась логическая часть, которая:

- обращается к index API;
- публикует artifact;
- скачивает `publisher/package_name`;
- проверяет SHA-256;
- пишет registry origin и digest в lockfile.

Это не зависимость registry server. Поскольку существующий `sputnik` написан
внутри toolchain и уже владеет локальными `package-*` командами, сетевую
поддержку правильнее реализовать прямо в `sputnik`. Отдельный
`sputnik-registry-client` package из roadmap удалён.

### Ed25519

Ed25519 — алгоритм цифровой подписи, а не шифрования. Он является частью
базовой цепочки доверия формата `.sputnikpkg`, поэтому отдельный
`ed25519-sputnik` создавал бы bootstrap dependency. Решение: добавить Ed25519 в
crypto surface Sputnik stdlib/core, дать package tooling тот же примитив и
зафиксировать signature format, key id, keyring и rotation в toolchain.

## 4. Изменения, нужные в самом Sputnik toolchain

Это не application packages и потому отслеживаются отдельно:

1. зафиксировать `publisher/package_name` как default reference и curated
   `package_name` как unscoped reference;
2. разбирать scoped reference на два безопасных компонента до filesystem
   mapping; текущий `safe_registry_component` не принимает `/`;
3. поддержать тот же reference в manifest, dependency keys, artifacts и
   lockfiles; оставить module names без `/` и проверять `publisher.` prefix
   для modules scoped package;
4. сделать версию manifest/package format явной;
5. определить exact-version network registry protocol и конфигурацию registry
   sources прямо в `sputnik`;
6. обеспечить lockfile pinning по canonical reference, source registry,
   version и artifact digest;
7. добавить Ed25519 в stdlib/core и package tooling, затем заменить dev
   signature trust model с key id и rotation;
8. после bootstrap формализовать dependency constraints и подключить SemVer;
9. добавить metadata: license, repository URL, Sputnik compatibility.

До согласования пунктов 1–3 сервер не должен изобретать несовместимую
канонизацию отдельно от compiler/client.

## 5. Этапы

### M0 — контракты и security baseline

- принять ADR из этого проекта;
- зафиксировать scoped/unscoped package reference и API v1 schemas;
- определить exact-version bootstrap и отдельно SemVer resolver profile;
- определить выдачу/отзыв неиспользованных unscoped grants;
- подготовить threat model и abuse cases;
- задать package/manifest/module/native limits;
- определить key management, yank, quarantine и retention policy;
- описать compatibility matrix Sputnik/Ember/package format.

Exit gate: один versioned contract одинаково понятен server, client и compiler.

### M1 — package protocol foundation

- реализовать `sputnik-package`;
- добавить scoped references в manifest/artifact/lockfile;
- реализовать exact-version network registry support прямо в `sputnik`;
- добавить versioned SQL migrations и runner внутрь registry;
- реализовать `sputnik-semver` перед включением ranges/latest;
- добавить cross-project conformance corpus.

Exit gate: `sputnik` публикует и скачивает
`publisher/package_name@exact-version` через protocol v1, а server независимо
проверяет identity и все digests.

### M2 — private bootstrap

- модульный монолит на Sputnik/Ember;
- `sputnik-orm` + SQLite WAL;
- local CAS и quarantine;
- accounts, scoped tokens и package ownership;
- publisher namespaces и operator-approved unscoped grants;
- publish session, immutable version, yank;
- public index/download;
- audit/outbox и single-node worker;
- backup/restore и integrity reconciliation.

Exit gate: clean-room end-to-end, race/idempotency/crash recovery tests и
24-hour private soak без утечек или orphan growth.

### M3 — production storage

- `postgres-sputnik` и `sputnik-orm-postgres`;
- `sputnik-blobstore` и `s3-sputnik`;
- `sputnik-jobs`;
- online migration SQLite -> PostgreSQL через export/import с digest audit;
- multi-instance leases, rolling deploy и CDN caching;
- failure injection для DB/object store/worker restarts.

Exit gate: multi-replica canary, backup/restore rehearsal, zero metadata/blob
divergence после fault suite.

### M4 — supply-chain hardening

- Ed25519 в stdlib/core и package tooling, production keyring;
- `clamav-sputnik`;
- native extension policy;
- tamper-evident audit export;
- namespace dispute, takedown и incident workflows;
- dependency graph и reverse-dependency read model.

Exit gate: независимый security review, zero open critical/high и
воспроизводимый hostile artifact corpus.

### M5 — public launch

- `oidc-sputnik`, account recovery и organization governance;
- `opentelemetry-sputnik`;
- rate/size/storage quotas и abuse response;
- `spdx-sputnik` после manifest metadata extension;
- документация publishers/consumers/operators;
- production SLO dashboards и alerts;
- 72-hour mixed-load soak за trusted edge.

Exit gate: SLO baseline, on-call/runbook, restore drill, key rotation drill и
утверждённый public launch review.

## 6. Что не следует делать раньше времени

- не выделять микросервисы до измеренной границы масштабирования;
- не добавлять Redis, Kafka или отдельный search cluster «на будущее»;
- не выполнять package code для получения metadata;
- не использовать `sputnik` subprocess как скрытый production parser;
- не выделять internal migration runner и network client в пакеты без
  повторного потребителя;
- не принимать replace/delete опубликованной версии;
- не освобождать опубликованное short name для нового проекта;
- не объявлять SQLite bootstrap высокодоступным public registry;
- не обходить незавершённую Ed25519 цепочку доверия dev signature.
