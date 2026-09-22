"""Оборотне entity resolution (§9.8, R-45): decision contract і чиста проєкція груп.

Source records ніколи не зливаються фізично; global group — materialized projection
послідовності `ResolutionDecision`. `project_groups()` — детермінований replay: той самий
список рішень завжди дає той самий snapshot, unmerge — це replay тієї ж історії.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import Decimal
from uuid import UUID

from pydantic import Field, model_validator

from collector.contracts._base import (
    ContractModel,
    JsonScalar,
    NonEmptyStr,
    SchemaVersion,
    VersionedDocument,
)
from collector.contracts.enums import ResolutionAction
from collector.contracts.identity import EntityId
from collector.contracts.temporal import UtcDatetime

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


def _effective_decisions(
    decisions: Sequence[ResolutionDecision],
) -> tuple[list[ResolutionDecision], set[UUID]]:
    """Рішення, що беруть участь у replay, і множина superseded id (semantics — docstring)."""
    latest: dict[UUID, ResolutionDecision] = {}
    for decision in decisions:
        current = latest.get(decision.decision_id)
        if current is None or decision.decision_version > current.decision_version:
            latest[decision.decision_id] = decision
    candidates = list(latest.values())
    superseded: set[UUID] = set()
    for _ in range(len(candidates) + 1):
        effective = [d for d in candidates if d.decision_id not in superseded]
        next_superseded = {
            d.supersedes_decision_id for d in effective if d.supersedes_decision_id is not None
        }
        if next_superseded == superseded:
            return effective, superseded
        superseded = next_superseded
    msg = "supersedes_decision_id утворює цикл — replay неможливий"
    raise ValueError(msg)


def project_groups(decisions: Sequence[ResolutionDecision]) -> ResolutionSnapshot:
    """Детермінований replay рішень → групи (§9.8, §10 п.15).

    Семантика версій і supersession (CR-09, `docs/contracts.md` §10):

    - `decision_version`: з кількох версій одного `decision_id` у replay бере участь лише
      найвища (нова версія *замінює* попередню, старі не replay-яться);
    - `supersedes_decision_id` не транзитивний «на прохід»: supersession діє лише від
      *ефективного* (не superseded) рішення. Ланцюжок A ← B ← C: C скасовує B, тому B більше
      не скасовує A — A відновлюється. Обчислюється як fixed point; цикл — `ValueError`.
      Dangling посилання (невідомий id) допускається і потрапляє у `superseded_decision_ids`.

    Порядок replay: `(effective_at, recorded_at, decision_version, decision_id)`.

    - `merge`/`manual_link`: усі members і члени їхніх поточних груп переходять у
      `canonical_group_id`; auto `merge` пропускається (`skipped_decision_ids`), якщо
      будь-яка пара результівної групи заблокована; операторський `manual_link` блоку не
      підлягає;
    - `manual_block`: усі пари members блокуються для наступних auto merge;
    - `unmerge`: members виходять зі своїх груп; група з < 2 членів зникає;
    - `reject`: не змінює групи (candidate відхилений).

    Складність: O(D + Σ|affected|) завдяки зворотному індексу `groups` (CR-02); перевірка
    блоку — O(|blocked|) на merge.
    """
    effective, superseded = _effective_decisions(decisions)
    ordered = sorted(
        effective,
        key=lambda d: (d.effective_at, d.recorded_at, d.decision_version, d.decision_id.int),
    )
    membership: dict[UUID, UUID] = {}
    groups: dict[UUID, set[UUID]] = {}
    blocked: set[tuple[UUID, UUID]] = set()
    applied: list[UUID] = []
    skipped: list[UUID] = []

    def leave(entity: UUID) -> None:
        old = membership.pop(entity, None)
        if old is not None:
            groups[old].discard(entity)
            if not groups[old]:
                del groups[old]

    for decision in ordered:
        members = set(decision.member_entity_ids)
        if decision.action in _GROUP_ACTIONS:
            group_id = _required_group_id(decision)
            affected = set(members)
            for member in members:
                current = membership.get(member)
                if current is not None:
                    affected |= groups[current]
            if decision.action is ResolutionAction.MERGE and any(
                a in affected and b in affected for a, b in blocked
            ):
                skipped.append(decision.decision_id)
                continue
            for entity in affected:
                leave(entity)
                membership[entity] = group_id
            groups.setdefault(group_id, set()).update(affected)
        elif decision.action is ResolutionAction.MANUAL_BLOCK:
            blocked |= _pairs(members)
        elif decision.action is ResolutionAction.UNMERGE:
            for entity in members:
                leave(entity)
        applied.append(decision.decision_id)

    return ResolutionSnapshot(
        groups={
            gid: sorted(entities, key=lambda value: value.int)
            for gid, entities in sorted(groups.items(), key=lambda item: item[0].int)
            if len(entities) >= 2
        },
        blocked_pairs=sorted(blocked, key=lambda pair: (pair[0].int, pair[1].int)),
        applied_decision_ids=applied,
        skipped_decision_ids=skipped,
        superseded_decision_ids=sorted(superseded, key=lambda value: value.int),
    )
