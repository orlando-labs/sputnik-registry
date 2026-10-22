# Запуск private registry

Актуализация: 2026-10-20. Реализован первый закрытый профиль сервиса:
Sputnik/Orbit web, отдельный Sputnik worker, SQLite WAL, локальный CAS.
Сборка написана в `tools/build.s`; `tools/build` запускает её через Sputnik SDK.
Web, worker, supervisor, operator, publish/fetch и backup/restore входят в один
исполняемый файл `registry`. Python нужен только внешнему тестовому стенду.
Production-код registry не содержит C/C++ и native bindings. CAS, spool,
проверка private credentials и backup orchestration написаны на Sputnik.
Приложение использует общие SDK-модули `sdk.fs`, `sdk.digest`, `sdk.process`,
`sdk.pkg` из bundled package `sputnik-lang/packages/system_io` и
`sqlite3.Database.backup_into` из `sqlite3`. Native implementations
системных операций и package core остаются внутри зависимостей.

## Сборка

Проверяемый host — macOS aarch64. Нужны текущий Sputnik SDK, Orbit,
sqlite3, C/C++ compiler, OpenSSL, libarchive и zlib. Скрипт принимает
явные `--sdk`, `--orbit`, `--sqlite`, поэтому sibling checkout — только default.
Для критических транзакций используются SQLite repositories с bind parameters
и pool sqlite3. ORM не требуется для запуска этого среза.

```sh
./tools/build
```

Для SDK вне sibling checkout указать также executable для bootstrap:

```sh
SPUTNIK=/path/to/sdk/build/sputnik ./tools/build --sdk /path/to/sdk
```

Результат: `.build/native/registry`, `.build/build-result.json`,
`.build/inputs.json`. Последний фиксирует SHA-256 фактического compiler/runtime,
исходников и migration; dirty checkout не выдаётся за опубликованный dependency
lock. Сборка отклоняется при изменении входов во время compilation.
Native build обязан пройти `--require-full-native`.
При параллельной пересборке SDK использовать его зафиксированную локальную копию.

## Инициализация и запуск

Из корня repository:

```sh
mkdir -p var
.build/native/registry --config var/config.json init --data-dir "$PWD/var/data" --port 8080
.build/native/registry --config var/config.json account-create --label Publisher --namespace slowpilot
.build/native/registry --config var/config.json serve
```

В другом terminal:

```sh
.build/native/registry --config var/config.json worker
```

Account-create возвращает token один раз. Сохранить его в credential JSON
с mode 0600: `{"origin":"http://127.0.0.1:8080","token":"spreg_..."}`.
В SQLite хранится SHA-256 token, prefix, scopes и expiry. Начальный token
действует 30 дней. Ключ `cursor_secret` и config также должны быть mode 0600.
Operator-команды требуют локального доступа к data directory; remote admin API нет.

`origin` — canonical URL без завершающего slash. Plain HTTP разрешён только для
loopback development. Для deployment задать HTTPS origin и allowed_hosts,
оставить listener на loopback за TLS reverse proxy. Proxy не должен сам отдавать
CAS или кешировать `/download`; лимит body согласовать с enabled profiles.
Forwarded headers по умолчанию не доверяются.

Supervisor на Sputnik запускает worker-once в отдельном процессе, с deadline 960 секунд,
лимитом fd и Linux address-space cap 2 GiB (включая резервирование стеков потоков).
Docker deployment отдельно ограничивает память worker 512 MiB и задаёт
`MALLOC_ARENA_MAX=2` для многоядерных Linux hosts. Один worker защищён file lock.
После аварии job остаётся в SQLite; lease истекает через 960 секунд. Запуск
`worker-once` полезен для тестов и ручного обслуживания. Web использует 4 OS workers;
DB lease не удерживается на время upload/download.

## Публикация и получение артефакта

Сначала собрать `.sputnikpkg` существующим `sputnik pkg build`. Source,
bytecode и mixed — варианты одной версии с одинаковыми общими declarations.

```sh
.build/native/registry publish --artifact example.sputnikpkg --origin http://127.0.0.1:8080 \
  --credentials var/credentials.json --ref slowpilot/example --version 1.0.0 \
  --variant source --session var/example-session.json
.build/native/registry fetch --origin http://127.0.0.1:8080 --credentials var/credentials.json \
  --ref slowpilot/example --version 1.0.0 --variant source --out example-downloaded.sputnikpkg
sputnik pkg inspect example-downloaded.sputnikpkg
```

Publisher сохраняет idempotency keys и session id для повторного запуска.
Fetch проверяет exact identity/size/digest и не перезаписывает существующий файл.
Credentials привязаны к origin; redirects не выполняются.
SDK `sputnik pkg install` также поддерживает HTTP source для portable
source/bytecode и точных зависимостей. Основной hub по умолчанию —
`https://registry.sputnik-lang.online`; automatic native variant selection и
application install `--locked` остаются отдельной стадией R4.

Начальные scopes: registry:read, namespace:create-package, namespace:publish,
token:manage. Управление owners/yank/publication stats требует exact scopes.
Operator может выдать такой token локальной командой `token-issue`.

Дополнительный native/unscoped grant:

```sh
.build/native/registry --config var/config.json operator <<'JSON'
{"command":"grant","account_id":"ACCOUNT_ID","kind":"native","target":"slowpilot/example"}
JSON
```

Kinds: `native`, `native-large`, `unscoped`. Для большого профиля дополнительно
включить `native_large` в config и передать publisher `--profile native-large`.
`native-large` поддерживает точный package reference в `target`, как и `native`;
право проверяется при reservation, upload, commit и promotion. `target: "*"`
явно разрешает профиль всему аккаунту и для отдельного пакета не требуется.
SDK publisher использует `--allow-native-large` и профиль из manifest.
Root alias назначается после публикации:
`.build/native/registry --config var/config.json promote --ref slowpilot/sputniktorch`.
Другие operator-команды: `package-promote`, `token-issue`, `account-suspend`, `quarantine`;
см. функцию operator в `src/registry.s`. Quarantine проверяется до ETag/HEAD.

## GitHub login and self-service publication

Configure `github_client_id` with the OAuth App Client ID and set `registration`
to `open` for self-service signup (`closed` permits only already linked users).
The OAuth App must have **Enable Device Flow** enabled. No client secret is
required. The registry verifies `/user` after each exchange and binds the
numeric GitHub user ID, never the mutable login or email.

```sh
sputnik pkg login --namespace alice
sputnik pkg whoami
sputnik pkg build ./library --out ./library.sputnikpkg
sputnik pkg publish ./library.sputnikpkg
sputnik pkg logout
```

On first login choose an available personal namespace; the manifest name must
be `alice/library`, and module names must start with `alice.`. Login displays
the GitHub verification URL and code. Open that URL in a browser on any device.
Subsequent logins retain the same account/namespace, including after GitHub
renames. `--registry URL` selects another hub, with separate saved credentials.

The SDK stores credentials in `~/.sputnik/credentials/<SHA256(origin)>.json`
with mode 0600. `--credentials FILE` and `SPUTNIK_PKG_CREDENTIALS` override this.
Install automatically uses saved credentials when present. `logout` revokes
the current registry token before removing the file; network failures retain
credentials and are reported as failures. Tokens expire after 30 days by default.
The SDK keeps private publication state next to the archive, resumes the same
session on retry and never stores a recoverable registry token in that state.
Use `--session FILE` to place it elsewhere. Native grants and large-artifact
admission remain administrative decisions; `--allow-native-large` explicitly
enables the larger client upload profile.

Administrative `account-create` / `token-issue` remain available without GitHub.
To let a manually provisioned account use GitHub, an operator can link its
verified numeric GitHub ID through the local console:

```json
{"command":"github-link","account_id":"ACCOUNT_ID","subject":"NUMERIC_GITHUB_USER_ID"}
```

This never guesses identity from namespace/login/email and cannot transfer a
GitHub ID already linked to another account. Device codes live only in bounded
web-process memory; restarting web invalidates pending attempts. The database
keeps login-secret hashes and one-time completion metadata. GitHub access tokens
are discarded after identity verification. Cancellation revokes only the token
issued by the cancelled login, leaving other sessions and console tokens intact.

## Данные и статистика

`metadata.sqlite` содержит accounts, namespace/package ACL, hash-only tokens,
sessions/reservations, rolling upload traffic, audit, idempotency и outbox.
`blobs/<prefix>/<sha256>` — immutable CAS; `staging` — private upload files.
Архив инспектируется package core без compilation/dlopen. Blob fsync и
no-replace promotion предшествуют DB publication. Failed/cancelled sessions
освобождают reservation; worker удаляет старые staging orphans.

`usage.sqlite` — отдельная проекция. Publication events переносятся из
transactional outbox с dedup по event id. Download event появляется после
terminal transport result; учитываются успешные GET с полной payload length.
HEAD/304/short/error не увеличивают счётчик. Это origin transfers, не число
пользователей или выполнений import; peer consumption после приёма OS socket
не наблюдается. Spool ограничен двумя файлами по 16 MiB, append fsync; overflow
и torn tail помечают known_gap. Crash tail обозначается unknown в coverage.
Статистика может отставать до следующего worker tick.

Первый срез хранит event ledger без daily compaction/retention; объём usage DB
нужно контролировать до длительного deployment. Реализованы daily query,
date/version/variant filters, watermark, максимум 366 дней. Publisher/registry
signatures, retention и 24h soak не объявлены готовыми.

## Backup и восстановление

```sh
.build/native/registry --config var/config.json backup --out /absolute/path/backup-001
.build/native/registry --config /absolute/path/restored-config.json restore --backup /absolute/path/backup-001
```

Backup использует SQLite backup API, проверяет все referenced CAS digests и
записывает checksums. Для restore нужен новый отсутствующий data_dir и тот же
canonical origin. Незавершённые sessions становятся failed, publication outbox
проигрывается повторно с dedup; transport coverage получает gap. Не копировать
живые SQLite main files отдельно от WAL.

## Проверки и граница релиза

```sh
python3 test/integration.py
python3 test/integration.py --vm
make test-storage
```

Тесты используют loopback, отдельные data directories, настоящий package builder,
web и worker. Native operational subprocesses запускаются с `PATH=/nonexistent`:
init/account/operator, publish/fetch, supervisor и backup/restore не могут
полагаться на поиск Python или других helper commands через PATH.
Evidence находится в `.build/integration-native.json` и
`.build/integration-vm.json` после успешного завершения соответствующего набора.
Native report содержит SHA-256 проверенного executable. Тестовый SDK также
можно выбрать через `SPUTNIK=/path/to/sdk/build/sputnik`.
Набор включает malformed HTTP inputs, token/owner policy, native/prebuilt/provider
metadata, реальные гонки и kill worker, квоты, ошибки файловых операций,
повреждения CAS/backup, CLI transport, downloads после restore и outbox/spool.
Состав, команды и границы проверки описаны в [TESTING](TESTING.md).
Storage tests отдельно проверяют torn tail, overflow spool, повторную CAS promotion
и обнаружение повреждённого blob. SDK system_io содержит собственные VM/native
тесты symlink/traversal, private modes, no-replace link и descriptor inspection.
Отдельный full-native SDK test проверяет HTTP upload из файла больше 64 KiB,
chunked upload и пустой файл.
Наличие этого кода не означает закрытие всех R0–R9: native variant selection,
Ed25519, PostgreSQL/S3, CAS orphan GC, retention и длительные
fault/load/soak gates перечислены отдельно в implementation plan.
