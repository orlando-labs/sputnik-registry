# Validation, trust и эксплуатация v1

Статус: design для реализации. Актуализация: 2026-10-20.

## 1. Threat boundaries

| Вход/угроза | Контроль |
|---|---|
| Hostile artifact | Bounded parser, bytecode validation, isolated worker, без execution |
| Namespace/dependency takeover | Canonical identity, grant/owner checks, bound source и lock digest |
| Partial/corrupt upload | Server hash/size, quarantine, durable finalize до DB publish |
| Replay/duplicate/race | Idempotency, reservations, unique releases, generation fencing |
| Token leakage/escalation | Random opaque tokens, hash-only storage, scope intersection, redaction |
| Login/account takeover | GitHub Device Flow, verified subject identity, secret-bound expiring attempts, expected-account check, no email auto-link |
| Native source/prebuilt | Explicit native trust, static binary/target/import checks, no install-time dlopen, controlled publishers |
| Revoked release в CDN | Private CAS, availability gate, no-store downloads |
| Disk/RAM/queue exhaustion | Hard byte/count/time/storage limits, admission и finite retries |
| TLS/proxy spoofing | Trusted edge, exact hosts/proxy trust; client TLS verification |

Registry никогда не открывает bytecode в RuntimeWorld, не импортирует modules
для metadata, не загружает shared library. Инспектор переиспользует C++ parsing
core, но не `sputnik` subprocess, compiler pipeline, loader или VM execution.
Наличие native source не меняет это правило.

## 2. Bounded PackageInspector

Плановый package `sputnik-package`: Sputnik public API поверх native leaves.
Рабочий контракт (имя API будет зафиксировано fixtures в R2):
`inspect_file(path, limits:) -> Result[Inspection, InspectionError]`.
Input file принадлежит registry quarantine; caller не передаёт произвольный
публичный path. Inspection возвращает только bounded normalized values.
C++ extension ABI передаёт scalars/flat lists/opaque handles, Sputnik facade
строит Maps и structured errors; нельзя предполагать поддержку Map в C ABI.

Inspector:

1. Ограничивает compressed/expanded tar/member/header/path lengths; проверяет
   gzip completion/CRC, overflow, duplicate/sparse/link entries и trailing input.
   Strict archive rules — [PACKAGE_FORMAT](PACKAGE_FORMAT.md). Text prototype
   не поддерживается как второй format; требует пересборки artifact.
2. Проверяет manifest/inventory и каждый source/bytecode/native/resource digest;
   whole compressed artifact SHA-256 считает независимо. Dependency declaration
   не принимается за hash реально выбранного artifact.
3. Проверяет полноту и взаимное соответствие manifest/module/native tables,
   root/module names, exact dependencies, capability names и native metadata.
4. Bytecode modules валидирует штатным verifier без выполнения. Source modules
   проверяет на bounded syntax/header/policy без macro expansion/compilation.
   Unsupported bytecode/ABI/profile — явная ошибка; semantic source compilation
   и cross-module linking выполняет client при install, не server.
5. Проверяет paths/дубли/escape/service names и native build-option policy;
   для prebuilt — ELF/Mach-O/PE headers, machine/OS/libc requirements, imports,
   ABI declarations и inventory. Не исполняет ABI query или package-provided tools.
6. Отдельно возвращает signature_present/checked/valid. Dev signature без key
   не является publisher identity. Detached signing проверяет admission layer.

Текущий text prototype parser материализует несколько копий; он заменяется
в пределах v1. Archive reader вводит streaming tar/gzip backend, member
validation и budgets до native facade. В libarchive разрешены только tar/gzip,
без external filters и generic extract-all. Source inspection не запускает
compiler/macros и не является доказательством semantic correctness.

## 3. Начальные limits bootstrap

| Ресурс | Default hard limit |
|---|---:|
| Artifact bytes на wire/disk, standard profile | 64 MiB; native-large задаётся отдельно |
| JSON write body / metadata response page | 64 KiB / 1 MiB |
| Manifest / checksum inventory | по 1 MiB |
| Modules / extension source/header inputs / dependencies | 2048 / 512 / 256; provider SDK files use archive member cap |
| Module name / normalized metadata | 255 bytes / 768 KiB |
| Один regular file / expanded tar stream | 64 MiB / 256 MiB |
| Archive members | 10,000 |
| Path bytes / parser entries | 1024 / 100,000 |
| Concurrent uploads / active sessions | 2 global, 1/account; 3 active sessions/account, 64 global |
| Pending expected upload bytes | 512 MiB global, 192 MiB per account |
| Session TTL / upload total / idle | 1 h / 600 s / 30 s |
| Inspector concurrency / wall deadline / RSS | 1 / 120 s / 512 MiB |
| Minimum free disk | max(1 GiB, 10% volume) |
| Release versions per package / variants per release | 10,000 / 32 |

Это policy defaults, не измеренные capacity claims. Profiles, rate/traffic/storage
quotas и disk reservations сведены в [ACCESS_AND_USAGE](ACCESS_AND_USAGE.md).
Native-large допускает максимум один receiving upload global и inspector
deadline 900 s при прежнем RSS 512 MiB. Compressed upload и expanded tar имеют
разные budgets; decompression bomb отвергается независимо от степени
сжатия. Worker disk admission учитывает worst-case expanded staging budget.
Upload quotas резервируются по expected compressed size до upload; Content-Length/
actual bytes не могут расширить reservation.
Операторы могут увеличить limits после corpus/load проверки; capabilities
публикует фактические limits. Client применяет минимум server и local limits. Большие bundled runtimes
используют operator-enabled native-large profile с отдельными disk/upload
quotas/deadlines: [NATIVE_ARTIFACTS](NATIVE_ARTIFACTS.md). Standard limits не
ограничивают весь protocol навсегда и не повышаются молча.

`fs.sync` для file/directory, durable atomic write и exclusive create уже есть.
Atomic replacement не заменяет immutable no-replace CAS promotion; no-follow
path/finalize contracts закрывает R2/R3 storage adapter. One-shot `Digest`
не используется для накопления полного upload в web memory: native facade
переиспользует bounded file hash из package core. Подробная граница Orbit/stdlib/
registry — [ORBIT_INTEGRATION](ORBIT_INTEGRATION.md).
Worker process имеет OS memory/resource cap и supervisor deadline watchdog.
Если native parser завис/упал, процесс перезапускается, lease восстанавливается;
web не падает вместе с parser. Corpus/fuzz и fault tests проверяют этот путь.

## 4. Profiles и подписи

### Private bootstrap

Operator-managed accounts, HTTPS edge либо явный loopback dev.
Unsigned/dev-signed artifact допустим **только при явном private trust profile**
на сервере и source config клиента. Digest/format/identity проверяются всегда.
Private не объявляется public supply-chain readiness.
Client по умолчанию не ослабляет public policy по значению capabilities server.

### Public controlled beta

Archive artifact `sputnik.pkg.v1` + detached Ed25519 publisher signature
и registry receipt. `Signature.available?(:ed25519)` — обязательный startup gate.
Новый crypto algorithm/package не нужен. Detached envelope связывает exact
compressed archive digest без циклической подписи собственных bytes.
Прототипная embedded `sputnik-sha256-dev-v1` больше не является частью
package format. Private profile разрешает unsigned archive; public trust требует Ed25519. Publisher signature подаётся при create session.

Signature envelope:
`{role, algorithm: ed25519, key_id, signature}`; signature Base64 от 64 raw bytes.
Response дополнительно содержит `public_key` (Base64 32 bytes); для publisher
он authenticated registry receipt, для registry authoritative key берётся из
локального keyring, а не из response. Key id grammar `[a-z0-9_-]{1,64}`.

Publisher подписывает UTF-8 bytes следующего **точного** текста: строки в этом
порядке, LF после каждой строки включая последнюю, без BOM/CR/пробелов вокруг `=`.
Все значения валидируются до составления; placeholder не часть payload.

```text
sputnik.release-signature.v1
role=publisher
registry=https://registry.example.test
package=slowpilot/example
version=1.2.0
variant=macos-aarch64-cpu
digest=sha256:<64-lowercase-hex>
size=12345
format=sputnik.pkg.v1
key_id=<publisher-key-id>
```

Server берёт public key по key_id из своей authorized account key table,
проверяет scope/current status и signature. Клиентская передача произвольного
public key не создаёт authorization. Финальная publication повторяет key/auth check.

Registry receipt подписывает текст с теми же полями, `role=registry`, своим
key_id, затем дополнительными строками:

```text
publisher_key_id=<publisher-key-id>
publisher_public_key=<64-lowercase-hex>
publisher_signature=<128-lowercase-hex>
signed_at=<RFC3339-UTC>
```

Client проверяет receipt по out-of-band доверенному registry keyring, затем
publisher signature по public key, связанному receipt. Обе подписи привязаны
к origin/ref/version/variant/bytes. Publication хранит immutable snapshot подписей.
Metadata dependencies/modules всё равно сверяется с artifact, а mutable yank/
quarantine приходит по authenticated HTTPS, не является частью этой подписи.
Подписи не обещают transparency log или защиту от malicious trusted registry.

Registry keys хранятся в protected file/secret mount, key id stable. V1 keyring
обновляется оператором вне download API; публичный `/keys` не может сам назначить
новый trust root. Rotation: сначала доставить новый public key клиентам, затем
переключить signing, оставить старый verify key на retention период. Publisher
key add/revoke доступен самому владельцу через CLI/JSON API с registry token,
`publisher-key:manage` и проверкой live account permissions; operator сохраняет
emergency revoke. Все операции пишут audit; revoke блокирует новые публикации.
Private key не отправляется registry. Frontend/browser sessions не требуются.
При compromise оператор quarantines affected releases и распространяет updated
keyring. Offline установленные bytes не получают автоматического отзыва.
Полностью автоматическая delegation/root rotation — отдельная версия trust protocol.

`trust_key_file` содержит TOML `schema = "sputnik.registry.keyring.v1"`,
canonical `origin` и `[[keys]]` с уникальными `key_id`, `algorithm = "ed25519"`,
Base64 `public_key`, `not_before`, `not_after`, optional `revoked_at`.
Файл передаётся доверенным каналом; origin должен совпадать с source config.
Registry receipt сохраняет `signed_at` в envelope; это поле также входит
в подписанный registry payload последней строкой. Формат времени —
`YYYY-MM-DDTHH:MM:SSZ` без fractional seconds. Client проверяет key validity
на signed_at, отсутствие future time (clock skew не более 5 minutes) и
собственный текущий keyring revoke policy. Publisher envelope
отдельного времени не содержит: его authorization подтверждается receipt.
Keyring update не происходит из artifact response. Expired/revoked key policy
принимается явно оператором; по умолчанию revoked key запрещён и для restore.

## 5. Native admission

Native source и prebuilt kinds поддерживаются в v1; binary target variants и
large runtime bundles — [NATIVE_ARTIFACTS](NATIVE_ARTIFACTS.md). В source kind
проверяются paths/flags/libraries, запрещены compiler plugins/@response files/
произвольные linker scripts и служебные metadata collisions.

Для prebuilt обязательны inventory hashes, static format/arch/OS/libc/CPU/ABI/
import checks и declared dependency closure. User-supplied filename/target triple
не заменяет binary headers. C++/LibTorch/runtime ABI conflicts завершают resolution.
Semantic compatibility, CPU instruction requirements и динамические plugin imports
не всегда доказуемы статически; publisher target CI обязателен.

Server никогда не делает dlopen/LoadLibrary, не вызывает ABI functions и package
programs. Install тоже не загружает libraries. Runtime loading требует native
trust/ffi grant, потому что constructors могут исполняться до ABI query.
Fallback на source build не запускается после tamper/signature/target errors;
source build отдельный explicit выбор, проверка ABI не sandbox.

Relocatable library paths заранее готовит publisher; installation не выполняет
postinstall hooks, patchelf, install_name_tool или пересоздание OS signature.
Требуемая host code-signing policy не отключается. Произвольная library из
cwd/global search path не считается удовлетворением declared package ABI provider.

Private native profiles включаются явно. Public beta: curated source/prebuilt
publishers после platform conformance, scanner и supply-chain review. Native
malware scan не доказывает безопасное выполнение. Binary redistribution требует
включения соответствующих license notices; нормализованный license search остаётся
отдельной задачей, а не блокирует описание native files в manifest.

Optional bundles — [NATIVE_BUNDLES](NATIVE_BUNDLES.md). Их component inventory
покрывает фактическое runtime closure, SDK headers и статически встроенные
компоненты. Writer проверяет declared license/notice/exception materials и
согласованность mapping с inventory, но не выводит legal clearance из root
LICENSE или SPDX string. Unknown component license не наследует лицензию PyTorch.
Bundle profile не повышает size limits и не разрешает неизвестные imports/plugins.
Prepared provider view сохраняет signed bytes; несовместимые либо необъяснимо
дублированные singleton runtimes не загружаются по принципу first-found.

## 6. Tokens, audit и observability

256-bit opaque random bearer token, hash SHA-256 в DB, prefix только для UI.
Secret показывается один раз; audit/idempotency/log его не сохраняют.
Default TTL 30 days, maximum 90 days; effective scopes/profile grants и read
policy определены в [ACCESS_AND_USAGE](ACCESS_AND_USAGE.md). В private deployment
read требует `registry:read`; anonymous read включается отдельной operator policy.
Scope проверяется вместе с live role/account/key state, не только при выдаче.
Admin operations сначала через local protected command; публичного admin bearer
endpoint без step-up auth нет. Owners не могут назначить себе operator scopes.

Audit security-sensitive mutation в той же transaction, append-only для app role;
bounded facts, actor/target/request id, без artifact body/сырого manifest/секретов.
Metrics: request class/status/latency, upload bytes, validation codes/time/RSS,
queue lag/leases/retries, DB busy/pool wait, disk/orphan/integrity counts.
Нет per-package/account labels без cardinality cap. Traces/exporter позднее;
локальные structured logs и metrics достаточны для private slice.

Per-package download/publication history — отдельная projection, не Prometheus
labels и не quota source. Business facts идут через transactional audit/outbox;
transport completions — через bounded spool в `usage.sqlite`. Полные origin
выдачи, attempts/bytes и accepted uploads считаются отдельно. Semantics,
dedup/retention, degraded coverage и Orbit transport gate —
[ACCESS_AND_USAGE](ACCESS_AND_USAGE.md).

## 7. Backup, reconcile и incident operations

Private gate: ежедневный backup, начальные **целевые** RPO 24 h/RTO 2 h, доказать
restore drill. В MVP короткое maintenance window: остановить admission, drain
web/worker, checkpoint/close SQLite, скопировать DB + final CAS + audit + config/
key references, составить manifest hashes. Keys backup отдельно и зашифрованно.
Копирование открытого SQLite файла без WAL/snapshot не считается backup.

Restore на пустом узле: schema/checksum check, сверка всех referenced CAS hashes,
новые credentials/config mounts, readiness и read/install smoke, затем write enable.
GC не удаляет referenced или quarantined evidence artifacts. Final CAS cleanup
только maintenance после mark/ref scan и retention, с backup hold.
Missing/corrupt referenced blob: release недоступен, readiness/alert, quarantine
затронутого release и restore; нельзя молча выдать другой digest.

Открытый public launch требует Orbit canary/72h soak и его остальных release
criteria, hostile-artifact fuzz evidence, key rotation/revoke drill, restore,
rate/quota abuse tests, scanner profile и operator incident runbook.
Эти gates не блокируют разработку private vertical slice.
