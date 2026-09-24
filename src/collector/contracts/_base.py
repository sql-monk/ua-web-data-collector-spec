"""Базові класи shared-контрактів: frozen Pydantic-моделі з версією контракту (§9.4).

Кожен контракт — чиста модель без I/O. `ContractModel` фіксує спільну конфігурацію
(immutable, `extra="forbid"`, base64 для bytes у JSON) і `contract_version` класу
(`major.minor`). `VersionedDocument` додає поле `schema_version` для документів і
повідомлень, що зберігаються або передаються між компонентами: документ попередньої
minor-версії того самого major валідується новою моделлю (§9.4), інший major відхиляється.
"""

from __future__ import annotations

from typing import Annotated, Any, ClassVar, Final

from pydantic import (
    AfterValidator,
    AllowInfNan,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    Strict,
    StringConstraints,
    model_validator,
)

CONTRACTS_VERSION = "1.0"
"""Версія набору shared-контрактів (для `collector version` і release manifest)."""

SCHEMA_VERSION_PATTERN = r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"

SchemaVersion = Annotated[str, StringConstraints(pattern=SCHEMA_VERSION_PATTERN)]
"""Версія контракту `major.minor` (§9.4): minor — сумісне додавання, major — breaking."""

NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]


def _require_real_float(value: object) -> object:
    # Strict float у Pydantic приймає int і Decimal; для JSON-скаляра потрібен саме float.
    if not isinstance(value, float):
        msg = f"очікувався float, отримано {type(value).__name__}"
        raise ValueError(msg)
    return value


StrictFiniteFloat = Annotated[float, BeforeValidator(_require_real_float), AllowInfNan(False)]
JsonScalar = (
    Annotated[str, Strict()]
    | Annotated[int, Strict()]
    | StrictFiniteFloat
    | Annotated[bool, Strict()]
    | None
)
"""Strict JSON-скаляр: `datetime`/`Decimal`/`UUID`/`bytes` і NaN/inf відхиляються (CR-01)."""

JsonKey = Annotated[str, Strict()]
"""Ключ JSON-об'єкта — лише `str`; `bytes`-ключ не приводиться мовчки (колізія `b"a"`/`"a"`)."""

type JsonValue = JsonScalar | Annotated[list[JsonValue], Strict()] | dict[JsonKey, JsonValue]
"""Рекурсивне strict-JSON значення — те, що без втрат переживає JSON/BSON round-trip.

Масив — лише `list` (`Strict()`): `set`/`frozenset`/`tuple` відхиляються, а не мовчки
приводяться до списку — порядок `set` залежить від `PYTHONHASHSEED`, і `state_hash`/event
bytes різнилися б між процесами (gate 2 M-1). Порядок елементів — відповідальність викликача.
"""

JsonObject = dict[JsonKey, JsonValue]
"""Bounded strict-JSON об'єкт (`core`, `attributes`, `latest_state`, event payload)."""

MAX_JSON_DEPTH: Final = 8
"""Максимальна вкладеність `BoundedJsonObject` (корінь — рівень 1)."""
MAX_JSON_ARRAY_ITEMS: Final = 256
"""Максимум елементів одного масиву в `BoundedJsonObject` (§9.2: без unbounded arrays)."""
MAX_JSON_OBJECT_KEYS: Final = 512
"""Максимум ключів одного об'єкта в `BoundedJsonObject`."""
MAX_JSON_STRING_CHARS: Final = 65_536
"""Максимальна довжина рядка-значення в `BoundedJsonObject` (довші тексти — artifact)."""
MAX_JSON_KEY_CHARS: Final = 256
"""Максимальна довжина ключа об'єкта в `BoundedJsonObject`."""
MAX_JSON_BLOCK_BYTES: Final = 1024 * 1024
"""Максимум canonical UTF-8 bytes одного `BoundedJsonObject` (1 MiB).

Три блоки current document (`core`/`attributes`/`latest_state`) разом ≤ 3 MiB — з запасом під
ліміт документа Mongo 16 MiB навіть із version snapshot. Межа свідомо більша за inline-ліміт
події 256 KiB (`EVENT_INLINE_LIMIT_BYTES`): великий `domain.changed` payload іде через
`payload_artifact` (`EventTooLargeError`), а не відхиляється на контракті.
"""
JSON_INT_MIN: Final = -(2**63)
JSON_INT_MAX: Final = 2**63 - 1
"""Діапазон цілих у `BoundedJsonObject` — BSON int64 (більші PyMongo не зберігає)."""


def _check_bounded(value: JsonValue, depth: int, path: str) -> None:
    if depth > MAX_JSON_DEPTH:
        msg = f"{path}: вкладеність > {MAX_JSON_DEPTH} (§9.2 bounded snapshot)"
        raise ValueError(msg)
    if isinstance(value, dict):
        if len(value) > MAX_JSON_OBJECT_KEYS:
            msg = f"{path}: {len(value)} ключів > {MAX_JSON_OBJECT_KEYS} (§9.2 bounded snapshot)"
            raise ValueError(msg)
        for key, item in value.items():
            if len(key) > MAX_JSON_KEY_CHARS:
                msg = f"{path}: ключ довжиною {len(key)} > {MAX_JSON_KEY_CHARS}"
                raise ValueError(msg)
            _check_bounded(item, depth + 1, f"{path}.{key}")
    elif isinstance(value, list):
        if len(value) > MAX_JSON_ARRAY_ITEMS:
            msg = (
                f"{path}: масив {len(value)} елементів > {MAX_JSON_ARRAY_ITEMS}; unbounded "
                "списки (offers, observations, reviews) — окремі collections (§9.2)"
            )
            raise ValueError(msg)
        for index, item in enumerate(value):
            _check_bounded(item, depth + 1, f"{path}[{index}]")
    elif isinstance(value, str):
        if len(value) > MAX_JSON_STRING_CHARS:
            msg = f"{path}: рядок {len(value)} символів > {MAX_JSON_STRING_CHARS}; текст — artifact"
            raise ValueError(msg)
    elif isinstance(value, int) and not isinstance(value, bool):
        if not JSON_INT_MIN <= value <= JSON_INT_MAX:
            msg = f"{path}: ціле поза int64 (BSON не зберігає)"
            raise ValueError(msg)


def require_bounded_json(value: JsonObject) -> JsonObject:
    """Validator `BoundedJsonObject` (§9.2): структура, рядки, int64 і розмір canonical bytes.

    Canonical bytes рахуються тут, тож lone surrogate (U+D800..U+DFFF) відхиляється на валідації,
    а не падає пізніше в `canonical_json_bytes`/`encode_event` (gate 2 L-1).
    """
    from collector.contracts.canonical import CanonicalEncodingError, canonical_json_bytes

    _check_bounded(value, 1, "$")
    try:
        size = len(canonical_json_bytes(value))
    except CanonicalEncodingError as exc:
        msg = f"значення не має canonical-представлення: {exc}"
        raise ValueError(msg) from None
    if size > MAX_JSON_BLOCK_BYTES:
        msg = f"canonical bytes {size} > {MAX_JSON_BLOCK_BYTES}; великий вміст — artifact"
        raise ValueError(msg)
    return value


BoundedJsonObject = Annotated[JsonObject, AfterValidator(require_bounded_json)]
"""`JsonObject` з межами `MAX_JSON_*` і діапазоном int64 (§9.2).

Застосовується до нових контрактів PR2 (normalized payload, version snapshot, observations);
межі не змінюють JSON Schema (перевірка — лише в моделі).
"""


def parse_schema_version(value: str) -> tuple[int, int]:
    """`"1.2"` → `(1, 2)`; кидає `ValueError` для невалідного формату."""
    major, sep, minor = value.partition(".")
    if not sep or not major.isdigit() or not minor.isdigit():
        msg = f"schema_version має формат major.minor, отримано {value!r}"
        raise ValueError(msg)
    return int(major), int(minor)


class ContractModel(BaseModel):
    """Спільна база всіх контрактів: immutable, без невідомих полів, детермінований JSON."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        populate_by_name=True,
        ser_json_bytes="base64",
        val_json_bytes="base64",
        validate_default=True,
    )

    contract_version: ClassVar[str] = "1.0"
    """Версія контракту класу (`major.minor`); потрапляє в JSON Schema як `x-contract-version`."""


class VersionedDocument(ContractModel):
    """Контракт документа/повідомлення з явним полем `schema_version` (§9.4).

    Значення за замовчуванням має збігатися з `contract_version` класу (перевіряється при
    визначенні підкласу). Вхідний документ приймається, якщо його major збігається з major
    моделі, а minor не перевищує minor моделі.
    """

    schema_version: SchemaVersion = Field(
        default="1.0",
        description="Версія контракту цього документа (`major.minor`, §9.4).",
    )

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        default = cls.model_fields["schema_version"].default
        if default != cls.contract_version:
            msg = (
                f"{cls.__name__}: default schema_version={default!r} "
                f"не збігається з contract_version={cls.contract_version!r}"
            )
            raise TypeError(msg)

    @model_validator(mode="after")
    def _check_schema_version_compatible(self) -> VersionedDocument:
        major, minor = parse_schema_version(self.schema_version)
        own_major, own_minor = parse_schema_version(type(self).contract_version)
        if major != own_major or minor > own_minor:
            msg = (
                f"schema_version {self.schema_version} несумісна з контрактом "
                f"{type(self).__name__} {type(self).contract_version}: потрібен major "
                f"{own_major} і minor <= {own_minor}"
            )
            raise ValueError(msg)
        return self
