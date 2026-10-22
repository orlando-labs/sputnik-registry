# ADR-0004: scoped package references по умолчанию

Статус: принято в design v1; уточнено ADR-0005.
Дата: 2026-07-26; актуализация 2026-10-16.

## Контекст

До массового adoption глобальные короткие имена легко занять
экспериментальными или быстро заброшенными проектами. Полное освобождение
опубликованного имени опасно: новый владелец сможет подменить dependency для
старых пользователей.

## Решение

- обычный canonical package reference имеет вид
  `publisher/package_name`;
- `publisher` — стабильный namespace, а не текущий account владельца;
- account или organization может владеть namespace и делегировать роли;
- package публикуется, устанавливается и записывается в dependencies под одним
  и тем же canonical reference;
- registry reference хранится отдельно от Sputnik module names; modules scoped
  package получают обязательный `publisher.` prefix;
- unscoped reference `package_name` доступен только publisher с operator grant
  на короткие имена и после reservation конкретного имени;
- grant перечисляет allowed module prefixes, поэтому `orbit-orm` может
  предоставлять `orbit.integrations.orm`; имя repository не задаёт identity;
- scoped и unscoped components проходят одну строгую canonicalization;
- строгая network admission отделена от legacy local-name совместимости;
- namespace slug становится неизменяемым после первой публичной публикации;
- опубликованный scoped или unscoped reference не перераспределяется другому
  проекту; заброшенный проект можно архивировать, yank/quarantine и снабдить
  ссылкой на replacement.

## Последствия

- обычный abandoned package занимает имя только внутри namespace владельца;
- глобальные короткие имена остаются curated;
- transfer ownership не меняет imports, dependencies и lockfiles;
- текущий Sputnik package tooling должен научиться разбирать ровно один `/` как
  separator, а не считать весь reference одним filesystem component;
- HTTP API использует отдельные scoped routes, потому что Orbit разделяет path
  до percent-decoding;
- install использует `publisher/package_name`, а import — объявленный
  `publisher.module_name`;
- оператор может закрепить root alias `name -> publisher/name` командой
  `registry promote --ref publisher/name`. Это одно каноническое scoped identity:
  владельцы, версии, архивы, module names и dependencies не меняются. Alias
  работает для GET/HEAD; публикация и изменения используют scoped reference;
- root alias неизменяем и не переназначается. Занятые unscoped packages,
  ранее использованные publish sessions и точные unscoped grants блокируют
  promotion. Повторная команда для того же пакета идемпотентна;
- разрешение alias возвращает канонический `package_ref` и `requested_ref`.
  Установщик проверяет каноническое имя внутри архива, сохраняет scoped identity
  и фиксирует его в lockfile. Архивы и URL downloads остаются каноническими.
