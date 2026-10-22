# Текущее состояние: 2026-10-22

Опубликованы 12 пакетов, 51 вариант; всем назначены короткие root names.
Код отправлен в 10 публичных репозиториев GitHub под аккаунтом `orlando-labs`.
Канонический namespace в registry остаётся `slowpilot`, имена модулей сохраняют
префикс автора. `sqlite3-sputnik`, `sputnik-orm`, `sputnik-plot` переименованы
в `sqlite3`, `orm`, `plot`; `sputniktorch` полностью заменён на `torch`.
Старый canonical ref и root alias удалены и возвращают HTTP 404, старые
неиспользуемые blobs удалены. Совместимости со старым именем нет.

[Матрица выпуска, проверки и ограничения](PACKAGES_2026_10_22.md).
Новый `slowpilot/torch@0.1.2` содержит CUDA 13.0 prebuilt+runtime, macOS
CPU/MPS prebuilt+runtime и macOS source без bundle. Точный LibTorch 2.14.0
фиксируется version/ABI/build identity. Свежая установка нового CUDA-пакета из
публичного hub и CUDA Run через основной remote core 18765 прошли: forward=14,
gradient=[2,4,6], bytes и generator OK.

В SDK реализованы автоматический выбор совместимых native variants, явный
`--variant` и lock с canonical ref/version/variant/SHA-256/origin; технические
зависимости не заменяются несовместимыми версиями. При упаковке библиотек
проверены scoped imports, foreign bridges, installed macro providers и полные
имена базовых классов ORM. Оптимизация компилятора сохраняется. Публикация
пакетов не создаёт отдельный SDK release.

Registry работает на `20261022-4`, ранее проверены 772 native integration checks.
Native grants выданы только конкретным каноническим refs; native-large —
`slowpilot/torch`. Предыдущие отчёты развёртывания сохранены как исторические:
[CUDA](DEPLOYMENT_NATIVE_2026_10_22.md), [macOS](DEPLOYMENT_MACOS_2026_10_22.md).
Новые отчёты находятся в [package-release evidence](evidence/2026-10-22/package-release/).

## Исторический срез: 2026-10-16

Срез сделан по рабочим деревьям workspace, включая незакоммиченные изменения.
Это проверка доступного кода, а не утверждение о выпущенных версиях.
В `sputnik-registry` до актуализации были только июльские документы.
Срез ниже исторический: он описывает text prototype с marker `sputnik.pkg.v1`.
По указанию пользователя ещё не применённый v1 contract можно менять breaking.
Будущий tar.gz также называется v1: [PACKAGE_FORMAT](PACKAGE_FORMAT.md).
Дополнение от 2026-10-19: в `sputnik-lang` реализованы tar.gz builder,
inspector/verifier/extractor и загрузка извлечённых source/bytecode/mixed пакетов
через local overrides, включая один prebuilt native unit. Подробности и границы:
[package-distribution-v1.md](../../sputnik-lang/docs/engineering/package-distribution-v1.md).
В `sputniktorch` собраны шесть macOS aarch64 вариантов с LibTorch 2.14.0:
[package-building.md](../../sputniktorch/docs/package-building.md).
Все шесть включают runtime; три native-source также включают SDK headers.
В исходной шестёрке slim source отсутствует. Независимые runtime/SDK bundle profiles и native
providers приняты в design [NATIVE_BUNDLES](NATIVE_BUNDLES.md). Дополнение от
2026-10-20: local typed provider preparation и 15-combination builder реализованы;
новая matrix в `sputniktorch/build/packages-v1-bundles` отделена от старых шести
bundles. Managed selection/lock остаются следующей интеграцией.
Shared install/variant selection и сервис registry пока не реализованы для tar
format. Text compatibility и обязательной миграции prototype artifacts/locks
не требуется. Platform design — [NATIVE_ARTIFACTS](NATIVE_ARTIFACTS.md).
Дополнение от 2026-10-21: single-node registry развёрнут на
`https://registry.sputnik-lang.online`, реализованы publish/fetch и SDK HTTP
install portable source/bytecode в общий store, scoped references и постоянные
операторские root aliases (`registry promote --ref publisher/name`). Основной
hub читается публично и выбран в SDK по умолчанию; публикация авторизованная.
Дополнение от 2026-10-21 после внедрения GitHub: Device Flow login/signup и
SDK `pkg login/whoami/logout/publish` реализованы. Основной hub принимает
self-service GitHub регистрацию; console provisioning/token issue сохраняются.
Проверены 764 native integration checks и отдельные 50 VM auth checks.
Автоматический выбор platform/native variants и detached signatures ещё не реализованы.
Таблица ниже сохраняет исходный исторический срез.

Проверка Orbit от 2026-10-20: CLI Device Flow/account API не требуют framework
auth subsystem/frontend. Fixed-length streaming response добавлен в текущие
рабочие деревья `net.http` + Orbit; terminal transport result/counters ещё нужны.
В stdlib уже есть `fs.sync`/durable atomic write/exclusive create; bounded file
SHA-256 есть в C++ package core, но ещё не в Sputnik facade.
Подробный source audit — [ORBIT_INTEGRATION](ORBIT_INTEGRATION.md).

## 1. Что уже есть и что остаётся

| Область | Подтверждённое состояние | Следствие для registry |
|---|---|---|
| Общая установка | `pkg install` из registry directory, artifact и source directory; `SPUTNIK_PKG_HOME` | Переиспользовать установщик и общий store |
| Source directory | TOML либо YAML/YML/JSON build manifest; build manifest требует `--version` | Не нужен второй source installer |
| Resolver | Явные modules/links → project `packages/` → shared home; один build/version на graph | Registry не участвует в `import` |
| Native | Source/header blobs; подготовленная shared library, host/ABI/build cache; current descriptor один target/path/digest | Target variants/prebuilt distribution ещё нужно добавить; новый loader descriptor должен учитывать dependency libraries |
| Locks | Application `sputnik.packages.lock.v1`: name/version/digest; embedded `sputnik.lock.v1` отдельно | Добавить source в application v1 на месте; прототипные locks пересоздать |
| Versions | `store.cpp::version_less`, numeric release/prerelease ordering, stable preference | Comparator уже имеет основу; строгий registry subset и corpus ещё нужны |
| Transport | Registry сейчас filesystem-only | Нужен HTTP adapter и protocol v1 |
| References | `package.cpp::safe_registry_component`, `store.cpp::component` принимают одно имя | Scoped затрагивает publish/install/discover/remove/locks/CLI вместе |
| Artifact verification | C++ parse/verify, bytecode verifier в `read_package` | Нужен bounded, не исполняющий API для Sputnik-сервера |
| Crypto | `Signature` с Ed25519 уже есть, availability зависит от build backend | Нужны release signing/key management, не новый алгоритм |
| Package signature | Только `sputnik-sha256-dev-v1`; без signing key подпись может быть не проверена | `verify.ok` не означает authenticated publisher |
| HTTPS | Клиент и low-level HTTP server поддерживают TLS, trust/hostname verification | Повторная реализация TLS не нужна |
| Orbit | `1.0.0-rc.1`, streaming, strong params, telemetry, lifecycle, `orbit-orm` | Достаточно для private slice; production evidence не завершён |
| Orbit TLS wrapper | `App#server/start` не выставляет low-level TLS options | V1 остаётся за trusted edge |
| SQLite/ORM | Pool, task-local leases, explicit transactions, SQLite adapter | Можно начать с native executable + SQLite WAL |
| Distributed storage | PostgreSQL adapter/S3 client в workspace не найдены | Последующий этап, не условие private MVP |
| Хэширование | `Digest` предоставляет one-shot API | Потоковый upload SHA-256 и durable CAS primitives ещё нужно обеспечить |

## 2. Источники проверки

- [package-store-v1](../../sputnik-lang/docs/engineering/package-store-v1.md),
  [store.h](../../sputnik-lang/package/store.h),
  [store.cpp](../../sputnik-lang/package/store.cpp).
- [CLI](../../sputnik-lang/tools/sputnik/main.cpp): `run_pkg_command`, directory
  dispatch и native preparation.
- [Package API](../../sputnik-lang/package/package.h),
  [формат/verify](../../sputnik-lang/package/package.cpp),
  [package-v1](../../sputnik-lang/docs/engineering/package-v1.md).
- [Signature](../../sputnik-lang/docs/stdlib-signature.md),
  [implementation](../../sputnik-lang/runtime/stdlib_signature.cpp),
  [HTTP TLS](../../sputnik-lang/docs/engineering/http-tls.md),
  [Digest](../../sputnik-lang/docs/stdlib-digest.md).
- [Orbit](../../orbit/README.md), [readiness](../../orbit/docs/PRODUCTION_READINESS.md),
  [wrapper](../../orbit/src/orbit.s),
  [dependency lock](../../orbit/release/dependencies.lock.json).
- [ORM](../../orm/README.md), [SQLite](../../sqlite3/README.md).

Sputnik HEAD при проверке: `136c622`, Orbit: `908361f`, ORM: `3575602`,
SQLite: `96f3a62`. Эти commits не включают все текущие изменения.
R0 должен зафиксировать проверенные revisions и dirty patches либо дождаться
их включения в commits; одного номера HEAD для воспроизводимости мало.

## 3. Совместимость существующих библиотек

| Checkout | Identity в текущем manifest | Root/import | Действие до bootstrap publish |
|---|---|---|---|
| `orbit` | `orbit` | `orbit` | Curated grant и package pin |
| `orbit/packages/orbit-orm` | `orbit-orm` | `orbit.integrations.orm` | Delegated module prefix; исправить dependency identity |
| `orm` | `orm` | `orm` | Согласовать с dependency `orm` в Orbit companion |
| `sqlite3` | `sqlite3` | `sqlite3` | Curated grant; native trust и pinned build inputs |
| `plot` | `plot` | `plot` | Manifest identity, не имя repository |
| language `packages/progressbar` | `progressbar` | `progressbar` | Source install требует версии; первый smoke |

В `orbit-orm/sputnik.toml` dependency называется `orm`, тогда как
build manifest ORM объявляет `orm`. Это реальная несогласованность identity,
её нельзя маскировать alias на сервере. В R0 выбирается одно имя и согласованно
обновляются manifests/locks/examples. При текущем manifest естественное имя — `orm`.

Orbit clean-room lock ссылается на более старую toolchain revision и прежние
Git URLs. Его release evidence нельзя переносить на dirty workspace без нового
clean-room build. Gate 6 (canary/72h soak/fuzz/rollback) в readiness документе
остаётся открытым. В этой задаче он заново не запускался.

Существующие библиотеки не нужно массово переименовывать в `publisher/name`.
Для первого сетевого выпуска оператор выдаёт отдельные unscoped grants и
module prefixes. Для нового обычного пакета действует scoped policy.

## 4. Выполненная проверка

На имеющихся локальных binaries успешно выполнены:

- `build/package_tests`;
- `build/package_store_tests`;
- `python3 tests/package_directory_install_cli_test.py build/sputnik`;
- `python3 tests/package_remove_cli_test.py build/sputnik`.

Это подтверждает локальную базу для дизайна. Полный clean-room rebuild,
HTTP registry integration и production soak этим не подтверждаются.
