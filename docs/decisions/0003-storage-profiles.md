# ADR-0003: bootstrap и production storage-профили

Статус: accepted for architecture baseline.  
Дата: 2026-07-25.

## Контекст

Текущий `sputnik-orm` и `sqlite3-sputnik` позволяют построить single-node сервис,
но в workspace нет PostgreSQL adapter и S3 client. Ожидание всех production
пакетов заблокирует проверку домена и protocol.

## Решение

Поддерживаются две явно разные конфигурации:

- bootstrap: SQLite WAL + local filesystem CAS, один узел;
- production: PostgreSQL + S3-compatible CAS, несколько web/worker replicas.

Domain и use cases зависят от repository/blob ports. Storage-specific детали
не входят в controllers и domain entities.

## Последствия

- private registry и end-to-end protocol можно проверить раньше;
- production нельзя заявить готовым на bootstrap storage;
- миграция данных требует отдельного export/import с проверкой digest;
- package/repository contracts должны иметь parity suites для обоих adapters.
