"""Read-only loader `docs/research/source-registry.yaml` для валідації `source_id`.

Реєстр — єдине джерело допустимих `source_id` (§9.3 п.1). Loader кешований і не змінює
файл. Шлях: env `COLLECTOR_SOURCE_REGISTRY`, інакше пошук `docs/research/source-registry.yaml`
угору від пакета і від поточного каталогу (repo checkout або Docker image з копією `docs/`).
`yaml` імпортується лише під час завантаження — імпорт `collector.contracts` лишається легким.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import ConfigDict, Field, StringConstraints, field_validator

from collector.contracts._base import ContractModel
from collector.contracts.enums import DataDomain

SOURCE_REGISTRY_ENV = "COLLECTOR_SOURCE_REGISTRY"
SOURCE_REGISTRY_RELATIVE_PATH = Path("docs") / "research" / "source-registry.yaml"
SOURCE_ID_PATTERN = r"^(news|vehicle|catalog)_[a-z]{2}_[a-z0-9]+(_[a-z0-9]+)*$"

SourceIdString = Annotated[str, StringConstraints(pattern=SOURCE_ID_PATTERN, max_length=64)]
"""Синтаксис `source_id`: `<kind>_<country>_<slug>`; семантика — наявність у реєстрі."""


class SourceRegistryError(RuntimeError):
    """Реєстр джерел не знайдено або він невалідний."""


class SourceRegistryEntry(ContractModel):
    """Один запис реєстру (`docs/research/source-registry.yaml`); зайві поля ігноруються."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    id: SourceIdString
    kind: DataDomain
    country: Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]
    name: str = Field(min_length=1)
    domains: list[str] = Field(min_length=1)
    rating: int = Field(ge=0, le=100)
    research: str = Field(min_length=1)


class SourceRegistry(ContractModel):
    """Реєстр джерел: версія, дата оцінки та список записів без дублікатів `id`.

    Дослідницькі поля (`rating_scale`, `rating_components`, ...) ігноруються — контракт
    читає лише те, що потрібно для валідації `source_id`.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    schema_version: int
    assessed_at: str
    sources: list[SourceRegistryEntry] = Field(min_length=1)

    @field_validator("sources")
    @classmethod
    def _unique_ids(cls, value: list[SourceRegistryEntry]) -> list[SourceRegistryEntry]:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for entry in value:
            (duplicates if entry.id in seen else seen).add(entry.id)
        if duplicates:
            msg = f"дублікати source_id у реєстрі: {sorted(duplicates)}"
            raise ValueError(msg)
        for entry in value:
            if not entry.id.startswith(f"{entry.kind.value}_"):
                msg = f"source_id {entry.id!r} не має префікса kind {entry.kind.value!r}"
                raise ValueError(msg)
        return value

    @property
    def ids(self) -> frozenset[str]:
        """Множина допустимих `source_id`."""
        return frozenset(entry.id for entry in self.sources)


def _candidate_roots() -> Iterable[Path]:
    package_root = Path(__file__).resolve()
    yield from package_root.parents
    yield from (Path.cwd().resolve(), *Path.cwd().resolve().parents)


def find_source_registry_path(environ: Mapping[str, str] | None = None) -> Path:
    """Шлях до реєстру: env `COLLECTOR_SOURCE_REGISTRY` або пошук угору від пакета/cwd."""
    env: Mapping[str, str] = os.environ if environ is None else environ
    configured = env.get(SOURCE_REGISTRY_ENV, "").strip()
    if configured:
        path = Path(configured)
        if not path.is_file():
            msg = f"{SOURCE_REGISTRY_ENV}={configured!r} не вказує на файл"
            raise SourceRegistryError(msg)
        return path
    for root in _candidate_roots():
        candidate = root / SOURCE_REGISTRY_RELATIVE_PATH
        if candidate.is_file():
            return candidate
    msg = f"не знайдено {SOURCE_REGISTRY_RELATIVE_PATH.as_posix()}; задайте {SOURCE_REGISTRY_ENV}"
    raise SourceRegistryError(msg)


@lru_cache(maxsize=4)
def load_source_registry(path: Path | None = None) -> SourceRegistry:
    """Завантажити та провалідувати реєстр (кешовано за шляхом; read-only)."""
    import yaml

    registry_path = find_source_registry_path() if path is None else path
    with registry_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, dict):
        msg = f"{registry_path}: очікувався mapping на верхньому рівні"
        raise SourceRegistryError(msg)
    return SourceRegistry.model_validate(raw)


def known_source_ids(path: Path | None = None) -> frozenset[str]:
    """Допустимі `source_id` з реєстру (кешовано)."""
    return load_source_registry(path).ids


def require_known_source_id(source_id: str) -> str:
    """Validator-helper: `source_id` має існувати в реєстрі."""
    if source_id not in known_source_ids():
        registry = SOURCE_REGISTRY_RELATIVE_PATH.as_posix()
        msg = f"невідомий source_id {source_id!r}: немає в {registry}"
        raise ValueError(msg)
    return source_id
