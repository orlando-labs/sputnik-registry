# ADR-0004: scoped package references по умолчанию

Статус: accepted for architecture baseline.  
Дата: 2026-07-26.

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
- scoped и unscoped components проходят одну строгую canonicalization;
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
- HTTP API использует отдельные scoped routes, потому что Ember разделяет path
  до percent-decoding;
- install использует `publisher/package_name`, а import — объявленный
  `publisher.module_name`;
- автоматическое promotion `publisher/name -> name` не входит в v1: это две
  разные canonical identities, если оператор явно не введёт alias contract в
  будущей версии протокола.
