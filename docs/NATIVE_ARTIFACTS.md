# Платформенные артефакты и prebuilt native binaries v1

Дата: 2026-10-19. Дополнение 2026-10-22: native selection, CUDA driver/device
checks, variant store и exact application locks реализованы в SDK; команды и
границы описаны в [package-native-install-v1](../../sputnik-lang/docs/engineering/package-native-install-v1.md).
Остальные разделы ниже сохраняют полный целевой контракт v1.
Уточняет PACKAGE_FORMAT, CLIENT, API и прежнее ограничение одного artifact
на reference/version. Номер format/manifest/API/application lock не повышается.
Native dependency delivery и optional runtime/SDK bundles уточняются в
[NATIVE_BUNDLES](NATIVE_BUNDLES.md). Local provider metadata/bindings и bundle
writer, managed selection и variant/hash locks реализованы в SDK.

## 1. Release и artifact — разные сущности

Release идентифицируется `(package_ref, version)` и содержит несколько artifact
variants. Каждый variant — самостоятельный `.sputnikpkg` (обычный tar.gz):

```text
slowpilot/example 1.2.0
  source                  example-1.2.0-source.sputnikpkg
  macos-aarch64-cpu        example-1.2.0-macos-aarch64-cpu.sputnikpkg
  linux-x86_64-gnu-cpu     example-1.2.0-linux-x86_64-gnu-cpu.sputnikpkg
```

Variant id — opaque publisher-assigned label `[a-z0-9][a-z0-9._-]{0,127}`,
не `.`/`..` и без `..` anywhere. Canonical target/compatibility задаются
структурированными metadata, не извлекаются из label/filename. Initial cap:
32 variants на release. Source variant тоже объявляет target, обычно any/any.

Общая immutable release metadata: ref/version, package kind, logical exports,
root и module names для library packages, общие exact package dependencies и
required capabilities. Native-only providers имеют пустой module set без root:
[NATIVE_BUNDLES](NATIVE_BUNDLES.md#5-native-only-packages). Дополнительные exact
dependencies конкретного platform/backend задаются `[artifact.dependencies]`;
они immutable внутри variant и не нагружают остальные платформы. Представление module
(source/bytecode), target, native payload и build metadata принадлежат variant.
У вариантов одной версии общий логический API; semantic equivalence проверяет
publisher CI. Server проверяет common declarations, но не доказывает эквивалентность
исходников, байткода и machine code.

Публикация идёт по одному variant в session. Первая успешная session создаёт
release и фиксирует common metadata. Позже разрешено добавить новый variant той
же версии, если common metadata совпадает. Добавление атомарно меняет index
revision; bytes и metadata ранее опубликованных variants не меняются.
Unique immutable identity: `(package_ref, version, variant)`; именно её занимает
reservation. При гонке первых variants common metadata сверяется под DB lock.
Иные declarations — `RELEASE_METADATA_CONFLICT`, не изменение существующего release.

Повторно использовать variant после quarantine/yank/remove нельзя. Общий release
можно yank/quarantine; отдельный artifact тоже можно quarantine. Downloads
проверяют оба уровня. Добавление variants к yanked/quarantined release запрещено,
пока ограничение не снято. Quarantine release запрещает все его variants;
quarantine одного artifact запрещает именно его. Другой доступный source
variant требует явного выбора build, а не скрытого обхода через fallback.

## 2. Состав platform artifact

```text
sputnik.build.json
checksums.sha256
src/example.s
bytecode/internal.sputnikbc
lib/example_ext.dylib
lib/vendor/libcompute.dylib
meta/build.json
LICENSE
licenses/libcompute.txt
```

`.so`, `.dylib`, `.dll` и их declared dependent libraries являются разрешёнными
inventory files. Native source не обязателен. Module representation source/
bytecode остаётся независимым от native extension representation.
V1 direct runtime loading принимает shared libraries, реализующие `sputnik_ext`
C ABI; вспомогательные libraries могут иметь свой ABI. Произвольный AOT executable
или `.o` не становится импортируемым Sputnik module из-за помещения в archive.
Static-library-only distribution требует отдельного linker consumer и в эту
первую загрузочную модель не входит.

Пример manifest с prebuilt extension:

```yaml
schema: "sputnik.build.v1"
name: "slowpilot/example"
version: "1.2.0"
source_root: "."
profiles:
  required:
    - "core.v1"
    - "ffi.v1"
files:
  - "src/example.s"
  - "lib/example_ext.dylib"
module_names:
  "src/example.s": "slowpilot.example"
artifact:
  variant: "macos-aarch64-cpu"
  limit_profile: "standard"
  bundle: "runtime"
target:
  os: "macos"
  arch: "aarch64"
  min_os: "13.0"
  cpu_features: []
  compute_backend: "none"
native_requirements:
  -
      id: "compute"
      kind: "runtime"
      version: "1.0.0"
      abi: "compute-c-1"
      provider: "bundled"
native_providers:
  -
      id: "compute"
      kind: "runtime"
      version: "1.0.0"
      abi: "compute-c-1"
      build_id: "sha256:<provider-build-id>"
      library_dirs:
        - "lib/vendor"
      link_libraries:
        - "compute"
      libraries:
        -
            name: "compute"
            path: "lib/vendor/libcompute.dylib"
            role: "dependency"
            abi: "compute-c-1"
native_extensions:
  -
      name: "example_ext"
      kind: "prebuilt"
      library: "lib/example_ext.dylib"
      sputnik_ext_abi: 1
      required_runtime_features: []
      requirements:
        - "compute"
      symbols:
        slowpilot.example.compute: "example_compute"
```

`kind = source` оставляет source/header/build declarations. `kind = prebuilt`
задаёт `library`, symbols/types/errors/blocking symbols и ABI; build commands,
compiler flags и обязательные native sources для этого extension отсутствуют.
Extensions разных kinds можно смешать, но compiler-free installation гарантируется
только если выбранный dependency graph не содержит native source build.
У fully prebuilt package ещё может быть Sputnik source: его обрабатывает
установленный Sputnik compiler, внешний C/C++ compiler для package не нужен.
Native build самого приложения по-прежнему может требовать host compiler/linker.

## 3. Target contract

| Поле | Правило |
|---|---|
| os | `any`, `macos`, `linux`, `windows`; unsupported host adapter отклоняется |
| arch | `any`, `aarch64`, `x86_64`; aliases нормализует packer, wire canonical |
| min_os | numeric dotted minimum macOS/Windows OS, если требуется |
| libc | Linux binary: `gnu` либо `musl`, не взаимозаменяемы |
| min_libc | Минимальная version выбранной libc; для GNU также actual symbol-version requirements |
| cpu_features | Canonical set, должен быть subset реально доступных CPU+OS process features |
| compute_backend | `none` для независимых libraries, иначе `cpu`, `mps`, `cuda` |
| runtime requirements | Versioned names/ABI IDs внешних runtimes, providers и driver requirements |

Any/any допустим для source/pure-bytecode variant. Variant с prebuilt machine
code имеет конкретные os/arch. Universal/fat binary v1 writer должен разбить
на отдельные variants; multiple CPU slices можно поддержать позднее внутри v1
до freeze, но reader сейчас не угадывает slice.
Host определяется по текущему процессу, не только физической машине: например
x86_64 process использует x86_64 libraries. Unsupported architecture не получает
«ближайший» binary. C++ standard library/provider ABI, Sputnik ext ABI и optional
leaf/runtime features проверяются отдельно от CPU/OS. Только target triple
`package_host_target()` из текущего кода для этого недостаточен.

Для ELF inspector читает architecture, DT_NEEDED/SONAME, interpreter-related
requirements и GLIBC/GLIBCXX symbol versions; для Mach-O — CPU, deployment target,
load commands/install names/rpaths; для PE — machine/import metadata при наличии
Windows adapter. Required CPU features и SDK/driver requirements не всегда
выводимы из binary; publisher объявляет их, platform CI подтверждает minimum
runtime. Runtime ABI query выполняется только после разрешённой загрузки.

Начальные executable gates: поддерживаемые toolchain macOS/Linux hosts с
native fixture. Наличие `windows` в manifest grammar само по себе не заявляет
готовность Windows loader. Adapter без conformance не рекламируется capabilities.

## 4. Зависимые libraries и relocatability

Source и prebuilt adapters могут потреблять runtime из отдельного exact package,
явного external binding либо opt-in bundle. Default bundle — `none`; source
не тянет SDK/runtime автоматически. `runtime` и `sdk` — разные профили, source+
runtime без вложенных headers допустим. Requirements/providers, build-only edges,
native-only packages и component/license inventory — [NATIVE_BUNDLES](NATIVE_BUNDLES.md).
Все bundled files входят в inventory; runtime/SDK assets считаются раздельно.

Для каждой library import dependency классифицируется как:

- `bundled`: точный inventory path и declared ABI;
- `package`: exact dependency ref, exported library name и ABI provider id;
- `system`: OS-provided allowlisted library/framework и requirement.

Это структурированные library requirements в manifest/build metadata. Для
`package` нужна также явная exact dependency: общая `[dependencies]` либо
variant-specific `[artifact.dependencies]`. Например CUDA provider нужен только
Linux CUDA artifact. Combined graph = common + выбранные variant declarations;
один ref с разными exact versions — ошибка. Dependency delta входит в manifest,
index/artifact metadata, signature digest и application lock graph; скрытых
registry dependencies через binary import не допускается.
Static binary imports сверяются с declarations. Optional runtime `dlopen` plugins
должны быть объявлены; неизвестные requirements не удовлетворяются произвольной
библиотекой из cwd/PATH. OS loader не считается sandbox; выполнение доверенного
native code всё равно может обращаться к host вне package.

Все non-system paths relocatable: ELF обычно ограниченный `$ORIGIN` RUNPATH,
Mach-O `@loader_path`/bounded `@rpath`, Windows explicit DLL directories в adapter.
Пути checkout publisher запрещены. Symlinked SONAME layout пакуется реальными
regular files, без archive links. Packer не меняет signatures/hashes после
финализации. При install нет patchelf/install_name_tool, relink или ad-hoc resign.
OS code signing policy проверяется отдельно от Ed25519 package trust; required
signing выполняет publisher до упаковки. Если host запрещает loading, client
сообщает ошибку, а не отключает OS policy.

C++/LibTorch/CUDA compatibility задаётся ABI ids и runtime requirements. Если
два пакета требуют несовместимые ABI providers одного process-wide runtime,
resolution завершается `NATIVE_ABI_CONFLICT`; нельзя загрузить случайную версию
из глобального library search path. Bundled libs не имеют обязательного
`sputnik_ext_abi_version` export: он требуется только extension entry library.

## 5. Selection и fallback

Client сначала применяет explicit bundle policy (`none` по умолчанию), затем
target/ABI/build-policy eligibility и выбирает version,
для которой есть совместимый разрешённый вариант;
без --version — максимальную stable version с совместимым artifact, затем
prerelease только при отсутствии stable. Yanked versions не участвуют без
explicit policy. Для exact request отсутствие подходящего artifact —
`NO_COMPATIBLE_ARTIFACT`, а не downgrade версии.

1. Отфильтровать по OS/process arch, OS/libc minimum, CPU/features, ext/runtime ABI
   и graph-wide ABI provider requirements.
2. Для compute-aware variants default backend CPU; `--backend mps|cuda` задаёт
   explicit preference. `none` остаётся backend-independent. User preference
   фиксируется в lock; CUDA не выбирается только по наличию GPU.
3. Совместимый prebuilt предпочитается source-build variant. Pure any/any,
   не требующий native build, также может быть default eligible variant.
4. Несколько подходящих variants: выбрать единственный наиболее специфичный
   по subset compatibility domain. Несравнимые либо эквивалентные selectors
   дают `ARTIFACT_SELECTION_AMBIGUOUS`; user задаёт `--variant`, а не получает
   выбор по lexical filename. Dependency provider constraints применяются до
   tie resolution. Variant-specific exact dependency graph проверяется вместе
   с common dependencies; cycles/version/provider conflicts — явная ошибка.
   Это bounded artifact constraint propagation, без version range solver или
   незаметного выбора другой версии ради обхода конфликта.
5. Native source variant требует explicit `--build-from-source` вместе с native
   trust `--allow-native`. Этот flag выбирает source-build variant вместо
   prebuilt; его отсутствие не разрешает hidden fallback. Невалидный hash/signature/prebuilt binary не вызывает
   fallback compilation. Отсутствие prebuilt тоже не запускает C compiler молча.

`--variant` не отключает compatibility checks. Foreign-target install с
выполнением невозможен; `pkg download --variant ...` как offline-fetch command
можно добавить отдельно. V1 install ориентирован на текущий host.
Selection по установленным пакетам на run/build offline и использует тот же
compatible variant contract, без скачивания или новой native compilation.

Целевые команды (ещё не реализованы):

```sh
sputnik pkg install slowpilot/example --version 1.2.0 --allow-native
sputnik pkg install slowpilot/example --version 1.2.0 --variant macos-aarch64-cpu --allow-native
sputnik pkg install slowpilot/example --version 1.2.0 --build-from-source --allow-native
```

## 6. Install, loader и общий store

```text
$SPUTNIK_PKG_HOME/
  orbit/1.0.0-rc.1/source/...
  .scoped/slowpilot/example/1.2.0/macos-aarch64-cpu/...
  .scoped/slowpilot/example/1.2.0/linux-x86_64-gnu-cpu/...
```

Variant добавляет уровень layout. Каждая directory содержит свой exact artifact,
verified manifest/inventory, module index/provenance и prepared loader descriptor.
Разные variants одной версии могут сосуществовать. Один graph активирует ровно
одну version/variant package; modules и native bindings из разных variants
не смешиваются. Local `packages/` override затеняет весь package как раньше.

Install prebuilt: verify archive/signatures/inventory → static binary/target/
import validation → compile только Sputnik source при наличии → write descriptor
→ atomic variant directory promotion. До runtime никакого dlopen/LoadLibrary,
ABI function call или выполнения constructors; source-build path тоже готовит
library в compile-only mode. Server никогда не загружает package library.
Текущий native-library.meta с одной строкой target/path/digest заменяется
structured descriptor: selected variant, artifact digest, target/ABI/features,
ordered extension/dependency library records с relative paths и SHA-256.

Network native package требует `--allow-native` на установку. Existing runtime
`--grant ffi`/notebook native trust остаётся отдельным условием загрузки.
Перед runtime load повторно проверяются file hashes, paths/containment, current
host ABI и dependency closure; изменение descriptor/files не вызывает rebuild.
После разрешённого dlopen вызывается existing `sputnik_ext_abi_version`, проверяются
symbols/types/leaf descriptors. Constructors при dlopen уже могут исполниться,
поэтому package signatures/ABI checks не заменяют explicit native trust.

`pkg uninstall ref --version V` удаляет все variants версии; без версии — все
версии/variants. Optional `--variant` разрешён вместе с exact --version для удаления
одного build. Source checkout, другие packages/locks не удаляются. Notebook
меняет loaded variant только при Apply/restart.

## 7. Locks и artifact signatures

Application lock schema остаётся `sputnik.packages.lock.v1`; каждый package pin
содержит source, exact version, **variant** и digest его compressed archive:

```toml
schema = "sputnik.packages.lock.v1"

[[packages]]
name = "slowpilot/example"
version = "1.2.0"
variant = "macos-aarch64-cpu"
digest = "sha256:<64-lowercase-hex>"
source_kind = "registry"
source = "https://registry.example.test"
```

Один lock фиксирует реально выбранный graph для host. Portable source pin может
подходить другим hosts; platform pin на несовместимом host даёт `LOCK_TARGET_MISMATCH`.
На другом host явная переустановка/relock создаёт его собственный graph. В v1
не требуется multi-target matrix в одном application lock. Restore не заменяет
variant, не выбирает новый prebuilt и не строит source вместо pinned binary.

Detached publisher/registry signature payload дополнен строкой `variant=<id>`
после version. Target/ABI/import metadata находятся внутри подписанного exact
archive, client сверяет их с index перед promotion. Подписи относятся к одному
variant, не являются заменяемой подписью всей версии. Release additions не
меняют существующие locks/signatures. Все contracts по-прежнему v1.

## 8. Размеры и gate реализации

Standard limits остаются 64 MiB compressed / 256 MiB expanded. Чтобы package мог
нести крупные runtimes, предусмотрен operator-enabled `native-large` profile:
2 GiB compressed, 8 GiB expanded stream, 4 GiB single file, upload deadline 2 h,
session TTL 4 h, pending compressed bytes до 8 GiB global/4 GiB per account.
Receiving native-large upload — максимум один global; inspector wall deadline
900 s при RSS 512 MiB. Read/write authorization, traffic/storage quotas и
statistics — [ACCESS_AND_USAGE](ACCESS_AND_USAGE.md).
Это bounded initial defaults; реальные configured limits публикует capabilities.
Native-large включается для доверенного publisher/namespace и при достаточном
зарезервированном disk budget; backend обрабатывает streaming, не whole-file RAM.
Worker concurrency и RSS limits остаются ограниченными. Client может иметь
более низкие local limits. Standard profile не повышается молча.

Обязательные R1b/R6 fixtures: prebuilt install/VM/notebook без C compiler;
macOS/Linux native loader conformance; wrong arch/libc/minimum OS/ext ABI;
missing dependency/hash tamper; runtime/provider conflict; ambiguous selectors;
no constructors before explicit runtime grant; variant publication races/
additions/quarantine; exact locked restore; large-file streaming с bounded RSS.
Фактические GPU/runtime variants требуют target CI, а не только synthetic metadata.

## 9. Источники и границы выводов

Compatibility зависит от runtime baseline и architecture, как показывают
[platform compatibility tags](https://packaging.python.org/en/latest/specifications/platform-compatibility-tags/).
Relocatable macOS imports описаны в [Apple run-path documentation](https://developer.apple.com/library/archive/documentation/DeveloperTools/Conceptual/DynamicLibraries/100-Articles/RunpathDependentLibraries.html).
Sputnik variant ids, selection, limits и publish rules выше — решения этого
проекта, не заимствованный wheel protocol. Existing Sputnik ext ABI и loader
проверены по [header](../../sputnik-lang/runtime/sputnik_ext.h) и
[load path](../../sputnik-lang/tools/sputnik/main.cpp).
