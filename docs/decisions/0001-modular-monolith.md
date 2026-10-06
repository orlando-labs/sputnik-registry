# ADR-0001: модульный монолит

Статус: accepted for architecture baseline.  
Дата: 2026-07-25.

## Контекст

Registry требует HTTP API, доменных транзакций, фоновой проверки артефактов,
аудита и нескольких storage adapters. На старте команда и нагрузка неизвестны,
а `sputnik-orm` пока поддерживает только SQLite.

## Решение

Система реализуется как модульный монолит с ports-and-adapters границами.
Один проект собирает две роли: stateless web и background worker. Они
разделяют domain/application modules и metadata database, но запускаются
отдельными процессами.

## Последствия

- ownership, publish и audit изменяются в одной database transaction;
- нет внутренних HTTP/RPC contracts и distributed tracing overhead;
- bootstrap работает на одном узле;
- production масштабирует web/worker отдельно;
- модуль можно выделить в сервис только после измеренной причины и
  стабилизации его port contract.
