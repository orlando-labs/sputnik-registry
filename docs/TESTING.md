# Проверки registry

Из корня repository:

```sh
./tools/build
python3 test/integration.py
python3 test/integration.py --vm
python3 test/integration.py --vm --auth-only
python3 test/storage.py
python3 test/storage.py --native
```

Нужны sibling SDK, Orbit и sqlite3, Python 3 и clang. `SPUTNIK` задаёт
путь к SDK. Сборка требует full-native coverage и сверяет SHA-256 входов до и
после компиляции. Integration reports сохраняются в
`.build/integration-native.json` и `.build/integration-vm.json`; содержат backend,
SHA-256 executable, список проверок и каталог стенда.

`--auth-only` runs the GitHub login/publication contract without the full storage
and failure matrix. Its loopback GitHub fixture covers numeric identity and
rename, pending/slow_down/deny/expiry, poll fencing, one-time completion,
namespace conflicts and transaction rollback, account switching, restart,
closed registration, console account linking and SDK login/publish/install/
logout (including renewal after token expiry). The full integration run also
includes these checks. The fixture override is accepted only when both registry
and provider are loopback HTTP; production always uses GitHub's HTTPS endpoints.

Проверено 2026-10-21 на macOS aarch64: 677 native и 672 VM integration checks;
storage и system_io прошли на обоих backend; package core — 299 checks.
Сборка: 1928/1928 native code bodies, без VM fallback. Разница числа integration
checks связана с native supervisor и созданным им дополнительным артефактом.

Linux deployment gate 2026-10-21: Debian 12 x86_64 Docker на builder `.136`,
677 native integration checks и оба storage self-test. Linux fixtures собирают
ELF `.so` с host/libc target; VM self-test использует SDK exports вместо включения
non-PIC static SDK в shared library. Generated registry unit собран с `-O0`
из-за медленных LLVM 14 optimization passes; runtime — `-O2`, extensions — `-O3`.
Worker использует 2 GiB address-space cap; deployment ограничивает его память
512 MiB через cgroup и задаёт `MALLOC_ARENA_MAX=2`. Длительный load/soak не выполнен.

Каждый запуск создаёт отдельные data directory, accounts и loopback listeners.
Native operational commands запускаются с `PATH=/nonexistent`. Fixture builder
и clang используются только тестовым Python harness. Временные стенды остаются
в `.build/integration-*` и `.build/storage-*` для разбора результата.

| Набор | Покрытие |
|---|---|
| `integration.py`, `regressions.py` | Реальные CLI/web/worker, token delegation/revoke, ACL, idempotency/races, source/bytecode/native/provider metadata, common metadata, CAS/backup corruption, restore, replay |
| `conformance.py` | Даты/пагинация/owners input, повторный cancel живого PUT, trusted inspector profile, provider grants, все dependency sets, unscoped quota owner, fresh backup, SemVer/cursor, public/private и suspension |
| `quota_contract.py` | Compressed, pending и logical storage boundaries обоих profiles; account/global limits, native-large grant, receiving cap, 32/33 variants |
| `client_contract.py` | Подставной HTTP origin: identity/origin/size/digest/framing/encoding/redirect errors, private credentials, no overwrite/partial cleanup, publish resume после kill |
| `fault_contract.py`, `faults.c` | Реальный kill после fsync/link/unlink, повторный kill, устаревший fence, отзыв ACL/native grant перед final commit, EIO/ENOSPC, spool rename/fsync errors, hostile tar, compressed expansion, RST/Range, backup одновременно с publication и WAL reader |
| `usage_contract.py` | Malformed spool с coverage gap, дедупликация, UTC half-open interval, version/variant/quarantine filters, outbox backlog/watermark, недоступная usage DB при работающем web |
| `storage.py`, `storage.s` | Descriptor-relative storage, torn/overflow spool, CAS retry и обнаружение повреждения |

`faults.c` — отдельная тестовая библиотека, не часть приложения. Она подменяет
выбранную файловую операцию только внутри data directory текущего стенда.
Для kill-тестов worker останавливается после настоящего syscall, затем harness
посылает SIGKILL и проверяет восстановление. Для EIO/ENOSPC операция однократно
отклоняется до изменения файлов. SQL задаёт границы счётчиков, lease/clock и
окна гонок; сами admission, upload, commit, publish и restore проходят штатный API.

Дополнительные проверки изменений SDK из `sputnik-lang`:

```sh
make build/package_distribution_tests
build/package_distribution_tests
python3 packages/system_io/tests/run.py
python3 packages/system_io/tests/run.py --native
```

Package-core тест проверяет ранний отказ при несовпадении admitted profile до
обработки следующего hostile member и сохранение offset открытого descriptor.
Registry тесты проверяют member header limits standard/native-large без выделения
многогигабайтных файлов и реальную распаковку за standard expanded limit.

Локальный gate выполняется на macOS aarch64. Он не заменяет Linux gate для
address-space limits, реальное OOM/power-loss, 8 GiB expanded corpus и длительный
load/soak. Повторный SIGKILL проверяет восстановление после внезапного завершения,
но не подтверждает работу OS OOM killer или supervisor deadline.
