# Roadmap и зависимости

Актуализация: 2026-10-20. Подробные задачи/проверки —
[IMPLEMENTATION_PLAN](IMPLEMENTATION_PLAN.md).

## 1. Профили и gates

| Профиль | Состав | Gate |
|---|---|---|
| Existing local install | Directory/artifact/filesystem registry, shared home | Уже есть, сохранить regressions |
| Private registry v1 | Sputnik/Orbit/ORM/SQLite, local CAS/jobs, bounded inspector, HTTP CLI, lock v1 | R0–R6 |
| Managed public beta | Private stack + detached Ed25519 trust, CLI GitHub Device Flow/signup, edge/abuse controls, Orbit release evidence | R7–R8 |
| Public native packages | Curated publishers, explicit client native trust, scanner и portable build policy | После native gates R8 |
| Multi-instance | PostgreSQL/ORM adapter + private S3 + distributed lease parity | R9 |
| Extended accounts | Другие providers, automatic recovery/linking, organizations, step-up admin | Отдельный follow-up |
| Web UI | Read-only catalog, личный кабинет поверх JSON API | Optional follow-up, не dependency v1 |

Single-node не обещает HA. PostgreSQL/S3 не являются условием private vertical
slice или managed single-node beta. Public readiness требует собственного
release evidence, даже если приложение работает на этом стеке.

## 2. Уже реализованное: не заводить повторные проекты

- Shared home, source directory install, module index, local override, native
  preparation, uninstall и offline graph resolver.
- HTTP/HTTPS, Ed25519 в `Signature`, SHA-256/HMAC, secure random, JSON, filesystem.
- Orbit routing/streaming/lifecycle/strong params/security/telemetry.
- SQLite pools/task-local transactions и ORM SQLite adapter.

Остались integration/limits/trust/lock/source contracts. `Digest` one-shot:
streaming hash/durable file operations не возникают автоматически из наличия SHA-256.

## 3. Новые зависимости и место реализации

| Возможность | Где реализовать | Когда |
|---|---|---|
| PackageReference/version compare | Shared toolchain core + facade fixtures | R1 |
| Tar.gz archive, source/bytecode modes | Toolchain + pinned libarchive backend | R1a |
| Platform variants/prebuilt shared libraries | Target probe, package model/store/loader, static binary inspector | R1b |
| Native providers, runtime/SDK bundles | Core model/store/loader + sputniktorch recipes, default none | R1c |
| Bounded inspection API | Reusable C++ core + `sputnik-package` native-only facade | R2, blocker server publish |
| Atomic CAS/streaming hash IO | Private registry native adapter, затем возможное выделение | R2–R3 |
| HTTP registry client | В `sputnik`, поверх existing transport/install | R4, без отдельного client package |
| Application lock v1 с source/variant/digest | Existing package/store | R4 |
| Migrations/jobs/outbox | Registry internal modules | R3–R5 |
| Ed25519 release integration | Existing Signature + shared signed-payload contract | R7, primitive уже есть |
| GitHub Device Flow/login/signup | Registry internal provider adapter + JSON API/CLI, existing HTTPS/crypto; без нового general OAuth package/frontend | R8, до public self-service |
| PostgreSQL/ORM adapter | Новые packages `postgres-sputnik` и ORM adapter | R9 |
| S3 | Новый adapter/client, private bucket и parity suite | R9 |
| Malware scanner integration | Native admission adapter; package при повторном consumer | До public native |
| OIDC/OTLP/SPDX | Отдельные packages при соответствующей продуктовой задаче | После v1 |

Названия будущих packages — рабочие. Наличие записи в roadmap не означает
наличия библиотеки. General `sputnik-semver` не блокирует v1: нужен strict
parse/compare уже существующего exact/latest режима, ranges отложены.
General `sputnik-jobs`, `sputnik-blobstore`, `sputnik-migrate` не обязательны
до появления второго потребителя. Redis, broker и внешний search не требуются.

## 4. Что заменено относительно июльского baseline

- M0–M5 заменены R0–R9 с code locations и проверяемыми exit gates.
- Не нужно реализовывать Ed25519/TLS с нуля.
- Exact/latest install строится на нынешнем store; application lock v1
  расширяется source fields на месте.
- Artifact/manifest — tar.gz v1 с source/bytecode modules. Text prototype
  заменяется без legacy reader; prototype artifacts/locks пересоздаются.
  До первого фактического применения breaking changes допустимы в v1.
  Подписи detached, keyring explicit.
- Finalize blob предшествует published DB transaction.
- Rejected/expired/cancelled sessions освобождают version reservation.
- Immutable bytes не дают оснований обходить quarantine через public CDN CAS URL.
- Current short-import ecosystem вводится через curated grants без массового rename.

## 5. Platform binaries входят в v1

Release содержит несколько immutable variants с общей логической metadata.
Prebuilt и source extensions, target/ABI/runtime requirements, bundled dependent
libraries и native-large profile предусмотрены [NATIVE_ARTIFACTS](NATIVE_ARTIFACTS.md).
Поставка dependency runtime/SDK — независимая опция по
[NATIVE_BUNDLES](NATIVE_BUNDLES.md): default none, explicit runtime/sdk, typed
providers и build-only edges. Local builder/bindings реализованы; managed
selection/lock остаются gates R1c/R4.
Новый variant может публиковаться позже без overwrite; lock/signatures привязаны
к variant/digest. Это R1b до network launch, а не deferred post-v1 feature.
