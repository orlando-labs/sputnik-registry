# Sputnik Registry

Статус: верхнеуровневая архитектура, без кода.  
Дата среза: 2026-07-26.

Sputnik Registry — сервер публикации, поиска и скачивания библиотек Sputnik,
аналогичный по назначению PyPI и RubyGems.

## Зафиксированный стек

- язык сервиса: Sputnik;
- HTTP и web: Ember;
- доступ к данным: `sputnik-orm`;
- bootstrap-профиль: `sqlite3-sputnik` и локальное content-addressed
  файловое хранилище;
- целевой production-профиль: PostgreSQL и S3-совместимое объектное
  хранилище после появления перечисленных в roadmap пакетов.

Сервис проектируется как модульный монолит. HTTP-процесс и фоновые worker
процессы собираются из одного приложения, но запускаются в разных ролях.
Артефакты `.sputnikpkg` неизменяемы, адресуются по SHA-256, а отзыв версии
реализуется через yank/quarantine, а не замену файла.

## Что входит в первую версию

- публичный индекс пакетов и версий;
- публикация `.sputnikpkg` через версионированный машинный API;
- scoped package references вида `publisher/package_name` по умолчанию;
- короткие `package_name` только по предварительному разрешению операторов;
- установка и dependency declarations по тому же package reference;
- неизменяемые версии и проверка совпадения имени/версии с артефактом;
- скачивание с проверяемым SHA-256;
- владельцы пакета и scoped API-токены;
- yank без удаления артефакта;
- аудит административных и издательских действий;
- health, readiness, метрики и структурированные логи;
- совместимый bootstrap-профиль для одного узла.

Не входят в первую версию: произвольный source hosting, сборка пакетов на
сервере, выполнение загруженного кода, dependency resolver внутри сервера,
социальные функции, платные организации и глобальная multi-region запись.

## Документы

- [Архитектура](docs/ARCHITECTURE.md)
- [Контракт HTTP API](docs/API.md)
- [Модель данных](docs/DATA_MODEL.md)
- [Roadmap и отсутствующие пакеты](docs/ROADMAP.md)
- [ADR-0001: модульный монолит](docs/decisions/0001-modular-monolith.md)
- [ADR-0002: неизменяемые релизы и CAS](docs/decisions/0002-immutable-releases.md)
- [ADR-0003: два storage-профиля](docs/decisions/0003-storage-profiles.md)
- [ADR-0004: scoped package references](docs/decisions/0004-scoped-package-references.md)

## Проверенные ограничения текущего стека

На дату среза Sputnik уже умеет воспроизводимо собирать, инспектировать,
проверять и публиковать `.sputnikpkg` в локальный файловый registry через
`sputnik package-*`. При этом:

- registry пока только файловый, сетевого протокола нет;
- package tooling принимает только одно filesystem-safe имя; scoped reference
  с `/` ещё нужно добавить в manifest, lockfile и `sputnik`;
- API разбора и проверки артефакта существует в C++ части компилятора, но
  недоступен серверу как библиотека Sputnik;
- текущая `sputnik-sha256-dev-v1` подпись предназначена только для локальных
  smoke-тестов, а не для публичной цепочки доверия;
- версия и dependency constraint сейчас являются строками; exact-version
  install возможен без SemVer, а ranges/latest selection потребуют общего
  SemVer-контракта;
- `sputnik-orm` поддерживает SQLite, но не содержит мигратора и PostgreSQL
  адаптера;
- Ember находится в release-candidate состоянии: программные production
  gates закрыты, но canary и реальный 72-часовой soak ещё не завершены.

Эти пробелы не скрыты внутри приложения: необходимые новые пакеты и их
критерии готовности перечислены в [roadmap](docs/ROADMAP.md).
