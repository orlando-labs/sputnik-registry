# Native dependencies и необязательные bundles v1

Дата: 2026-10-20. Статус: локальный builder и explicit provider bindings
реализованы; managed package providers, selector и portable lock остаются design. Уточняет [NATIVE_ARTIFACTS](NATIVE_ARTIFACTS.md) и
[ADR-0008](decisions/0008-optional-native-bundles.md). Format/manifest/lock остаются v1.
Сервис registry эта работа не создаёт.

## 1. Независимые решения

У artifact три независимых свойства:

1. Представление каждого Sputnik module: source или bytecode; возможен mixed.
2. Представление собственного native adapter: source или prebuilt.
3. Доставка сторонних native dependencies: отдельный package provider,
   явно настроенный external provider либо вложенный bundle.

Наличие C++ source не требует поставки SDK или runtime внутри artifact.
Наличие prebuilt adapter тоже не означает обязательного vendoring LibTorch.
Source + runtime bundle — полезный вариант: runtime уже поставлен, совместимые
headers для компиляции адаптера предоставляет отдельный SDK provider.

Обычный source package содержит свои `.s`, C/C++ sources/headers, resources и
requirements. Его упаковка **не требует установленного LibTorch, C++ compiler
или GPU**. Bytecode mode добавляет только необходимость Sputnik compiler.
Prebuilt adapter сначала собирается на publisher, затем пакуется.

## 2. Профили bundle

Writer option `torch/tools/package --bundle none|runtime|sdk`, default `none`.
В distribution manifest результат фиксирует `artifact.bundle`; filename
и слово `source` не заменяют эту metadata.

| Native adapter | Bundle | Что в archive | Что нужно потребителю извне |
|---|---|---|---|
| source | none | Свои sources/headers | Compiler, SDK, runtime providers |
| source | runtime | Sources + runtime closure | Compiler и SDK того же build/ABI |
| source | sdk | Sources + runtime closure + SDK assets | Compiler и platform SDK |
| prebuilt | none | Своя extension library | Совместимый runtime provider |
| prebuilt | runtime | Extension + runtime closure | Sputnik runtime, OS и объявленные drivers |

Каждая строка сочетается с source/bytecode/mixed Sputnik modules. В текущем
`torch` это максимум 15 содержательно разных combinations, а не
обязательная матрица для каждой сборки. Default строит один запрошенный artifact;
matrix mode получает явные axes/filters. SDK bundle без native source в v1
отклоняется: для готового адаптера SDK не нужен. Для native provider packages
их собственный payload определяется `kind`, а не этими consumer profiles.

`runtime` включает всё объявленное non-system runtime closure выбранных native
requirements. `sdk` добавляет их необходимые build assets. Частичное vendoring
под флагом полного runtime bundle в v1 не вводится: если компонент нельзя
включить, build сообщает конкретное requirement и завершается ошибкой.
OS libraries/frameworks и drivers остаются host requirements. Вложенных
archives и собственного исполняемого installer нет.

SDK bundle не содержит compiler, OS SDK или Python environment. Это возможность
собрать адаптер с поставленными dependency headers/libs на подходящей toolchain,
а не обещание полностью автономной сборки на пустой машине.

## 3. Runtime closure и SDK assets

Runtime payload строится из необходимых exported libraries, их static imports,
объявленных dynamic plugins и runtime data: например kernels, metallibs или
configuration, если конкретный build действительно обращается к ним.
Классификация каждого import: provider/bundled либо allowlisted OS requirement.
Нельзя объявить установленную через Homebrew библиотеку OS library лишь потому,
что она есть на машине publisher.

В closure не входят автоматически headers, CMake metadata, Python bindings,
tests, examples, caches и весь prefix установленного PyTorch. Для `sdk` SDK
adapter отдельно перечисляет headers, generated configuration и link metadata;
в LibTorch source build сюда относится и согласованный Gloo SDK. Рецепт closure
и его provider fingerprints входят в build metadata. Статически встроенные
сторонние компоненты тоже отражаются в component inventory и license records.

Один `libtorch_cpu` может содержать CPU и MPS. При одинаковом payload не нужны
два архива только ради разных labels. Supported backends, verified backends и
required backend различаются; фактическое MPS execution проверяется отдельно.
Удаление operators из монолитного binary не является pruning dependencies:
сборка уменьшенного LibTorch — другой provider build с собственным fingerprint.

Любой bundled machine code делает artifact platform-specific, даже если свой
адаптер поставлен как source. `bundle=none` не делает source автоматически
any/any: portability source/build requirements также должна быть подтверждена.
Minimum OS/libc/CPU — максимум требований всех поставляемых binaries.

## 4. Requirements и providers

В model нужны отдельные `native_requirements` и `native_providers`, а не
абсолютные include/library paths из машины publisher. Минимальный contract:

| Record | Содержание |
|---|---|
| Requirement | id, runtime/sdk kind, exact upstream version, ABI constraints, выбранный delivery/provider binding |
| Native extension | Существующие source/prebuilt facts + IDs потребляемых requirements |
| Runtime provider | logical runtime, exact build id, ABI, target/backends, exported link names, libraries/resources |
| SDK provider | headers/config/link exports, toolchain requirements и связь с конкретным runtime build |
| Resolved provider | Проверенный fingerprint, package artifact либо bundled members либо external input provenance |

Целевой YAML для managed package providers; parser хранит эти records, но
package-edge resolution/selection пока не реализованы. Текущая matrix использует
`provider=external` с explicit local bindings или `provider=bundled`:

```yaml
artifact:
  variant: source-none
  bundle: none
  dependencies:
    vendor/libtorch-runtime: "2.14.0-r1"
  build_dependencies:
    vendor/libtorch-sdk: "2.14.0-r1"
native_requirements:
  - id: libtorch
    kind: runtime
    version: "2.14.0"
    abi: "<libtorch-cxx-abi-id>"
    provider: package
    package: vendor/libtorch-runtime
  - id: libtorch-sdk
    kind: sdk
    version: "2.14.0"
    provider: package
    package: vendor/libtorch-sdk
    runtime: libtorch
native_extensions:
  - name: torch
    kind: source
    requirements: [libtorch, libtorch-sdk]
    # sources/headers и обычные adapter build facts
```

`vendor/...` — примеры references, не назначенные publisher accounts.
Native dependency edges принадлежат artifact: runtime —
`artifact.dependencies`, SDK/build-only — `artifact.build_dependencies`.
Общие Sputnik `dependencies` остаются release-wide. Bundle заменяет
соответствующий provider edge вложенным provider record; client не скачивает
тот же runtime второй раз как обязательную common dependency.
Версия provider package может отличаться от upstream LibTorch version;
например `2.14.0-r1` идентифицирует recipe build. Ranges solver не добавляется.

`provider=external` требует явного binding к установленному prefix через local
config/flag. В publisher manifest нет Homebrew paths. Probe читает metadata,
headers и binary facts, не вызывает library functions до native trust. Version
из имени directory недостаточна. Если статически установить provider identity
невозможно, нужен явный producer descriptor; произвольный prefix не принимается
за проверенный runtime. Обычный import не ищет новый SDK по cwd/PATH.

`provider=bundled` ссылается на `native_providers` внутри того же artifact.
Library records под provider содержат relative paths и exports; inventory
остаётся единственным полным списком physical members. Текущий плоский
`native_extensions[].libraries` при реализации переносится под provider, без второго
конкурирующего списка dependency files. Один payload может обслуживать несколько
extensions. SDK assets не смешиваются с runtime libraries.

SDK и runtime должны принадлежать согласованному provider build: одного
`TORCH_VERSION` недостаточно. Build plan берёт include paths из SDK, а link paths
из **выбранного runtime**. Нельзя собрать адаптер с bundled runtime A, незаметно
использовав libraries B из external `TorchConfig.cmake`.

## 5. Native-only packages

Для отдельно поставляемых runtime/SDK нужен `kind: native`:
`native_providers`, inventory/resources/license metadata, ноль Sputnik modules,
root отсутствует. `kind: library` остаётся default и требует обычные modules без обязательного root.
Не создавать фиктивный `.s` только ради текущей проверки «хотя бы один module».
Обычная C++ library provider не обязана экспортировать Sputnik extension ABI.

Runtime и SDK — отдельные package references, связанные exact dependencies и
build identity. Они не два одновременно выбираемых variants одного package:
правило «один active variant на ref» сохраняется. SDK не устанавливается при
обычном запуске prebuilt package. Build-only providers не активируют Sputnik
modules в application runtime graph.

Release common metadata включает package kind и logical export/module set;
representations, provider delivery, build edges и physical payload — artifact
facts. Provider package получает обычные immutable version/variant/digest,
trust/quarantine и size limits; специального сервиса LibTorch не требуется.

## 6. Selection, lock и один runtime в процессе

Selection сначала применяет пользовательскую bundle policy (`auto` по
умолчанию; `artifact.bundle` у создаваемого архива по-прежнему `none`), затем
source/prebuilt, target/backend и ABI/provider constraints.
Иначе thin и bundled prebuilt с одинаковым target дают необъяснимый tie.
При `auto` совместимый готовый runtime предпочтительнее source adapter; native и
large-profile пакеты по-прежнему требуют соответствующих разрешений. Явный
`--bundle none` исключает runtime/SDK bundles, `--bundle runtime` не включает SDK.
Импорт выбирает из установленных вариантов и не скачивает/не собирает пакет.
Lock необязателен: он фиксирует точный вариант и хэш для воспроизводимости.
`--variant` по-прежнему не отключает compatibility checks.

Lock v1 фиксирует profile, exact runtime/build edges, provider delivery,
version/build/ABI identity и fingerprints. Bundled providers привязаны к artifact
digest, package providers — к pinned dependency artifact. External fingerprint
проверяется по local binding; абсолютный prefix хранится в local descriptor,
не является переносимой publisher metadata. Изменение prefix не обновляет lock.

На один logical singleton runtime в одном процессе выбирается один provider;
это касается LibTorch и его общих singleton dependencies, например OpenMP.
Равенство upstream version либо filenames не доказывает совместимость.
Разные build/fingerprint requirements дают `NATIVE_ABI_CONFLICT`, а не выбор
первой найденной dylib. `RTLD_LOCAL` не считается изоляцией C++ runtime.
Смена runtime требует нового процесса/worker, не горячей замены живых handles.

Bundled/package/external — способ доставки, а не разрешение загрузить три копии
одного runtime. Для одинакового provider fingerprint client готовит один
canonical provider root в local cache и связывает все consumers с ним.
Это derived client state, не новый file-level CAS на сервере.

Нужен проверенный prepared dependency view: extension bytes копируются/reflink
в client-owned layout, loader-relative imports ведут к выбранным provider roots.
Publisher relocation contract должен поддерживать этот layout. Копирование и
локальные directory bindings не меняют signed library bytes; archive links
остаются запрещены. Нельзя просто загрузить две исходные extension paths,
каждая из которых hardwired на собственный `lib/vendor`. До conformance такого
binding host adapter отклоняет подобный graph, даже если ABI labels совпали.
Install не вызывает patchelf/install_name_tool/resign. VM и fully native
executable используют один provider plan; executable сохраняет exports host C ABI.

Missing prepared native state при offline run даёт явную ошибку. Source build и
восстановление SDK выполняются только explicit preparation/install с build opt-in;
обычный run не скачивает SDK и не запускает C++ compiler.

## 7. Лицензии и component inventory

Redistribution оценивается для фактического payload каждого provider build,
включая транзитивные libraries, встроенные компоненты и SDK headers. Наличие
разрешающей лицензии у основного PyTorch не устанавливает условия всех остальных
компонентов. В локальном эксперименте есть, в частности, GCC runtime files и
`COPYING.RUNTIME`; один root LICENSE не заменяет разбор этого набора.

`meta/components.json`, schema `sputnik.native-components.v1`, содержит component
name/version/origin, exact build identity, SPDX expression при наличии, mapping
на inventory files либо embedded-in binary, LICENSE/NOTICE/exception paths и
условия поставки дополнительных материалов, если они применимы. Неизвестная
лицензия остаётся неизвестной (`NOASSERTION`), не получает BSD-3-Clause по имени
основного runtime. Inventory фиксирует actual final files после relocation/signing.

Bundler проверяет полноту component/file mapping, наличие объявленных license/
notice/exception/source-material files и явное redistribution declaration в
recipe. Missing metadata и запрещённый recipe компонент — ошибки bundle build,
без интерактивных подтверждений и silent omission. SPDX string, пользовательская
декларация или успешный тест не являются автоматическим юридическим заключением;
packer не пытается доказать выполнение всех условий лицензий. Runtime и SDK
профили могут иметь разные наборы компонентов и материалов.

Первичные тексты для проверки, не blanket clearance текущих архивов:
[PyTorch LICENSE](https://github.com/pytorch/pytorch/blob/main/LICENSE),
[GCC Runtime Library Exception](https://www.gnu.org/licenses/gcc-exception-3.1.en.html)
и [GCC FAQ](https://www.gnu.org/licenses/gcc-exception-3.1-faq.en.html).
Для release сохраняется текст именно поставляемого build, а не только URL main.

## 8. План реализации и проверки

Оставшиеся integration gates и критерии — [R1c](IMPLEMENTATION_PLAN.md#r1c-native-providers-и-optional-bundles).

1. Requirements/providers, package kinds и build-only edges в core model,
   parser/writer/inspector; fixtures нового v1, без требований читать прототип.
2. Local/external LibTorch runtime/SDK adapter, fingerprints и проверка
   согласованности; маленький source archive строится без LibTorch/clang/GPU.
3. Source build plan и native cache с provider inputs, общий VM/native binding
   descriptor. Сначала local providers, без registry service.
4. Explicit runtime bundler: binary imports + declared dynamic resources,
   component/license inventory, relocation/signing до final hashes.
5. Optional SDK assets, без compiler/OS SDK/Python; managed native-only providers
   и exact build dependencies через существующий local store.
6. Profile-aware selection/lock и migration development fixtures внутри v1.
   HTTP/server adapters — отдельная следующая работа.

Tests: source none без SDK/compiler; bytecode/mixed без случайного vendoring;
source+runtime с external matching headers; SDK/runtime mismatch до compilation;
prebuilt none с bound provider; prebuilt runtime без compiler/SDK/Homebrew prefix;
SDK bundle source rebuild на host toolchain без dependency prefix; VM/full-native
CPU и фактический MPS без fallback; missing resources/licenses; relocation/hash
tamper; two consumers с одним canonical provider, conflict разных builds;
reproducible pack; runtime offline без SDK; changed external files не обходят lock.

## 9. Локальная реализация sputniktorch от 2026-10-20

`tools/package` по умолчанию строит один source/source/none artifact без SDK,
C++ compiler и GPU probes. `--matrix` строит все 15 combinations и два native-only
provider artifacts: runtime и SDK. Новый вывод находится в
`build/packages-v1-bundles`; прежние шесть экспериментальных bundles в
`build/packages` не переписываются. Команды и текущие границы описаны в
[package-building.md](../../sputniktorch/docs/package-building.md).

Local providers привязываются через colon-separated `SPUTNIK_NATIVE_PROVIDERS`
(extracted native-only directories с manifest/inventory). Provider preparation
проверяет inventories, version/ABI/build identity и SDK/runtime pairing. Runtime
и SDK помещаются в canonical roots в `SPUTNIK_NATIVE_EXT_CACHE`; prebuilt adapter
копируется без изменения bytes, а local `lib/vendor` связывается с canonical
runtime. Fingerprints входят в native compilation cache. Это не переносимый
application lock и не managed package dependency resolution.

Matrix CPU/MPS проверяется после relocation, с отключённым MPS fallback;
prebuilt VM не получает C++ compiler или SDK binding. Для каждой из пяти
native/bundle combinations проверяется full-native bytecode consumer.
Source-only repack с отсутствующим dependency prefix должен воспроизвести digest.
Проверенные результаты привязываются к report digest в verification/result.json.

SDK содержит compiler-observed header closure для текущего adapter/target;
универсальность SDK этим не обещается. Component inventory отображает фактически
поставляемые libraries/headers на upstream package attribution и сохранённые
license/notice/exception/recipe materials; оно не заменяет проверку условий
встроенных upstream компонентов перед публичной публикацией. Declarative resources
и другие platform/provider adapters требуют отдельных conformance tests.

Shared tar install, managed provider dependencies, profile-aware registry
selection и portable application lock остаются integration gates R1c/R4.
Сервис не реализуется этой работой.
