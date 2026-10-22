# Orbit: достаточность и необходимые расширения

Проверка: 2026-10-20, по текущим рабочим деревьям Orbit/Sputnik, включая
незакоммиченные изменения. После исходного source audit реализован первый
transport extension — fixed-length streaming; полный production soak не проводился.
Код registry service по-прежнему не создаётся.

Для CLI регистрации и account API базового Orbit достаточно. Для полного
контракта registry определены два общих transport extension: первый реализован
локально, второй ещё запланирован. Storage/inspector и product authorization
остаются вне framework core.

## Что переиспользуется

| Потребность | Текущий механизм | Что пишет registry |
|---|---|---|
| JSON API | Routes, middleware, `ParamSchema.check/expect`, status/headers | Endpoint schemas, domain errors, response envelope |
| Bearer authorization | `Request.header`, route middleware | Token lookup/digest, live ACL/scopes, revoke, grants |
| GitHub Device Flow | Stdlib `net.http.Client`, HTTPS и bounded response reads | Provider adapter, attempt state, interval/expiry/deny policy |
| Token/signature primitives | `SecureRandom`, one-shot `Digest`, Ed25519 `Signature` | Credential/signing-key lifecycle, key bindings и receipts |
| Upload | `RequestBodyStream.read_chunk(max_bytes:)` | Profile/expected-size admission, temp-file writes, traffic/quota accounting |
| DB | `orbit-orm`, ORM/SQLite transactions и pool | Accounts/namespaces, migrations, leases, idempotency/outbox |
| Operations | Lifecycle/integrations, trusted Host/proxy, telemetry/metrics | Registry readiness checks, rate/quota policy, usage spool/projections |

Это primitives, не готовый OAuth/account/permission subsystem. Device Flow не
требует frontend, browser sessions, callback или изменений routing Orbit.
GitHub endpoints фиксируются provider config, redirects выключены; ответы
читаются с finite byte limit. Для full-native использовать static Str/Bytes
HTTP requests и явный JSON/form encoding: convenience JSON/form helpers имеют
VM bridge в текущем Sputnik. Клиентский timeout не является общим deadline:
он применяется к отдельным IO, а DNS пока synchronous; absolute-deadline
conformance provider adapter проверяется отдельно.

Источники: [Orbit core](../../orbit/src/orbit.s),
[ORM integration](../../orbit/packages/orbit-orm/src/orbit/integrations/orm.s),
[HTTPS/native boundaries](../../sputnik-lang/docs/engineering/http-tls.md),
[HTTP client](../../sputnik-lang/runtime/net_http_client_native.inc),
[Signature](../../sputnik-lang/docs/stdlib-signature.md).

## Статус transport extensions

1. **Потоковый ответ известной длины — реализован локально.** Registry API обещает
   `Content-Length` для artifact download до 2 GiB без full-file buffering.
   `stream(length: size, ...)` добавлен в Orbit/Controller и
   `net.http.ServerResponse.stream`, включая VM/native constructor и writer.
   Прямая установка framing headers остаётся запрещённой; `length: null`
   сохраняет chunked default. Header/security/telemetry wrappers сохраняют length.
   Writer ограничивает overflow, отвергает short EOF, не допускает chunk
   extensions/trailers в fixed mode; HEAD сообщает длину без producer/file read.
   Buffered response или одна правка headers это требование не выполняют.
   Это требование выбранного registry API, не запрет RFC на потоковое скачивание
   без Content-Length. Исходный [design Orbit, §14.1](../../sputnik-lang/DESIGN-web-framework-2026-07-13.md)
   ограничил первую реализацию chunked responses; запрет header сохраняет
   однозначное framing этого режима.
2. **Наблюдение окончательного transport result — ещё требуется.** `Telemetry.finish_response`
   завершает streaming observation сразу после producer, а runtime завершает
   framing позже. Счётчик `ObservedStreamBody` также не видит bytes частичного
   write, закончившегося ошибкой. Нужны actual request/response payload counters
   и ровно одно terminal notification после завершения записи либо failure/
   cancel/disconnect, с request correlation. В Orbit оно доступно adapter и
   telemetry; callback не пишет business DB синхронно и не меняет response.
   Payload bytes отделяются от framing bytes. Это origin transport result,
   не подтверждение получения клиентом или trusted edge.

Оба изменения затрагивают `sputnik-lang/net.http` и Orbit. Fixed-length tests:
[`vm_net_http_tests.cpp`](../../sputnik-lang/tests/vm_net_http_tests.cpp) и
[`fixed_length_selftest.s`](../../orbit/test/fixed_length_selftest.s), новый Orbit
test входит в CI. Terminal-observer tests ещё должны покрыть partial write,
failure после producer, disconnect/cancel/timeout и shutdown.
Full download считается только по successful terminal result и exact size;
producer completion сам по себе таким событием не является.

Проверка реализации, 2026-10-20, macOS aarch64: HTTP VM suite — 790 checks;
Orbit stage1/stage2/stage4 и новый fixed-length suite — pass в VM. Новый suite
собран с `--require-full-native` и выполнен успешно, без runtime bridge/bytecode
fallback. Raw-wire pipelining проверен в VM, а оба execution paths проверяют
HTTP Client, keep-alive, HEAD с длиной 2 GiB, short/overflow, zero length,
invalid options, chunked compatibility и middleware/telemetry preservation.
Исправлен также native producer adapter: его возвращаемое значение игнорируется,
поэтому trailing StreamBody/telemetry event не обрывает корректный keep-alive.
Результат не заменяет release pin, Linux matrix или production soak.

Источники: [StreamingResponse](../../orbit/src/orbit.s),
[Telemetry](../../orbit/src/orbit/telemetry.s),
[server writer](../../sputnik-lang/runtime/net_http_server.cpp).

### Соответствие RFC

[RFC 9112 §6.2](https://www.rfc-editor.org/rfc/rfc9112.html#section-6.2) запрещает
отправлять Content-Length вместе с Transfer-Encoding. Простое снятие guard в
chunked writer нарушило бы это требование; реализован отдельный fixed mode. По
[§6.3](https://www.rfc-editor.org/rfc/rfc9112.html#section-6.3), fixed-length и
chunked — разные допустимые способы определить границу HTTP/1.1 body.
Порционная запись из файла не требует chunked framing: при известной длине
можно записывать N bytes частями без whole-body buffering и без Transfer-Encoding.
Short transfer считается incomplete, соединение не переиспользуется.

[RFC 9110 §8.6](https://www.rfc-editor.org/rfc/rfc9110.html#section-8.6) рекомендует
origin server отправлять Content-Length при известном заранее размере и
отсутствии Transfer-Encoding, с оговорёнными method/status exceptions.
Существующий chunked HTTP/1.1 режим допустим; fixed-length расширяет API для
известного размера. Raw framing headers по-прежнему контролирует transport,
а приложение сообщает length через typed option с проверкой числа bytes.

## Limits и storage: границы ответственности

`App.server(max_body_bytes:)` задаёт общий transport ceiling, по умолчанию 1 MiB.
Для native-large его нужно явно поднять до разрешённого max artifact; JSON
routes при этом обязаны иметь собственный меньший cap **до** materialization.
Текущий `request.params.json` читает whole body до schema validation. Registry
может без расширения framework bounded-read body stream, затем вызвать
`Json.parse` и `ParamSchema.expect(value)`; после этого повторно читать
`request.params.json` нельзя. Общий `body_limit`/bounded JSON helper Orbit полезен,
но не является prerequisite. Total upload deadline и periodic revoke проверяются
application adapter; read timeout одного IO не заменяет total deadline.
В текущем native server request-body read использует blocking guard: native
closures ещё не сохраняют continuation для безопасного park/resume. Число OS
workers поэтому важно для slow uploads. R5/R6 проверяют доступность коротких
API requests при двух медленных uploads; connection capacity не принимается за
число независимых native workers. Это capacity gate, не готовая гарантия из README.

`fs.sync(file/directory)`, `fs.atomic_write_*(durable: true)` и exclusive create
уже реализованы. Atomic write заменяет существующий destination и принимает
whole value: он не является streaming immutable CAS promotion. У generic
`fs.rename` нет no-replace option, у `fs.File.open` нет отдельного no-follow flag.
Registry storage adapter обеспечивает нужные path/finalize invariants и reuse
доступных sync primitives; это не задача Orbit.

Публичный `Digest` пока one-shot. В package C++ core уже есть bounded
`sputnik::pkg::file_sha256` с buffer 64 KiB и incremental OpenSSL context.
R2 экспортирует/переиспользует этот core через native facade; для одного прохода
upload можно открыть incremental context. Новая реализация SHA-256 не нужна.
Bounded package inspector и отдельный worker также относятся к registry/package
core, а не к framework.

Источники: [IO primitives](../../sputnik-lang/docs/engineering/remote-io-api.md),
[fs dispatch](../../sputnik-lang/runtime/stdlib_fs.cpp),
[file hash](../../sputnik-lang/package/distribution.cpp),
[Digest](../../sputnik-lang/docs/stdlib-digest.md).

## Включение в план

- R2/R3: native facade/storage contracts, reuse существующего sync/file hash.
- R5: два transport extension до принятия download/statistics contract; bounded
  per-route bodies, deadlines, quota admission, существующие Orbit middleware.
- R8: Device Flow adapter и CLI account/token/key operations; framework auth
  subsystem/HTML кабинет не требуются. Отдельно пройти текущие открытые
  [Orbit production gates](../../orbit/docs/PRODUCTION_READINESS.md), включая
  clean pinned build и реальные canary/soak/fault tests.
