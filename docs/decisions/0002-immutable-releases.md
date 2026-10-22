# ADR-0002: immutable artifacts и content-addressed storage

Статус: принято в design v1; уточнено ADR-0005/0007.
Дата: 2026-07-25; актуализация 2026-10-19.

## Решение

- Release `(canonical ref, version)` имеет immutable common declarations.
- Release может содержать несколько platform artifact variants; новая платформа
  добавляется без изменения existing metadata/bytes других variants.
- `(canonical ref, version, variant)` уникальна навсегда после publish.
  Неуспешная session освобождает временную artifact reservation.
- Artifact SHA-256 сервер считает по exact compressed bytes; CAS key из digest.
- Повторная публикация existing variant — conflict; иной payload требует нового
  release либо действительно нового target variant с теми же common declarations.
- Yank меняет release resolver visibility; quarantine release/variant имеет
  приоритет над downloads. Evidence и опубликованные identities сохраняются.
- Hard delete допустим для unreferenced quarantine/final objects с retention/
  backup policy, не для published release artifacts.

## Последствия

CAS остаётся immutable независимо от количества platforms. Public downloads
проверяют release+variant availability и возвращают no-store. Lock фиксирует
variant/digest и остаётся воспроизводимым после добавления новых платформ.
Согласованность общего API вариантов проверяет publisher CI; server фиксирует
module/dependency/capability declarations. [ADR-0007](0007-platform-artifacts-and-prebuilt-native.md).
