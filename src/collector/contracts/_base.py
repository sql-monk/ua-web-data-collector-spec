"""Базові класи shared-контрактів: frozen Pydantic-моделі з версією контракту (§9.4).

Кожен контракт — чиста модель без I/O. `ContractModel` фіксує спільну конфігурацію
(immutable, `extra="forbid"`, base64 для bytes у JSON) і `contract_version` класу
(`major.minor`). `VersionedDocument` додає поле `schema_version` для документів і
повідомлень, що зберігаються або передаються між компонентами: документ попередньої
minor-версії того самого major валідується новою моделлю (§9.4), інший major відхиляється.
"""

from __future__ import annotations

from typing import Annotated, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

SCHEMA_VERSION_PATTERN = r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"

SchemaVersion = Annotated[str, StringConstraints(pattern=SCHEMA_VERSION_PATTERN)]
"""Версія контракту `major.minor` (§9.4): minor — сумісне додавання, major — breaking."""

JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list[Any] | dict[str, Any]
JsonObject = dict[str, Any]
"""Bounded JSON-об'єкт (`core`, `attributes`, `latest_state`, event payload)."""


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
