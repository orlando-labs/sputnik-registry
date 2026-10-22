# ADR-0006: tar.gz и representation каждого module

Дата: 2026-10-19. Статус: принято в design для реализации.
Заменяет пункты 3/5 ADR-0005 в части format/lock compatibility.
По указанию пользователя ещё не применённая v1 допускает breaking changes.

## Контекст

Текстовый `sputnik.pkg.v1` кодирует binary payload в hex и смешивает transport,
metadata и module representation. Он был прототипным форматом. Источник module
не обязан публиковаться только потому, что меняется внешний package container.

## Решение

- Новые `.sputnikpkg`: стандартный tar.gz (restricted POSIX PAX), format `sputnik.pkg.v1`.
- Root `sputnik.toml`, checksum inventory, отдельные source/bytecode/native/
  resource files; без hex payload и собственного archive envelope.
- Source — default. У каждого module ровно одно `source` либо `bytecode`;
  mixed package разрешён, source bytecode-only module не обязателен.
- Source компилируется client при install и кэшируется вне archive; bytecode-only
  проверяется на ABI/profile compatibility без source fallback.
- Native source/prebuilt variants уточняет ADR-0007; sources/headers для
  prebuilt не обязательны, runtime native trust сохраняется.
- Server не компилирует source, не выполняет macros или module initialization.
- Artifact, manifest и application lock остаются v1. Text prototype заменяется
  на месте, без dual-reader, legacy writer или обязательного мигратора.
  Прототипные artifacts/locks пересоздаются; существующие CLI сценарии сохраняются.
- CAS/lock/signatures адресуют exact compressed artifact bytes; registry не repack.
- HTTP protocol v1 и application lock v1 сохраняют свои independent versions.

## Последствия

R1a (archive/modes и замена прототипа) становится обязательным предшественником
inspector и network client. Появляется pinned native archive dependency и
decompression/path/member corpus. Semantic source compilation — client gate.
Bytecode сохраняет ценность для publishers, которые не распространяют source,
но не является защитой от reverse engineering.

Полный контракт: [PACKAGE_FORMAT](../PACKAGE_FORMAT.md).
