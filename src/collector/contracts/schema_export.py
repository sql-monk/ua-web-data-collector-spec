"""Експорт JSON Schema snapshots і перевірка сумісності (§9.4).

`EXPORTED_CONTRACTS` — реєстр публічних моделей за групами `schemas/<group>/<name>.v<major>.json`.
`render_schema()` — детермінований текст (sorted keys, 2 пробіли, LF). `export_schemas()`
пише файли, `check_schemas()` повертає drift; `check_compatibility()` — breaking-зміни між
двома snapshot-ами (видалене/перейменоване поле, нове required, зміна типу) без підвищення
major. Модуль не має побічних ефектів при імпорті; I/O — лише у функціях export/check.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from collector.contracts._base import ContractModel, parse_schema_version
from collector.contracts.artifacts import (
    ArtifactRef,
    NormalizedArtifactRef,
    RawArtifactRef,
    UploadClaim,
)
from collector.contracts.current import CurrentDocumentBase, Lineage, SourceRef
from collector.contracts.events import DomainChangedEvent, EncodedEvent
from collector.contracts.identity import NormalizedUrl, SourceIdentity
from collector.contracts.projection import (
    AppliedProjectionReceipt,
    ProjectionAcknowledgement,
    ProjectionCommand,
)
from collector.contracts.release import (
    ComponentVersions,
    EntityVersionRef,
    ReleaseManifest,
    ReleasePart,
    ReleaseWatermark,
    SourceInclusion,
)
from collector.contracts.resolution import ResolutionDecision, ResolutionSnapshot
from collector.contracts.temporal import (
    BitemporalInterval,
    EffectiveTime,
    EntityTime,
    SourceTime,
    SystemTime,
    VersionInterval,
    VersionTimes,
)
from collector.contracts.values import ContactValue, MeasuredValue, Money

SCHEMA_ID_PREFIX = "https://ua-collector.local/schemas"


@dataclass(frozen=True, slots=True)
class ExportedContract:
    """Одна публічна модель у snapshot-реєстрі."""

    group: str
    name: str
    model: type[ContractModel]

    @property
    def relative_path(self) -> Path:
        """`<group>/<name>.v<major>.json`."""
        major, _ = parse_schema_version(self.model.contract_version)
        return Path(self.group) / f"{self.name}.v{major}.json"


EXPORTED_CONTRACTS: tuple[ExportedContract, ...] = (
    # common — value objects і shared блоки, які вкладаються в документи/події
    ExportedContract("common", "source_identity", SourceIdentity),
    ExportedContract("common", "normalized_url", NormalizedUrl),
    ExportedContract("common", "money", Money),
    ExportedContract("common", "contact_value", ContactValue),
    ExportedContract("common", "measured_value", MeasuredValue),
    ExportedContract("common", "source_time", SourceTime),
    ExportedContract("common", "system_time", SystemTime),
    ExportedContract("common", "entity_time", EntityTime),
    ExportedContract("common", "effective_time", EffectiveTime),
    ExportedContract("common", "bitemporal_interval", BitemporalInterval),
    ExportedContract("common", "version_times", VersionTimes),
    ExportedContract("common", "version_interval", VersionInterval),
    ExportedContract("common", "artifact_ref", ArtifactRef),
    ExportedContract("common", "raw_artifact_ref", RawArtifactRef),
    ExportedContract("common", "normalized_artifact_ref", NormalizedArtifactRef),
    ExportedContract("common", "upload_claim", UploadClaim),
    ExportedContract("common", "lineage", Lineage),
    ExportedContract("common", "source_ref", SourceRef),
    # events — повідомлення між компонентами (§7.3)
    ExportedContract("events", "projection_command", ProjectionCommand),
    ExportedContract("events", "projection_acknowledgement", ProjectionAcknowledgement),
    ExportedContract("events", "domain_changed_event", DomainChangedEvent),
    ExportedContract("events", "encoded_event", EncodedEvent),
    # mongo — документи MongoDB (§9.2); WP-01B генерує з них $jsonSchema validators
    ExportedContract("mongo", "current_document_base", CurrentDocumentBase),
    ExportedContract("mongo", "applied_projection_receipt", AppliedProjectionReceipt),
    # releases — dataset release і resolution snapshot (§9.8, §9.9)
    ExportedContract("releases", "release_manifest", ReleaseManifest),
    ExportedContract("releases", "release_part", ReleasePart),
    ExportedContract("releases", "release_watermark", ReleaseWatermark),
    ExportedContract("releases", "entity_version_ref", EntityVersionRef),
    ExportedContract("releases", "source_inclusion", SourceInclusion),
    ExportedContract("releases", "component_versions", ComponentVersions),
    ExportedContract("releases", "resolution_decision", ResolutionDecision),
    ExportedContract("releases", "resolution_snapshot", ResolutionSnapshot),
)

SCHEMA_GROUPS: tuple[str, ...] = tuple(dict.fromkeys(c.group for c in EXPORTED_CONTRACTS))


def _sort_required(node: Any) -> None:
    """`required` — відсортований список: перестановка полів у моделі не є drift."""
    if isinstance(node, dict):
        required = node.get("required")
        if isinstance(required, list):
            node["required"] = sorted(required)
        for child in node.values():
            _sort_required(child)
    elif isinstance(node, list):
        for child in node:
            _sort_required(child)


def build_schema(contract: ExportedContract) -> dict[str, Any]:
    """JSON Schema моделі з `$id`, `x-contract-version` і стабільним `title`."""
    schema = contract.model.model_json_schema(mode="validation")
    _sort_required(schema)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"{SCHEMA_ID_PREFIX}/{contract.relative_path.as_posix()}"
    schema["x-contract-version"] = contract.model.contract_version
    return schema


def render_schema(schema: Mapping[str, Any]) -> str:
    """Детермінований текст snapshot: sorted keys, indent 2, LF, завершальний newline."""
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def iter_rendered(
    contracts: tuple[ExportedContract, ...] = EXPORTED_CONTRACTS,
) -> Iterator[tuple[Path, str]]:
    """`(відносний шлях, текст)` для кожної публічної моделі."""
    for contract in contracts:
        yield contract.relative_path, render_schema(build_schema(contract))


def export_schemas(root: Path) -> list[Path]:
    """Записати всі snapshot-и у `root/<group>/<name>.v<major>.json`; повертає записані шляхи."""
    written: list[Path] = []
    for relative, text in iter_rendered():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
        written.append(target)
    return written


def check_schemas(root: Path) -> list[str]:
    """Drift між згенерованими схемами і файлами у `root`; порожній список = без drift."""
    problems: list[str] = []
    expected: dict[Path, str] = dict(iter_rendered())
    for relative, text in expected.items():
        target = root / relative
        if not target.is_file():
            problems.append(f"missing: {relative.as_posix()}")
            continue
        if target.read_text(encoding="utf-8").replace("\r\n", "\n") != text:
            problems.append(f"drift: {relative.as_posix()}")
    for group in SCHEMA_GROUPS:
        group_dir = root / group
        if not group_dir.is_dir():
            continue
        for existing in sorted(group_dir.glob("*.json")):
            relative = existing.relative_to(root)
            if relative not in expected:
                problems.append(f"stale: {relative.as_posix()}")
    return problems


def _definitions(schema: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {"": schema}
    for name, definition in schema.get("$defs", {}).items():
        result[name] = definition
    return result


_NON_STRUCTURAL_KEYS = frozenset({"description", "title", "default"})


def _type_signature(prop: Mapping[str, Any]) -> str:
    structural = {k: v for k, v in prop.items() if k not in _NON_STRUCTURAL_KEYS}
    return json.dumps(structural, sort_keys=True)


def check_compatibility(old: Mapping[str, Any], new: Mapping[str, Any]) -> list[str]:
    """Breaking-зміни `old → new` для того самого major (§9.4); порожній список = сумісно.

    Breaking: видалене/перейменоване property, нове required property, зміна типу/обмежень
    наявного property, видалене значення enum, видалене визначення у `$defs`.
    Додавання optional property або нового enum-значення — сумісне (minor).
    """
    problems: list[str] = []
    old_defs = _definitions(old)
    new_defs = _definitions(new)
    for name, old_def in old_defs.items():
        label = name or "<root>"
        new_def = new_defs.get(name)
        if new_def is None:
            problems.append(f"{label}: визначення видалено")
            continue
        if "enum" in old_def:
            new_enum = {str(v) for v in new_def.get("enum", [])}
            removed = sorted({str(v) for v in old_def["enum"]} - new_enum)
            if removed:
                problems.append(f"{label}: видалені enum-значення {removed}")
        old_props: Mapping[str, Any] = old_def.get("properties", {})
        new_props: Mapping[str, Any] = new_def.get("properties", {})
        for prop, old_spec in old_props.items():
            if prop not in new_props:
                problems.append(f"{label}.{prop}: property видалено або перейменовано")
            elif _type_signature(old_spec) != _type_signature(new_props[prop]):
                problems.append(f"{label}.{prop}: змінено тип/обмеження")
        added_required = sorted(set(new_def.get("required", [])) - set(old_def.get("required", [])))
        if added_required:
            problems.append(f"{label}: нові required поля {added_required}")
    return problems
