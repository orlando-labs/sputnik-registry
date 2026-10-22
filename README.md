# Sputnik Registry

Статус: single-node service v1 на Sputnik + Orbit + SQLite WAL/local CAS;
публичное чтение, закрытая регистрация. Актуализация: 2026-10-21.

Код web/worker и локальные operator/client утилиты находятся в этом repository.
Исходники приложения целиком на Sputnik: CAS, spool и orchestration находятся
в `src/registry/storage.s`. Системные API предоставляет bundled SDK package
`system_io`, online backup — `sqlite3`; собственного native extension нет.
[Сборка, запуск, проверки и фактические ограничения](docs/RUNNING.md).
[Deployment на registry.sputnik-lang.online](docs/DEPLOYMENT.md).
GitHub Device Flow login/signup и SDK publication реализованы; detached signatures
остаются отдельной стадией. SDK устанавливает portable
source/bytecode из HTTP registry; основной hub по умолчанию —
`https://registry.sputnik-lang.online`. После публикации оператор назначает
короткое имя командой `registry promote --ref slowpilot/sputniktorch`.

Registry — источник библиотек для существующего `sputnik pkg install`.
Установщик помещает проверенные пакеты в общий `SPUTNIK_PKG_HOME`
(`~/.sputnik/packages` по умолчанию). Скрипты, native builds и notebook
используют существующий resolver; `import` не обращается в сеть.

Установка из исходной директории и `.sputnikpkg`, локальный filesystem registry,
приоритет проектного `packages/`, подготовка native libraries и удаление пакетов
сохраняются. HTTP transport использует тот же store и offline resolver.

## Зафиксированные решения

- Модульный монолит с отдельными ролями `web` и `worker`.
- Первый работающий профиль: управляемый оператором single-node registry,
  SQLite WAL и локальный CAS. PostgreSQL/S3 — последующее масштабирование.
- Public v1 включает CLI login через GitHub Device Flow, самостоятельное создание
  account/personal namespace и JSON API для tokens/publisher public keys.
  Собственный frontend и личный кабинет не требуются.
  Native admission и короткие имена сохраняют отдельные operator grants.
- Обычное имя: `publisher/package_name`; короткие имена выдаёт оператор.
  Package identity и имена импортируемых модулей различаются.
- Exact dependencies, установка конкретной версии и выбор последней stable
  входят в v1. Dependency ranges и backtracking resolver отложены.
- У release несколько platform variants; каждый `.sputnikpkg` immutable по
  reference/version/variant. Контейнер — стандартный tar.gz (`sputnik.pkg.v1`),
  отдельные files без hex. Для каждого module publisher
  выбирает source либо bytecode; mixed packages поддерживаются.
- Source — default для Sputnik modules; native extension выбирает source или
  prebuilt `.so`/`.dylib`/`.dll`. Совместимый prebuilt ставится без C/C++ build;
  native source build требует явного выбора. Возможны bundled dependent libraries.
- Release и каждый добавляемый variant становятся видны после проверки и
  durable finalize blob. Неуспешная попытка не занимает variant навсегда.
- Application lock v1 фиксирует source, reference, version, variant и SHA-256.
  Synthetic embedded lock прототипа в archive не переносится.
- Существующий Ed25519 используется для detached release signatures.
- До первого фактического применения все breaking changes входят в v1:
  текстовый прототип заменяется архивом без dual-reader и legacy-output режима.
  Прототипные artifacts/locks при необходимости пересоздаются.
- Download v1 проверяет availability; публичного CAS URL и долгоживущего
  immutable CDN cache, обходящего quarantine, нет.

Установка из основного хаба:

```sh
sputnik pkg install slowpilot/example --version 1.2.0
sputnik pkg install slowpilot/example
```

`--registry` меняет основной `https://registry.sputnik-lang.online` на другой
origin или filesystem registry. Публикация доступна через `registry publish`;
`sputnik pkg publish` также доступен после `sputnik pkg login`; application install
`--locked` остаётся целевым интерфейсом.
Локальный сценарий также работает:

```sh
sputnik pkg install /path/to/library
sputnik run train.s
```

## Документы

1. [Срез текущего состояния](docs/CURRENT_STATE.md).
2. [Архитектура и publication lifecycle](docs/ARCHITECTURE.md).
3. [Tar.gz layout и source/bytecode modules](docs/PACKAGE_FORMAT.md).
4. [Platform variants и prebuilt native libraries](docs/NATIVE_ARTIFACTS.md).
5. [CLI, resolver, locks и native packages](docs/CLIENT.md).
6. [HTTP API v1](docs/API.md).
7. [Модель данных](docs/DATA_MODEL.md).
8. [Проверка артефактов, подписи и эксплуатация](docs/SECURITY.md).
9. [Пошаговый план реализации](docs/IMPLEMENTATION_PLAN.md).
10. [Roadmap и зависимости](docs/ROADMAP.md).
11. [Native requirements, runtime и SDK bundles](docs/NATIVE_BUNDLES.md).
12. [Авторизация, upload quotas и статистика](docs/ACCESS_AND_USAGE.md).
13. [Достаточность Orbit и необходимые расширения](docs/ORBIT_INTEGRATION.md).

[ADR-0001](docs/decisions/0001-modular-monolith.md),
[ADR-0002](docs/decisions/0002-immutable-releases.md),
[ADR-0003](docs/decisions/0003-storage-profiles.md),
[ADR-0004](docs/decisions/0004-scoped-package-references.md),
[ADR-0005](docs/decisions/0005-existing-store-and-v1-boundaries.md),
[ADR-0006](docs/decisions/0006-tarball-and-module-representations.md),
[ADR-0007](docs/decisions/0007-platform-artifacts-and-prebuilt-native.md) и
[ADR-0008](docs/decisions/0008-optional-native-bundles.md)
фиксируют решения. Октябрьский design заменяет июльские неопределённости;
ADR-0005/0006/0007/0008 объясняют изменения.

## Текущая реализация

См. [RUNNING](docs/RUNNING.md): native build, config, bootstrap account/token,
запуск web/worker, публикация готового artifact, exact fetch, backup/restore.
[IMPLEMENTATION_PLAN](docs/IMPLEMENTATION_PLAN.md) сохраняет полный roadmap;
незакрытые пункты не означают отсутствие уже работающего service slice.
