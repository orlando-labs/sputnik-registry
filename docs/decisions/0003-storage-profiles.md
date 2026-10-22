# ADR-0003: bootstrap и production storage-профили

Статус: принято в design v1; уточнено ADR-0005.
Дата: 2026-07-25; актуализация 2026-10-16.

## Контекст

Текущий `orm` и `sqlite3` позволяют построить single-node сервис,
но в workspace нет PostgreSQL adapter и S3 client. Ожидание всех production
пакетов заблокирует проверку домена и protocol.

## Решение

Поддерживаются две явно разные конфигурации:

- single-node: SQLite WAL + local filesystem CAS, первый private deploy,
  затем managed public beta после собственных readiness gates;
- multi-instance: PostgreSQL + S3-compatible CAS, несколько web/worker replicas.

Domain и use cases зависят от repository/blob ports. Storage-specific детали
не входят в controllers и domain entities.

## Последствия

- private registry и end-to-end protocol можно проверить раньше;
- single-node не является HA; public readiness требует release evidence,
  а не только выбора database/storage;
- миграция требует write maintenance window, export/import и digest audit;
- package/repository contracts должны иметь parity suites для обоих adapters.
