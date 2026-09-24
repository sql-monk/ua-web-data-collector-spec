"""JSON Schema snapshot (`schemas/mongo/*.json`, WP-01C) → Mongo `$jsonSchema` validator.

Mongo підтримує лише підмножину draft 4 без `$ref`, `format`, `default`, `$defs` і JSON-типу
`integer`, тому snapshot перетворюється явно (рішення WP-01B, §8 «Pydantic contract → явний
BSON mapping»):

- `$ref` на `#/$defs/<Name>` інлайниться; рекурсивне посилання (напр. `JsonValue` усередині
  самого себе) стає `{}` — довільне значення на цьому рівні вкладеності;
- `type` → `bsonType` (`integer` → `int|long`, `number` → `int|long|double|decimal`,
  `boolean` → `bool`);
- рядки з `format: uuid`/`base64url` → `binData` (UUID — BSON Binary subtype 4,
  `uuidRepresentation=standard`; bytes — subtype 0), `format: date-time` → `date` (BSON date
  UTC); рядкові обмеження (`pattern`, `minLength`, `maxLength`) для них відкидаються;
- анотації (`title`, `description`, `default`, `examples`, `$schema`, `$id`,
  `x-contract-version`) відкидаються; контракт і версія стають `title` кореня;
- невідоме ключове слово або `format` → `ValueError`: validator не має мовчки втратити
  обмеження.

Validators у міграціях зберігаються **замороженими** JSON-файлами
(`migrations/mongo/<NNNN_name>/<asset>.json`), а не генеруються під час `ensure-mongo`:
застосована міграція не змінюється разом зі snapshot-ом. Тест
`tests/unit/persistence/mongo/test_validators.py` звіряє останній asset кожного контракту зі
snapshot-ом, тож зміна snapshot-а вимагає нової міграції. Регенерація asset-а:
`uv run python -m collector.persistence.mongo.validators <asset> > <каталог міграції>/<asset>.json`
(рецепти — `RECIPES`).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

JsonSchema = Mapping[str, Any]

BSON_NUMBER_TYPES: tuple[str, ...] = ("int", "long", "double", "decimal")
_TYPE_MAP: dict[str, str | list[str]] = {
    "string": "string",
    "integer": ["int", "long"],
    "number": list(BSON_NUMBER_TYPES),
    "boolean": "bool",
    "object": "object",
    "array": "array",
    "null": "null",
}
_FORMAT_MAP: dict[str, str] = {"uuid": "binData", "base64url": "binData", "date-time": "date"}
_ANNOTATIONS = frozenset(
    {"$schema", "$id", "$defs", "title", "description", "default", "examples", "x-contract-version"}
)
_STRING_ONLY = frozenset({"pattern", "minLength", "maxLength"})
_PASSTHROUGH = frozenset(
    {
        "required",
        "enum",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "pattern",
        "minItems",
        "maxItems",
        "uniqueItems",
        "minProperties",
        "maxProperties",
    }
)
_REF_PREFIX = "#/$defs/"


def mongo_validator(
    schema: JsonSchema,
    *,
    open_top_level: bool = False,
    extra_properties: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """`{"$jsonSchema": …}` для `create_collection`/`collMod` зі snapshot-а контракту.

    `open_top_level` прибирає `additionalProperties: false` лише на корені: current documents
    доменів (WP-07/WP-09) розширюють базовий контракт власними полями. `extra_properties`
    додає поля, яких немає в контракті, але які має кожен Mongo-документ (напр. `_id`).
    """
    defs: Mapping[str, JsonSchema] = schema.get("$defs", {})
    body = _convert(schema, defs, ())
    if open_top_level:
        body.pop("additionalProperties", None)
    if extra_properties:
        properties = dict(body.get("properties", {}))
        for name, spec in extra_properties.items():
            properties.setdefault(name, dict(spec))
        body["properties"] = properties
    title = schema.get("$id", "").rsplit("/", 1)[-1].removesuffix(".json")
    version = schema.get("x-contract-version")
    if title and version:
        body = {"title": f"{title.split('.v', 1)[0]}@{version}", **body}
    return {"$jsonSchema": body}


def _convert(node: JsonSchema, defs: Mapping[str, JsonSchema], stack: tuple[str, ...]) -> Any:
    ref = node.get("$ref")
    if ref is not None:
        if not isinstance(ref, str) or not ref.startswith(_REF_PREFIX):
            msg = f"непідтримуваний $ref {ref!r}: лише {_REF_PREFIX}<Name>"
            raise ValueError(msg)
        name = ref.removeprefix(_REF_PREFIX)
        if name in stack:
            return {}
        if name not in defs:
            msg = f"$ref {ref!r}: визначення відсутнє в $defs"
            raise ValueError(msg)
        merged = {**defs[name], **{k: v for k, v in node.items() if k != "$ref"}}
        return _convert(merged, defs, (*stack, name))

    out: dict[str, Any] = {}
    fmt = node.get("format")
    if fmt is not None:
        if fmt not in _FORMAT_MAP:
            msg = f"format {fmt!r} не має BSON-відповідника; додайте мапінг явно"
            raise ValueError(msg)
        if node.get("type") != "string":
            msg = f"format {fmt!r} очікується лише для type=string"
            raise ValueError(msg)
        out["bsonType"] = _FORMAT_MAP[fmt]
    for key, value in node.items():
        if key in _ANNOTATIONS or key == "format":
            continue
        if key == "type":
            if fmt is None:
                out["bsonType"] = _bson_type(value)
        elif fmt is not None and key in _STRING_ONLY:
            continue
        elif key == "properties":
            out["properties"] = {name: _convert(spec, defs, stack) for name, spec in value.items()}
        elif key in ("items", "not"):
            out[key] = _convert(value, defs, stack)
        elif key == "additionalProperties":
            out[key] = value if isinstance(value, bool) else _convert(value, defs, stack)
        elif key in ("anyOf", "oneOf", "allOf"):
            out[key] = [_convert(option, defs, stack) for option in value]
        elif key == "const":
            out["enum"] = [value]
        elif key in _PASSTHROUGH:
            out[key] = list(value) if isinstance(value, list) else value
        else:
            msg = f"ключове слово {key!r} не підтримується Mongo $jsonSchema"
            raise ValueError(msg)
    return out


def _bson_type(value: str | Sequence[str]) -> str | list[str]:
    names = [value] if isinstance(value, str) else list(value)
    result: list[str] = []
    for name in names:
        if name not in _TYPE_MAP:
            msg = f"JSON-тип {name!r} невідомий"
            raise ValueError(msg)
        mapped = _TYPE_MAP[name]
        for bson in [mapped] if isinstance(mapped, str) else mapped:
            if bson not in result:
                result.append(bson)
    return result[0] if len(result) == 1 else result


def render_validator(validator: Mapping[str, Any]) -> str:
    """Детермінований текст asset-а (UTF-8, відступ 2, `\\n` у кінці)."""
    return json.dumps(validator, ensure_ascii=False, indent=2) + "\n"


@dataclass(frozen=True, slots=True)
class ValidatorRecipe:
    """Як із snapshot-а контракту отримати validator collection-ів (asset міграції)."""

    snapshot: str
    open_top_level: bool = False
    uuid_id: bool = False
    required_properties: tuple[str, ...] = ()

    def build(self, schema: JsonSchema) -> dict[str, Any]:
        extra = {"_id": {"bsonType": "binData"}} if self.uuid_id else None
        validator = mongo_validator(
            schema, open_top_level=self.open_top_level, extra_properties=extra
        )
        body = validator["$jsonSchema"]
        properties = body.get("properties", {})
        missing = set(self.required_properties) - set(properties)
        if missing:
            msg = f"примусово required поля відсутні у snapshot: {sorted(missing)}"
            raise ValueError(msg)
        if self.required_properties:
            body["required"] = sorted({*body.get("required", []), *self.required_properties})
        return validator


RECIPES: dict[str, ValidatorRecipe] = {
    # Усі `*_current`: базовий контракт §9.2, корінь відкритий для доменних полів WP-07/WP-09.
    "current_document": ValidatorRecipe(
        "current_document_base.v1.json",
        open_top_level=True,
        # Pydantic snapshot має default=1 і тому не додає поле до JSON Schema `required`, але
        # збережений Mongo current document за §9.2 завжди мусить явно нести schema_version.
        required_properties=("schema_version",),
    ),
    # PK receipt-а — `_id = projection_task_id` (UUID), §9.2 «PK projection_task_id».
    "applied_projection_receipt": ValidatorRecipe(
        "applied_projection_receipt.v1.json", uuid_id=True
    ),
}
"""Asset `<name>.json` у каталозі міграції ↔ рецепт; ключ — ім'я asset-а."""


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m collector.persistence.mongo.validators <asset> [<schemas/mongo>]` → stdout."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in RECIPES or len(args) > 2:
        sys.stderr.write(f"usage: validators {{{','.join(RECIPES)}}} [schemas/mongo]\n")
        return 2
    recipe = RECIPES[args[0]]
    root = Path(args[1]) if len(args) == 2 else Path("schemas/mongo")
    schema = json.loads((root / recipe.snapshot).read_text(encoding="utf-8"))
    sys.stdout.write(render_validator(recipe.build(schema)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
