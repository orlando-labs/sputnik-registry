# ADR-0007: variants release и prebuilt native libraries

Дата: 2026-10-19. Статус: принято в design v1.
Заменяет ограничение одного artifact на version в ADR-0002/0005/0006.
V1 ещё не применена: совместимого legacy режима и новых номеров schemas не нужно.

## Решение

- Release `(ref, version)` имеет общие immutable declarations и несколько variants.
  Variant может объявлять additional exact dependencies для своей платформы/backend.
- Artifact identity `(ref, version, variant)` immutable; новый target variant
  можно добавить позже без замены existing artifacts. Common metadata не меняется.
- Tar.gz v1 может нести prebuilt shared extension/dependency libraries без native
  source. Sputnik source/bytecode module modes остаются независимыми.
- Native manifest kind source/prebuilt, structured target/ABI/runtime/import
  requirements; matching не сводится к OS/CPU или имени файла.
- Client выбирает совместимый prebuilt и устанавливает без C/C++ build. Source
  fallback только explicit. Install/server inspection не вызывают dlopen.
- Exact application lock/signatures включают variant и compressed artifact digest.
- CAS не меняется; DB/API/reservations/store layout работают на уровне variants.
- Крупные runtime bundles получают bounded operator-controlled limit profile.

## Последствия

R1b и platform conformance входят в первый implementation milestone.
Static inspection не доказывает безопасное исполнение или ABI semantic equivalence;
release CI и runtime native trust остаются обязательными. У одной package version
может быть несколько физических artifacts, но в graph ровно один active variant.

Нормативный контракт: [NATIVE_ARTIFACTS](../NATIVE_ARTIFACTS.md).
