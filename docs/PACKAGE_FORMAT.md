# Формат .sputnikpkg v1: tar.gz с файлами

Дата: 2026-10-19. Статус: целевой contract; базовые tar builder/inspector/extractor
реализованы вместе с local provider/bundle metadata и preparation; shared install
и managed provider selection ещё нет.
Архив уточняет ещё не применённый v1; текстовый контейнер — прототип реализации,
а не выпущенный format contract. По решению пользователя breaking changes до
первого фактического применения входят в v1. HTTP API, package manifest и
application lock также остаются v1; их schemas различаются именами.

## 1. Решение

`.sputnikpkg` — обычный gzip-compressed POSIX tar archive (restricted PAX).
Без собственного внешнего заголовка, hex/base64 payload и вложенного архива.
Внешнее расширение сохраняется; файл можно посмотреть обычной командой:

```sh
tar -tzf example-1.2.0.sputnikpkg
```

Native tar/gzip backend — libarchive; его новые зависимости и pinned build
проверяются в R1a. Reader включает только tar/gzip, не все archive formats и
не external filter programs. Server/client не вызывают shell `tar`/`gzip`.
Доступны source, bytecode и смешанные пакеты. Default packing поставляет Sputnik
sources. Binary modules публикуются явным выбором publisher по каждому module.
Отдельно доступны platform variants с prebuilt native libraries, без обязательных
C/C++ sources: [NATIVE_ARTIFACTS](NATIVE_ARTIFACTS.md). Release может содержать
несколько artifacts, immutable identity — `(ref, version, variant)`.

```text
sputnik.build.json
checksums.sha256
src/example.s
bytecode/internal.sputnikbc
native/fast/fast.c
native/fast/fast.h
resources/defaults.json
meta/build.json
README.md
LICENSE
```

Это пример состава; обязательны только manifest, inventory и объявленные files.
Верхнего wrapper directory `package/` или `<name>-<version>/` нет.
Bytecode хранится исходными bytes `.sputnikbc`. Declared prebuilt `.so`/`.dylib`/
`.dll` и dependent libraries разрешены. Installed caches, произвольные compiler
outputs, `.git`, credentials и application `sputnik.lock` не упаковываются.
Resources — данные; их наличие не разрешает новые filesystem capabilities.
Dependencies других пакетов объявляются, но их files в archive не копируются.

## 2. Distribution manifest

В корне `sputnik.build.json` со schema `sputnik.build.v1`.
Это versioned distribution manifest; packer строит его из source/build manifest.
Отдельного несогласованного manifest.json внутри archive нет.
Пример смешанного пакета:

```yaml
schema: "sputnik.build.v1"
name: "slowpilot/example"
version: "1.2.0"
requires:
  sputnik: ">=1.0.0 <2.0.0"
source_root: "."
files:
  - "src/example.s"
  - "bytecode/internal.sputnikbc"
module_names:
  "src/example.s": "slowpilot.example"
  "bytecode/internal.sputnikbc": "slowpilot.internal"
dependencies:
  "slowpilot/base": "1.0.0"
artifact:
  variant: "source"
  limit_profile: "standard"
target:
  os: "any"
  arch: "any"
  compute_backend: "none"
```

Для каждого module ровно одно из `source`/`bytecode`. Нет неявного выбора между
двумя разными реализациями одного module внутри artifact. Режим published
variant immutable: его изменение требует новой release version. Другие
platform variants той же версии могут иметь собственные representations;
общие root/module names сохраняются и проверяются по namespace rules.

Source-режим:

- Исходник включён отдельным UTF-8 `.s` file, без обязательного bytecode payload.
- Client компилирует при install, с dependency/macro providers, и сохраняет
  локальный результат вне archive. Повторный run использует подготовленный cache.
- Cached bytecode ключуется artifact/source digest, compiler revision/ABI,
  profiles/options и dependency fingerprints; он не меняет artifact digest.
- Module initializers во время install не запускаются. Compile-time macros
  обрабатывает обычный compiler с его policy; это нельзя описывать как полное
  отсутствие evaluation. Registry macros не исполняет.

Bytecode-режим:

- `.sputnikbc` включён, исходник этого module не обязателен и по умолчанию
  исключается. Publisher packlist проверяется отдельно от выбора modules.
- Inspector/client проверяют штатный bytecode verifier и metadata совместимости.
- Нет source fallback при несовместимом bytecode; выдаётся явная ошибка.
- Это способ не распространять source file, без обещания защиты от декомпиляции:
  constants, symbols и необходимая runtime metadata могут оставаться в bytecode.
- Packer не включает source maps/source snippets/debug sidecars для закрытого
  module без явного выбора. Strip необязательной debug metadata — отдельная
  проверяемая toolchain operation, не удаление нужных linker/runtime records.

Module, который предоставляет compile-time macros, пока публикуется с source,
если текущий toolchain не умеет восстановить macro provider из bytecode.
Обычные runtime modules могут оставаться bytecode-only. Gate R1a проверяет это
на настоящих Orbit macros и source/bytecode cross-module imports.

## 3. Native, дополнительные files и build metadata

Native extension имеет `kind = source` либо `kind = prebuilt` независимо от
source/bytecode представления Sputnik modules. Source kind содержит обычные
source/header/include/build declarations; prebuilt kind — shared library,
binding/ABI metadata и declared dependent libraries. Native source для prebuilt
не требуется. Files располагаются отдельными members (`lib/`, `native/`, resources)
и входят в inventory. Native prebuilt contract/пример manifest —
[NATIVE_ARTIFACTS](NATIVE_ARTIFACTS.md).

Доставка сторонних runtimes независима от source/prebuilt: default bundle `none`,
explicit `runtime` либо `sdk`. Source+runtime допустим без вложенного SDK.
Typed requirements/providers и отдельные native-only runtime/SDK packages —
[NATIVE_BUNDLES](NATIVE_BUNDLES.md). Native-only `kind: native`
не требует фиктивных modules/root; обычный `kind: library` требует modules без обязательного root.
Provider model заменяет плоский список dependency libraries текущего prototype
внутри той же v1, без второго параллельного wire format.

Target задаётся `target`, variant id — `artifact.variant`, size profile —
`artifact.limit_profile`. Дополнительные exact dependencies только для этого
platform/backend задаются `artifact.dependencies`; common `dependencies`
release остаётся общей. Build-only exact edges задаются
`artifact.build_dependencies` и не активируются при runtime-only install/run.
Bundle profile `artifact.bundle` входит в selection/lock; не выводится из label.
Client объединяет applicable graphs без exact-version/provider conflicts. Packer генерирует эти sections и для any/any source
variant. Server сравнивает target и реальные binary headers без выполнения.
Paths package-relative; publisher build checkout paths не распространяются.
OS/process architecture, libc/minimum OS, CPU features, Sputnik ext ABI,
C++/LibTorch/CUDA requirements проверяются совместно. Runtime loading остаётся
за existing explicit native trust boundary; source compilation — client opt-in.

`meta/build.json` обязателен при bytecode/native metadata: versioned schema
`sputnik.package-build.v1`, toolchain revision, по каждому bytecode member —
фактические bytecode/profile/ABI facts, по native extension — kind, ext ABI/
target/symbol digests и source либо prebuilt/library-import digests. Inspector сверяет эти assertions с реальными files;
самоописание не делает несовместимый payload допустимым. Source-only package
может обходиться без build metadata. Compiler minimum не выводится из имени release.

README/LICENSE и `resources/` включаются через явный packlist. Packer не обходит
checkout рекурсивно без allowlist. Установка сохраняет resources без выполнения.
Произвольные executable hooks в archive не поддерживаются.

## 4. Inventory, digest и подписи

`checksums.sha256` — sorted UTF-8/LF inventory: `<64-lowercase-hex>  <relative-path>`
по одному regular file на строку. Содержит `sputnik.build.json` и все payload/docs/
metadata/resource files; не содержит себя. Paths без LF/CR/NUL/backslash.
Inventory и manifest нужны целиком для inspection; limit каждого 1 MiB.

Archive содержит ровно manifest/inventory и перечисленные inventory files.
Missing/extra/duplicate members, duplicate inventory paths, size/type/digest
mismatch — rejection. Manifest module/native paths являются подмножеством inventory;
ненужный `.s` с закрытым source не попадёт в package через default packlist.

SHA-256 artifact считается по **всем сжатым bytes tar.gz**. Это CAS key и pin
application lock. Registry никогда не перепаковывает upload; tar/gzip metadata
также входит в digest. Per-file inventory проверяет состав, но не заменяет
whole-artifact hash. Detached Ed25519 signing использует прежний signed-payload
contract с `format=sputnik.pkg.v1`, variant id, compressed size и artifact digest.
Подписи не кладутся внутрь подписываемого archive, чтобы не создавать цикл.

## 5. Reproducible writer

Canonical writer: manifest первый, inventory второй, остальные files в порядке
ASCII path; только regular file entries, mode 0644, uid/gid 0, пустые owner/group
names, mtime 0, без atime/ctime/ACL/xattrs. Directory entries не нужны.
PAX используется только там, где path не помещается в ustar; PAX names/ordering
и padding фиксирует pinned backend/golden corpus.
Gzip: level 6, mtime 0, без original filename/comment; фиксированный OS header
и параметры compression. Backend versions входят в release build provenance.

Гарантия одинаковых bytes проверяется на pinned writer + одинаковых inputs,
в том числе cross-host fixture. Любой стандартный tar writer может создать
валидный archive в пределах admission rules, но одинаковый file tree сам по себе
не гарантирует одинаковый compressed digest. Перепаковка опубликованного variant
не допускается, даже если files семантически те же. Новые target variants той же
версии добавляются по отдельному publish session без изменения existing bytes.

## 6. Bounded reader и server inspection

Два независимых бюджета: compressed artifact (64 MiB initial) и decompressed tar
stream (256 MiB, включая headers/padding). Initial file limit 64 MiB, суммарные
file bytes 256 MiB, members 10,000; header/path/manifest/inventory limits также
проверяются. Это standard profile v1. Для крупных runtime bundles есть
operator-controlled native-large profile: [NATIVE_ARTIFACTS](NATIVE_ARTIFACTS.md).
Hex expansion budgets прототипа больше не нужны.
Gzip trailer/CRC и завершённость потока проверяются; archive end не означает,
что можно перестать проверять остаток compressed input. Writer создаёт один
gzip stream; reader может принять стандартные concatenated gzip members только
если полный decompressed результат — один tar в общих budgets. Appended second
tar/payload и nonzero trailing bytes запрещены. Только bounded tar end padding,
не неограниченные нули до EOF; предел padding 10 KiB после двух tar EOF blocks.

Reader работает streaming через archive library в изолированном worker.
Только regular files; symlinks/hardlinks/devices/FIFO/sparse entries отклоняются.
ACL/xattrs/ownership/executable bits не восстанавливаются. Effective path после
PAX resolution проверяется до materialization: relative slash-separated, без
empty/`.`/`..` segments, drive prefix, backslash, absolute path, NUL/control bytes.
Paths — portable ASCII; portable casefold collisions отклоняются. Destination
служебные metadata names зарезервированы. Limits действуют до выделения memory/
записи; worker resource cap остаётся второй границей для native archive backend.

Inspector в server не компилирует source и не запускает macros: bounded parse
source header/module declaration, package-reference/module policy, inventory,
exact dependencies, bytecode verification, static native binary/import metadata. Успешный source
inspection означает admission по формату/policy, не обещание semantic compilation
на всех toolchains. Полная source compilation/graph compatibility — client gate.
Client extracts только проверенные inventory members в private staging, после
этого компилирует source/prepares native и атомарно публикует version directory.

## 7. Замена прототипа и фиксация v1

- Reader/writer реализуют только archive contract `sputnik.pkg.v1`.
  Dual reader text/archive, legacy-output и converter не требуются.
- Старый text prototype отклоняется с понятным сообщением о необходимости
  пересобрать пакет. Его прежний schema marker не делает его опубликованной v1.
- Прототипные artifacts и производные install/cache directories можно очистить
  и пересобрать; форматная совместимость с ними не является release gate.
  Source-directory install и остальные пользовательские CLI сценарии сохраняются.
- Application lock `sputnik.packages.lock.v1` расширяется source fields на месте.
  Прототипный lock без обязательного source пересоздаётся явным `pkg lock`;
  origin не угадывается и lock при обычном run не переписывается молча.
- Synthetic embedded `sputnik.lock.v1` не переносится в archive: exact package
  declarations уже находятся в manifest; application lock — отдельный документ.
- `/api/v1`, `sputnik.build.v1` и release-signature schemas остаются v1.
  Capabilities сообщает archive format и source/bytecode modes.
- После первой фактической публикации immutable artifact identity
  `(ref, version, variant, digest)` начинает действовать.
  Общие release declarations фиксируются первым variant. Последующее несовместимое изменение
  опубликованного contract потребует отдельного решения о версии.

## 8. Основания выбора стандартного контейнера

Tar/gzip отделяет упаковку от module representation и позволяет обычными tools
осмотреть состав. Reproducibility требует нормализации timestamps/owners/order,
а не только выбора расширения: [GNU tar reproducibility](https://www.gnu.org/software/tar/manual/html_node/Reproducibility.html).
Native библиотечный путь опирается на [libarchive examples](https://github.com/libarchive/libarchive/wiki/Examples)
и [API](https://github.com/libarchive/libarchive/blob/master/libarchive/archive.h).
Gzip header/streaming controls описаны в [zlib manual](https://www.zlib.net/manual.html).
Эти источники описывают базовые форматы/APIs; Sputnik layout/modes/limits выше —
собственный design contract, не поведение библиотек по умолчанию.

## Language and VM compatibility (1.0.0)

`requires.sputnik` is a mandatory bounded SemVer range in published manifests. Unversioned local sources use the fixed 1.x baseline; packers write it explicitly. Old immutable archives without a requirement must be rebuilt as a new release. Source, bytecode and native variants share this requirement. Bytecode format 1.2 carries the producer language patch component. Native `sputnik_version` uses the same language/VM release rather than a separate extension ABI counter. Application locks record the exact language version; regenerate a legacy/different-version lock explicitly with `sputnik pkg lock`. See [language version contract](../../sputnik-lang/docs/engineering/language-version.md).
