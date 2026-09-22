"""Оборотне entity resolution (§9.8, R-45): decision contract і чиста проєкція груп.

Source records ніколи не зливаються фізично; global group — materialized projection
послідовності `ResolutionDecision`. `project_groups()` — детермінований replay: той самий
список рішень завжди дає той самий snapshot, unmerge — це replay тієї ж історії.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from collector.contracts._base import ContractModel, JsonScalar, SchemaVersion, VersionedDocument
from collector.contracts.enums import ResolutionAction
from collector.contracts.identity import EntityId
from collector.contracts.temporal import UtcDatetime

NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]

_GROUP_ACTIONS = frozenset({ResolutionAction.MERGE, ResolutionAction.MANUAL_LINK})


class ResolutionDecision(VersionedDocument):
    """Одне рішення entity resolution (§9.8): append-only, versioned, з provenance."""

    contract_version = "1.0"
    schema_version: SchemaVersion = "1.0"

    decision_id: UUID
    decision_version: int = Field(ge=1)
    action: ResolutionAction
    member_entity_ids: list[EntityId] = Field(min_length=1)
    canonical_group_id: UUID | None = None
    evidence_refs: list[NonEmptyStr] = Field(default_factory=list)
    feature_values: dict[str, JsonScalar] = Field(
        default_factory=dict,
        description="Ознаки matcher (JSON-скаляри; гроші тут не зберігаються).",
    )
    score: Decimal | None = Field(default=None, ge=0, le=1)
    calibration_version: NonEmptyStr | None = None
    rule_or_model_version: NonEmptyStr
    actor: NonEmptyStr = Field(description="`system:<worker>` для auto, інакше operator id.")
    reason: NonEmptyStr
    effective_at: UtcDatetime
    recorded_at: UtcDatetime
    supersedes_decision_id: UUID | None = None

    @model_validator(mode="after")
    def _shape_by_action(self) -> ResolutionDecision:
        if len(set(self.member_entity_ids)) != len(self.member_entity_ids):
            msg = "member_entity_ids містить дублікати"
            raise ValueError(msg)
        if self.action in _GROUP_ACTIONS:
            if self.canonical_group_id is None:
                msg = f"{self.action.value} вимагає canonical_group_id"
                raise ValueError(msg)
            if len(self.member_entity_ids) < 2:
                msg = f"{self.action.value} вимагає щонайменше два member_entity_ids"
                raise ValueError(msg)
        if self.action is ResolutionAction.MERGE and self.score is None:
            msg = "auto merge без score заборонений (§9.3 п.6)"
            raise ValueError(msg)
        if self.action is ResolutionAction.MANUAL_BLOCK and len(self.member_entity_ids) < 2:
            msg = "manual_block вимагає щонайменше два member_entity_ids"
            raise ValueError(msg)
        if self.supersedes_decision_id == self.decision_id:
            msg = "рішення не може supersede саме себе"
            raise ValueError(msg)
        return self


class ResolutionSnapshot(ContractModel):
    """Результат `project_groups`: групи, блоки та які рішення застосовано/пропущено."""

    groups: dict[UUID, list[UUID]] = Field(
        default_factory=dict, description="canonical_group_id → відсортовані member ids."
    )
    blocked_pairs: list[tuple[UUID, UUID]] = Field(default_factory=list)
    applied_decision_ids: list[UUID] = Field(default_factory=list)
    skipped_decision_ids: list[UUID] = Field(default_factory=list)
    superseded_decision_ids: list[UUID] = Field(default_factory=list)

    def group_of(self, entity_id: UUID) -> UUID | None:
        """Група сутності або `None`, якщо вона не в жодній групі."""
        for group_id, members in self.groups.items():
            if entity_id in members:
                return group_id
        return None


def _pairs(ids: Iterable[UUID]) -> set[tuple[UUID, UUID]]:
    ordered = sorted(set(ids), key=lambda value: value.int)
    return {(a, b) for index, a in enumerate(ordered) for b in ordered[index + 1 :]}


def _required_group_id(decision: ResolutionDecision) -> UUID:
    if decision.canonical_group_id is None:
        msg = f"{decision.action.value} без canonical_group_id"
        raise ValueError(msg)
    return decision.canonical_group_id


def project_groups(decisions: Sequence[ResolutionDecision]) -> ResolutionSnapshot:
    """Детермінований replay рішень → групи (§9.8, §10 п.15).

    Порядок replay: `(effective_at, recorded_at, decision_version, decision_id)`. Рішення,
    на яке посилається чиєсь `supersedes_decision_id`, пропускається цілком (разом із його
    ефектом), тому скасування manual_block — це нове рішення, що supersede старе.

    - `merge`/`manual_link`: усі members і члени їхніх поточних груп переходять у
      `canonical_group_id`; auto `merge` пропускається (`skipped_decision_ids`), якщо
      будь-яка пара результівної групи заблокована; операторський `manual_link` блоку не
      підлягає;
    - `manual_block`: усі пари members блокуються для наступних auto merge;
    - `unmerge`: members виходять зі своїх груп; група з < 2 членів зникає;
    - `reject`: не змінює групи (candidate відхилений).
    """
    superseded = {d.supersedes_decision_id for d in decisions if d.supersedes_decision_id}
    ordered = sorted(
        decisions,
        key=lambda d: (d.effective_at, d.recorded_at, d.decision_version, d.decision_id.int),
    )
    membership: dict[UUID, UUID] = {}
    blocked: set[tuple[UUID, UUID]] = set()
    applied: list[UUID] = []
    skipped: list[UUID] = []

    for decision in ordered:
        if decision.decision_id in superseded:
            continue
        members = set(decision.member_entity_ids)
        if decision.action in _GROUP_ACTIONS:
            group_id = _required_group_id(decision)
            affected = set(members)
            for entity, current_group in membership.items():
                if any(membership.get(m) == current_group for m in members):
                    affected.add(entity)
            if decision.action is ResolutionAction.MERGE and _pairs(affected) & blocked:
                skipped.append(decision.decision_id)
                continue
            for entity in affected:
                membership[entity] = group_id
        elif decision.action is ResolutionAction.MANUAL_BLOCK:
            blocked |= _pairs(members)
        elif decision.action is ResolutionAction.UNMERGE:
            for entity in members:
                membership.pop(entity, None)
        applied.append(decision.decision_id)

    groups: dict[UUID, list[UUID]] = {}
    for entity, group_id in membership.items():
        groups.setdefault(group_id, []).append(entity)
    return ResolutionSnapshot(
        groups={
            gid: sorted(members, key=lambda value: value.int)
            for gid, members in sorted(groups.items(), key=lambda item: item[0].int)
            if len(members) >= 2
        },
        blocked_pairs=sorted(blocked, key=lambda pair: (pair[0].int, pair[1].int)),
        applied_decision_ids=applied,
        skipped_decision_ids=skipped,
        superseded_decision_ids=sorted(superseded, key=lambda value: value.int),
    )
