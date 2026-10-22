# Toolchain и registry client v1

Статус на 2026-10-22: HTTP native install, выбор совместимого variant, CUDA
driver/device checks и variant/hash/origin в application lock реализованы.
Точные команды, поддержанные проверки и ограничения:
[package-native-install-v1](../../sputnik-lang/docs/engineering/package-native-install-v1.md).
Ниже также сохранены разделы целевого контракта (registries.toml, signatures,
token/key subcommands); их наличие не означает реализацию CLI.
База: [существующий package store](../../sputnik-lang/docs/engineering/package-store-v1.md).

Основной hub по умолчанию — `https://registry.sputnik-lang.online`.
`sputnik pkg install NAME` поддерживает scoped references и операторские root
aliases, portable source/bytecode, exact dependencies и проверку SHA-256.
`--registry`/`SPUTNIK_PKG_REGISTRY` меняют origin или выбирают local directory.
`--credentials`/`SPUTNIK_PKG_CREDENTIALS` задают private origin-bound JSON token
для закрытого registry. Без credentials публичный hub читается анонимно.

## 1. Один установщик, несколько sources

`DirectorySource`, `ArtifactSource`, `FilesystemRegistrySource` и
`HttpRegistrySource` подают artifact в общий verify/stage/native-prepare/install
pipeline. HTTP adapter не пишет собственный module index и не создаёт второй
resolver. `run`, `build`, `import`, обычный notebook Run остаются offline.

Существующий порядок поиска сохраняется:

1. Явные modules/build declarations/notebook links.
2. `<anchor>/packages/` для local overrides.
3. `SPUTNIK_PKG_HOME`, default `~/.sputnik/packages`.

Anchor — каталог entry source/build manifest/notebook, не shell cwd;
parent traversal нет. Local override затеняет весь пакет, не добирая modules
из глобального build. Версия или provider conflict завершают resolution ошибкой.

## 2. PackageReference и версии

Все ограничения — ASCII, case-sensitive; uppercase reference отклоняется,
не приводится к lowercase молча. Нет Unicode normalization/case aliases.

| Поле | Grammar и лимит |
|---|---|
| publisher | `[a-z][a-z0-9_]{0,62}`; также допустимый module segment |
| local package name | `[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*`, максимум 64 bytes |
| reference | Один local name либо ровно `publisher/local_name` |
| version | `major.minor.patch[-prerelease]`, максимум 64 bytes |
| major/minor/patch | `0` либо `[1-9][0-9]*` |
| prerelease | Dot-separated `[a-z0-9-]+`; чисто numeric identifiers без leading zero |

Registry v1 принимает этот filesystem-safe subset SemVer: без `+build`,
uppercase prerelease и `v` prefix. Exact version equality — byte equality;
order — numeric release fields, затем SemVer prerelease order. Это не ranges API.
`local`, opaque versions и legacy names остаются локальной совместимостью,
но новые network publications с ними запрещены. Public name admission также
проверяет reserved handles/stdlib module ids и operator grants.

Shared C++ `PackageReference` используется manifest/build parser, artifacts,
source installer, filesystem registry, HTTP adapter, remove, discovery и locks.
Sputnik inspector использует тот же validation core. TOML dependency keys
со slash записываются quoted: `"slowpilot/example" = "1.2.0"`; JSON/YAML keys
остаются обычными strings. Manifest identity не подменяется именем checkout.

Для legacy unscoped пакетов parser сохраняет прежнюю локальную grammar.
Network admission строже. Artifact — tar.gz `sputnik.pkg.v1` с distribution
manifest v1: [PACKAGE_FORMAT](PACKAGE_FORMAT.md). Text prototype заменяется
на месте; его чтение не является требованием. Capabilities объявляет scoped refs,
archive format и source/bytecode representations.

## 3. Filesystem mapping

```text
$SPUTNIK_PKG_HOME/
  orbit/1.0.0-rc.1/source/orbit-1.0.0-rc.1-source.sputnikpkg
  slowpilot/example/1.2.0/macos-aarch64-cpu/example-1.2.0-macos-aarch64-cpu.sputnikpkg
  .install/<random-session>/
```

Один дополнительный уровень variant применяется к unscoped и scoped packages.
Каждый component проходит validation до filesystem calls; raw ref не является
path. Discovery учитывает namespace/version/variant и совместимость.
Filesystem registry использует тот же mapping. Это breaking изменение ещё
не применённой v1; prototype install directories можно пересоздать.

В variant directory: exact archive, verified members/manifest/inventory,
`modules.index`, `.registry-source`, prepared native descriptor и service caches
`.compiled`/`.native`. Archive не может подменять service metadata. Descriptor
содержит entry library path/hash; `native-files.meta` дополнительно связывает
подготовленные библиотеки с variant, archive digest и SHA-256 каждого файла.

Разные variants одной версии могут coexist. Другие bytes для одного
`ref/version/variant` конфликтуют независимо от source. Graph активирует одну
version/variant; modules разных variants не смешиваются. Local `packages/`
override затеняет весь package. Uninstall с --version удаляет все его variants;
optional --variant вместе с --version удаляет один. Подробности —
[NATIVE_ARTIFACTS](NATIVE_ARTIFACTS.md).

Существующая установка с другим registry origin отклоняется; origin в lock
не делает разные registries эквивалентными.

## 4. Configuration и source binding

Предлагаемый файл `$HOME/.sputnik/registries.toml`:

```toml
schema = "sputnik.registries.v1"
default = "main"

[registries.main]
url = "https://registry.example.test"
trust_profile = "public"
trust_key_file = "/path/to/registry-keyring.toml"

[namespaces]
slowpilot = "main"
```

Секретных tokens/private keys в этом файле нет. Token читает client из
`SPUTNIK_REGISTRY_TOKEN` либо protected credential file; не через CLI argument.
Приоритет: `--registry` → `SPUTNIK_PKG_REGISTRY` → namespace binding → default.
`--registry` и env сохраняют directory form и дополнительно принимают HTTPS URL
или `@alias`. Namespace mapping задаёт запрет обращения к другому source:
конфликт explicit selection с binding — ошибка, пока binding явно не изменён.
В `--locked` source из lock имеет приоритет; другой `--registry` — ошибка.

Canonical network origin: lowercase DNS host и scheme, HTTPS, default port
удаляется, без path/query/fragment/userinfo; API всегда `/api/v1`.
Registry под URL subpath не входит в v1. Origin equality не использует DNS/IP
эквивалентность. Filesystem source имеет отдельный kind, canonical absolute path.
HTTP разрешён только для явного loopback development profile; TLS verification
отключить нельзя. Default public URL — `https://registry.sputnik-lang.online`.
`trust_profile` — `public` по умолчанию; `private` задаётся только локальной
конфигурацией пользователя. Capability response не может понизить эту policy.
Пустой/root URL path нормализуется в отсутствие path; другие paths отклоняются.

Транзитивная dependency наследует source родителя, если namespace binding не
назначает другой конкретный source. Нет fallback по публичным registries,
search по имени модуля или автоматического namespace promotion. Для первого
network install без lock несовместимый либо отсутствующий bound source — ошибка.

## 5. CLI

Реализован просмотр каталога из SDK (без скачивания архивов):

```sh
sputnik pkg versions sputniktorch
sputnik pkg versions slowpilot/sputniktorch --json
sputnik pkg variants sputniktorch@0.1.2
sputnik pkg variants slowpilot/sputniktorch@0.1.2 --json
```

`versions` собирает все страницы index и сортирует версии по SemVer убыванию;
prerelease включены, yanked помечены в текстовом выводе. `variants` требует
точную версию в форме `reference@version`, показывает все доступные варианты
независимо от платформы текущей машины и исключает quarantined artifacts.
По умолчанию одна запись на строку; `--json` возвращает index/release metadata
с canonical `package_ref`, target, compatibility и SHA-256. У собранного index
`next_cursor` равен null. При `409 INDEX_CHANGED` чтение начинается заново, до
трёх попыток; частичный список при ошибке не выводится.
Root alias разрешается самим index/release endpoint; отдельного GET package
перед ним нет. Canonical identity проверяется в ответе и на каждой странице.

Обе команды принимают `--registry URL` и `--credentials FILE`; default —
`https://registry.sputnik-lang.online`, затем применяются `SPUTNIK_PKG_REGISTRY`
и явный `--registry`. Credentials берутся из `--credentials`,
`SPUTNIK_PKG_CREDENTIALS` или сохранённого для exact origin файла; public reads
работают без входа. JSON сохраняет numeric sizes/revisions без округления.

Целевые формы расширяют `pkg`, старые `package-*` остаются совместимыми:

```sh
sputnik pkg install ./library
sputnik pkg install ../library --version 1.2.0
sputnik pkg install ./library-1.2.0.sputnikpkg
sputnik pkg install slowpilot/example --version 1.2.0 --registry @main
sputnik pkg install slowpilot/example --registry @main
sputnik pkg install --locked ./train.s
sputnik pkg publish ./example-1.2.0.sputnikpkg --registry @main --publisher-key-file /protected/key
sputnik pkg lock ./train.s
sputnik pkg uninstall slowpilot/example --version 1.2.0
sputnik pkg env
```

GitHub signup/login, `whoami`, `logout` и `publish` реализованы в CLI.
Управление publisher keys и дополнительные token subcommands ниже остаются
целевым интерфейсом:

```sh
sputnik pkg login --registry @main
sputnik pkg whoami --registry @main
sputnik pkg token list --registry @main
sputnik pkg token create --package alice/example --label ci --expires-in 7d --registry @main
sputnik pkg token revoke TOKEN_ID --registry @main
sputnik pkg key add /protected/publisher.ed25519.pub --registry @main
sputnik pkg key list --registry @main
sputnik pkg key revoke KEY_ID --registry @main
sputnik pkg logout --registry @main
```

`pkg login` показывает GitHub device code/verification URI и ожидает подтверждения
с bounded polling. Затем показывает verified identity и, при первом входе,
спрашивает свободный personal namespace в терминале; complete выдаёт registry
token. Нет собственного web frontend, browser callback или постоянного login
secret. Сохранённый token связан с exact registry origin, credentials mode 0600.
Повторный login проверяет expected account; другая identity требует явного
выбора пользователя. Native/large grants не выводятся из успешного login.
Personal token покрывает namespace create/publish, token/key management и read;
CI token получает только выбранный exact package publish scope, без management
(read добавляется при authenticated registry). CLI явно показывает scopes/expiry.
Token secret не передаётся аргументом команды. CI token показывается один раз
только в интерактивном terminal/explicit secret-output mode, не в обычном log.
`pkg logout` отзывает текущий token; сетевой failure не объявляется успешным revoke.
Device attempt, потерянный ответ complete и cancel — [API, §4.1](API.md).

Dispatch сохраняет существующие files/directories, включая relative directories
со slash. Явные path forms `./`, `../`, absolute path и suffix `.sputnikpkg`
при отсутствии завершаются local error без network lookup. Остальная строка,
соответствующая PackageReference, может быть registry ref; `a/b/c` — ошибка.
Для существующего directory `slowpilot/example` local precedence сохраняется;
CLI выводит тип source. Для однозначного local intent следует писать `./`.
`--locked` — отдельная форма без package argument; target задаёт anchor lock.

`pkg publish` принимает готовый artifact и optional detached signature, не
выполняет package build автоматически. `--publisher-key-file` создаёт detached
signature. Upload сохраняет session/idempotency metadata для retry; статус
polling bounded. Server rejection не вызывает незаметную смену версии.

Без `--version`: client получает index, выбирает максимальную stable active
версию с совместимым artifact; если stable нет, максимальную prerelease. При равенстве
order tie невозможен в registry subset. Yank исключается. Exact request
проверяет exact identity. Ranges в flags/manifests возвращают unsupported error.

## 6. Install transaction

1. Получить bounded metadata/index, выбрать exact plan всех dependencies;
   обнаружить cycles, duplicate module providers и incompatible exact versions.
2. Выбрать согласованные platform variants и ABI providers по
   [NATIVE_ARTIFACTS](NATIVE_ARTIFACTS.md); зафиксировать source, variant, digest,
   size, compatibility и trust каждого элемента.
   Индекс никогда не заменяет проверку manifest внутри artifact.
3. Скачать в private staging; проверить compressed/decompressed limits, size,
   SHA-256, release signature, tar/inventory/paths, bytecode и manifest/index.
   Source modules проверяются server на admission без их compilation.
4. Сверить объявленные и фактические transitive dependencies; при расхождении
   остановиться. Version metadata immutable, retry не выбирает другую версию.
5. Скомпилировать source modules в staging с dependency/macro providers, без
   module initialization, по штатной compile-time policy. Bytecode-only modules
   проходят ABI/profile checks без source fallback. Source compilation не
   разрешает network/host grants автоматически.
6. Для network native packages требуется `--allow-native`. Prebuilt libraries
   статически проверить и записать descriptor без dlopen и C/C++ compiler.
   Native source build разрешён только с explicit `--build-from-source`;
   compile-only preparation не запускает library constructors.
7. Per-ref/version/variant lock + no-replace atomic directory promotion. Discovery видит
   только полностью готовую version directory. При crash staging удаляется/reclaimed.
8. Записать variant-bound provenance/index; при одинаковых existing bytes проверить
   состояние и завершить/починить подготовку. Иные bytes — `INSTALLED_DIGEST_CONFLICT`.

Per-package promotion атомарна; вся транзитивная установка не является общей
filesystem transaction. При сбое уже готовые dependencies остаются; корневой
пакет не объявляется установленным. Lock при failed install не меняется.
Existing local installation проходит тот же pipeline после рефакторинга.

Downloads: timeout/byte limit, не более 3 redirects; credentials не пересылаются
на другой origin, HTTPS downgrade запрещён. В v1 server отдаёт bytes сам;
неожиданный external artifact URL отклоняется. Retry только bounded transient
GET/PUT с теми же digest/session; hash mismatch — terminal integrity error.

## 7. Locks и offline restore

Application lock остаётся `sputnik.packages.lock.v1`; source/variant fields добавляются
в ещё не применённую v1 на месте. Synthetic embedded `sputnik.lock.v1` прототипа
удаляется из archive: exact library declarations уже находятся в manifest.
Номер package format не определяет номер application lock.

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

Другие source kinds: `filesystem_registry` с canonical path, `local_artifact`
без исходного пути как обязательного runtime input и `source_override` с
fingerprint source manifest/modules/native inputs и объявленных resource files.
Для локального directory, установленного в общий home, `local_artifact`:
после удаления checkout import продолжает работать. Source override требует
текущего source и fingerprint. У source override digest обозначает fingerprint,
у остальных — bytes artifact.

- Reader/writer используют обновлённую v1, без отдельного legacy parser/мигратора.
- Прототипный lock без required source/variant fields отклоняется с указанием
  пересоздать его через `pkg lock`; source не угадывается по имени пакета.
- `pkg lock` намеренно делает новую фиксацию из установленных packages/provenance,
  сетевого auto-update не делает. Обычный run lock не меняет.
- `pkg install --locked target` устанавливает только pinned version/variant/digest
  из pinned source. На несовместимом host — `LOCK_TARGET_MISMATCH`; relock явный.
  `--offline` требует наличия artifacts в home; смена variant/fallback запрещена.
- Для отсутствующего `local_artifact`/`source_override` restore требует явный
  local input. Неполный lock — ошибка.
- Ранее сгенерированные artifacts/locks для разработки пересоздаются на новой
  toolchain; сохранение совместимости с прототипом не является условием v1.

## 8. Native и notebook

Native extension `kind = source` собирается у client; `kind = prebuilt` использует
вложенную shared library и declared dependency libraries. Sources prebuilt
extension не обязательны. LibTorch/BLAS/CUDA runtime можно bundlе внутри variant
либо доставить отдельным exact package-provider. Target/ABI/import/loader contract
и пример manifest — [NATIVE_ARTIFACTS](NATIVE_ARTIFACTS.md).

Bundle policy и provider model — [NATIVE_BUNDLES](NATIVE_BUNDLES.md): default
`none`, explicit `runtime`/`sdk`, независимо от module/native representations.
Policy отсекает variants до target-specificity tie resolution. SDK/build-only
providers не скачиваются для prebuilt runtime; их exact edges/fingerprints
фиксируются отдельно в lock. Source+runtime bundle может использовать external
SDK, если build identity совпадает. External prefix — local binding, не publisher
path. VM и executable используют один canonical provider plan, без duplicate
LibTorch/OpenMP runtimes и runtime search по ambient PATH. Local explicit bindings
через `SPUTNIK_NATIVE_PROVIDERS` и prepared provider view поддерживаются вместе
с managed selection, HTTP install и lock integration. Версии технических
providers, ABI и build identity проверяются до компиляции/загрузки.

Install prebuilt не требует C/C++ compiler и не загружает библиотеку; Sputnik
source при наличии компилирует сам Sputnik. Build native приложения может
по-прежнему требовать linker/compiler. Prebuilt invalidity не запускает build
fallback; native source compilation только `--build-from-source --allow-native`.
Runtime `--grant ffi` — отдельное разрешение на load/execute. Native ABI query
после dlopen не отменяет возможное выполнение constructors во время загрузки.

Для source-built library cache key включает variant/artifact/native input digest,
host/OS/libc/ext ABI, compiler/options и external ABI providers. Для prebuilt
проверяются actual binary hash, target/runtime requirements и dependency closure;
cache не подменяет source archive и не переписывает binary/rpaths/signatures.
Нынешний one-path native-library.meta требуется заменить structured descriptor.

Открытие notebook не скачивает/не компилирует пакеты. CLI install → существующий
resolver; Apply/evaluation выбирает совместимый installed variant. Обычный Run
сохраняет loaded generation, uninstall/новая установка действуют после Apply/restart.
Native trust notebook и explicit prepared links сохраняются.
