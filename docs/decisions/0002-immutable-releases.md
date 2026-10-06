# ADR-0002: неизменяемые релизы и content-addressed artifacts

Статус: accepted for architecture baseline.  
Дата: 2026-07-25.

## Контекст

Замена байтов уже опубликованной версии ломает lockfiles, кэши и
воспроизводимость, а физическое удаление уничтожает evidence.

## Решение

- `(canonical package reference, canonical version)` уникальна навсегда;
- artifact SHA-256 вычисляет сервер;
- физический storage key выводится только из SHA-256;
- повторная публикация версии возвращает conflict;
- yank меняет resolver visibility, но не байты;
- quarantine имеет приоритет над download и сохраняет аудит;
- hard delete разрешён только для orphan quarantine objects по retention
  policy, не для опубликованных релизов.

## Последствия

- CDN может кэшировать digest URL как immutable;
- exact lockfile остаётся проверяемым;
- исправление требует новой версии;
- legal/security takedown реализуется блокировкой доступа, а не
  переиспользованием версии.
