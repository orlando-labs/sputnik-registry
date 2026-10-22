# ADR-0008: независимые native requirements и optional bundles

Дата: 2026-10-19, актуализация 2026-10-20. Статус: принято в v1;
local builder/provider bindings реализованы, managed integration запланирована.
Уточняет ADR-0007. Нового номера package/manifest/lock нет.

## Причина

Эксперимент sputniktorch дал шесть архивов ~90–92 MiB: SDK/runtime оказались
неявной частью source variants. Самодостаточный runtime иногда полезен,
но source representation не должна определять его поставку.

## Решение

- Независимые axes: Sputnik source/bytecode, native source/prebuilt и native
  dependency delivery. Default bundle `none`; explicit `runtime` и `sdk`.
- Source+runtime bundle допустим; build SDK должен соответствовать выбранному
  runtime. Prebuilt без bundle потребляет отдельный bound runtime provider.
- Bundle собирает необходимое closure, а не весь установленный Torch prefix.
  SDK добавляется отдельно; compiler/OS SDK/Python не входят автоматически.
- Ввести typed native requirements/providers и runtime/build-only package edges.
  Runtime/SDK native-only packages не получают фиктивных Sputnik modules.
- Delivery не создаёт отдельный ABI namespace. Один singleton provider на
  process, exact binding/fingerprints, общий prepared view для VM/native.
- Лицензии/provenance учитываются по реальному component inventory всего
  payload. Packer проверяет metadata/materials, а не выдаёт legal clearance.
- Bundle profile участвует в selection/lock. Никакого неявного перехода от
  slim к большому bundle при missing dependency или incompatible provider.

## Последствия

Поля prototype можно изменить breaking в v1. Понадобятся core/provider/store/
loader changes до корректного создания slim/source+runtime/SDK variants.
Удаление `lib/vendor` из сегодняшнего archive само по себе эту работу не заменяет.
Существующие шесть development bundles остаются evidence эксперимента; design
не выдаётся за уже работающие flags. Registry service не реализуется.

Дополнение от 2026-10-20: новые `--bundle`/`--matrix` flags и local typed provider
preparation доступны. Полная текущая matrix содержит 15 consumer artifacts и
два native-only providers; прежние шесть bundles сохранены отдельно. Managed
dependency resolution, registry selection и portable application lock требуют
следующей интеграции.

Нормативные details и этапы: [NATIVE_BUNDLES](../NATIVE_BUNDLES.md).
