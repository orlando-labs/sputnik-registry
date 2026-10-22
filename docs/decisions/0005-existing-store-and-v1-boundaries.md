# ADR-0005: существующий store и границы protocol v1

Статус: принято в design v1 для реализации. Дата: 2026-10-16.
Уточняет ADR-0002/0003/0004; архитектурный модульный монолит сохраняется.

## Контекст

Июльский baseline предшествовал shared package home/source directory installer,
application locks и нынешним native cache/resolver. Ed25519/HTTPS уже есть.
Baseline оставлял неопределёнными source identity, failed version reservation,
подпись artifact и противоречивый порядок DB publish/blob finalize.

## Решение

1. Registry — новый source adapter существующего `sputnik pkg install`.
   Directory/artifact/filesystem sources и offline resolver сохраняются.
2. Scoped identity проходит через общий core, новый `.scoped` layout; curated
   unscoped libraries сохраняют текущие imports. Unscoped grant содержит
   allowed module prefixes, включая companion `orbit-orm` → `orbit.integrations.orm`.
3. Application lock `sputnik.packages.lock.v1` расширяется origin/source kind
   на месте до первого применения. Прототипный lock пересоздаётся явно;
   synthetic embedded lock в archive не нужен. Уточнено ADR-0006.
4. Registry versions — filesystem-safe строгий subset SemVer, exact dependencies,
   shared numeric/prerelease compare и latest stable; ranges/backtracking позже.
5. Изначально сохранялся artifact v1; эта часть **заменена ADR-0006 от 2026-10-19**:
   package contract v1 теперь tar.gz с source/bytecode modules; text prototype
   заменяется без требования local compatibility. Public trust добавляет
   detached publisher Ed25519 signature и registry receipt на
   origin/ref/version/digest/size/format.
   Алгоритм уже есть в Signature. Private unsigned trust только explicit profile.
6. Publish state включает promoting. Durable final blob возникает раньше
   published metadata; stale workers блокируются generation fencing.
7. Reservation не является опубликованным variant (уточнено ADR-0007). Failed/rejected/cancelled/
   expired session освобождает её; опубликованный variant не переиспользуется.
8. V1 downloads всегда проверяют release availability. CAS private и downloads
   no-store. Immutable bytes не означают unrestricted immutable public URL.
9. SQLite/local CAS — первый управляемый single-node deploy. PostgreSQL/S3,
   common jobs/blob packages — отдельные последующие задачи. Уточнение
   2026-10-20: self-service accounts включены в public v1 gate R8;
   private bootstrap R0–R6 сохраняет operator provisioning, см.
   [ACCESS_AND_USAGE](../ACCESS_AND_USAGE.md).
10. Native source и prebuilt shared libraries поддерживаются в v1 (ADR-0007).
    Source build explicit; prebuilt install без C/C++ build. Release имеет
    platform variants, signature/lock bind variant/digest. Public native curated.

## Последствия

Нельзя закончить только server routes: R1/R2/R4 — обязательные toolchain/package
изменения. Но локальный installer повторно не проектируется. Private vertical
slice достижим без построения всех июльских future packages. Полная public trust
и production evidence остаются отдельными gates. Platform artifact variants
входят в v1 по ADR-0007. Automatic key delegation/rotation, multi-target application locks и range resolver — последующие задачи.

Нормативные детали: [CLIENT](../CLIENT.md), [API](../API.md),
[SECURITY](../SECURITY.md), [план](../IMPLEMENTATION_PLAN.md).
