"""§9.8 / R-45 (рівень 10 на рівні контракту): merge → manual_block → unmerge → replay."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import pytest
from factories import ENTITY_A, ENTITY_B, ENTITY_C, ENTITY_D, GROUP_1, GROUP_2, at
from pydantic import ValidationError

from collector.contracts.enums import ResolutionAction
from collector.contracts.resolution import ResolutionDecision, project_groups


def decision(
    number: int,
    action: ResolutionAction,
    members: list[UUID],
    *,
    group: UUID | None = None,
    supersedes: int | None = None,
    actor: str = "system:matcher",
) -> ResolutionDecision:
    return ResolutionDecision(
        decision_id=UUID(int=number),
        decision_version=1,
        action=action,
        member_entity_ids=members,
        canonical_group_id=group,
        evidence_refs=["s3://evidence/" + str(number)],
        feature_values={"vin_match": True, "score_raw": 0.97},
        score=Decimal("0.95") if action is ResolutionAction.MERGE else None,
        calibration_version="cal-1",
        rule_or_model_version="matcher/1.0",
        actor=actor,
        reason="test",
        effective_at=at(number),
        recorded_at=at(number),
        supersedes_decision_id=None if supersedes is None else UUID(int=supersedes),
    )


def test_merge_manual_block_unmerge_replay() -> None:
    history = [
        decision(1, ResolutionAction.MERGE, [ENTITY_A, ENTITY_B], group=GROUP_1),
    ]
    after_merge = project_groups(history)
    assert after_merge.groups == {GROUP_1: sorted([ENTITY_A, ENTITY_B], key=lambda u: u.int)}

    history.append(
        decision(2, ResolutionAction.MANUAL_BLOCK, [ENTITY_A, ENTITY_B], actor="operator:olena")
    )
    after_block = project_groups(history)
    assert after_block.groups == after_merge.groups  # блок не розриває групу сам по собі
    assert after_block.blocked_pairs == [(ENTITY_A, ENTITY_B)]

    history.append(
        decision(3, ResolutionAction.UNMERGE, [ENTITY_A, ENTITY_B], actor="operator:olena")
    )
    after_unmerge = project_groups(history)
    assert after_unmerge.groups == {}
    assert after_unmerge.group_of(ENTITY_A) is None

    # replay: повторний auto merge тієї самої пари пропускається через manual_block
    history.append(decision(4, ResolutionAction.MERGE, [ENTITY_A, ENTITY_B], group=GROUP_2))
    replayed = project_groups(history)
    assert replayed.groups == {}
    assert replayed.skipped_decision_ids == [UUID(int=4)]
    assert replayed.applied_decision_ids == [UUID(int=1), UUID(int=2), UUID(int=3)]

    # той самий список у будь-якому порядку → той самий snapshot (детермінований replay)
    assert project_groups(list(reversed(history))) == replayed
    assert project_groups(history) == replayed


def test_superseding_block_allows_new_merge_but_history_is_kept() -> None:
    history = [
        decision(1, ResolutionAction.MERGE, [ENTITY_A, ENTITY_B], group=GROUP_1),
        decision(2, ResolutionAction.MANUAL_BLOCK, [ENTITY_A, ENTITY_B], actor="operator:olena"),
        decision(3, ResolutionAction.UNMERGE, [ENTITY_A, ENTITY_B], actor="operator:olena"),
        decision(4, ResolutionAction.MERGE, [ENTITY_A, ENTITY_B], group=GROUP_2),
    ]
    assert project_groups(history).groups == {}
    # явне superseding рішення знімає блок: replay тієї ж історії дає групу
    history.append(
        decision(5, ResolutionAction.REJECT, [ENTITY_A], supersedes=2, actor="operator:olena")
    )
    snapshot = project_groups(history)
    assert snapshot.superseded_decision_ids == [UUID(int=2)]
    assert snapshot.blocked_pairs == []
    assert snapshot.groups == {GROUP_2: sorted([ENTITY_A, ENTITY_B], key=lambda u: u.int)}


def test_manual_link_not_subject_to_block_and_merge_pulls_whole_groups() -> None:
    history = [
        decision(1, ResolutionAction.MANUAL_BLOCK, [ENTITY_A, ENTITY_B], actor="operator:x"),
        decision(
            2, ResolutionAction.MANUAL_LINK, [ENTITY_A, ENTITY_B], group=GROUP_1, actor="operator:x"
        ),
        decision(3, ResolutionAction.MERGE, [ENTITY_C, ENTITY_D], group=GROUP_2),
        decision(4, ResolutionAction.MERGE, [ENTITY_B, ENTITY_C], group=GROUP_1),
    ]
    snapshot = project_groups(history)
    # merge B+C підтягує всю групу C (C, D) у GROUP_1 — блок A/B не стосується пари (B, C)...
    # ...але результівна група містить пару (A, B), яка заблокована → auto merge пропущено.
    assert snapshot.skipped_decision_ids == [UUID(int=4)]
    assert snapshot.groups == {
        GROUP_1: sorted([ENTITY_A, ENTITY_B], key=lambda u: u.int),
        GROUP_2: sorted([ENTITY_C, ENTITY_D], key=lambda u: u.int),
    }
    without_block = project_groups(history[1:])
    assert without_block.groups == {
        GROUP_1: sorted([ENTITY_A, ENTITY_B, ENTITY_C, ENTITY_D], key=lambda u: u.int)
    }


def test_unmerge_of_one_member_leaves_rest_grouped() -> None:
    history = [
        decision(1, ResolutionAction.MERGE, [ENTITY_A, ENTITY_B, ENTITY_C], group=GROUP_1),
        decision(2, ResolutionAction.UNMERGE, [ENTITY_C], actor="operator:x"),
    ]
    snapshot = project_groups(history)
    assert snapshot.groups == {GROUP_1: sorted([ENTITY_A, ENTITY_B], key=lambda u: u.int)}
    assert snapshot.group_of(ENTITY_C) is None


def test_decision_contract_validation() -> None:
    with pytest.raises(ValidationError, match="canonical_group_id"):
        decision(1, ResolutionAction.MERGE, [ENTITY_A, ENTITY_B])
    with pytest.raises(ValidationError, match="два"):
        decision(1, ResolutionAction.MERGE, [ENTITY_A], group=GROUP_1)
    with pytest.raises(ValidationError, match="score"):
        decision(1, ResolutionAction.MERGE, [ENTITY_A, ENTITY_B], group=GROUP_1).model_validate(
            {
                **decision(
                    1, ResolutionAction.MERGE, [ENTITY_A, ENTITY_B], group=GROUP_1
                ).model_dump(),
                "score": None,
            }
        )
    with pytest.raises(ValidationError, match="дублікати"):
        decision(1, ResolutionAction.MANUAL_BLOCK, [ENTITY_A, ENTITY_A])
    with pytest.raises(ValidationError, match="supersede"):
        decision(1, ResolutionAction.REJECT, [ENTITY_A], supersedes=1)
    with pytest.raises(ValidationError):
        decision(1, ResolutionAction.MERGE, [ENTITY_A, ENTITY_B], group=GROUP_1).model_validate(
            {
                **decision(
                    1, ResolutionAction.MERGE, [ENTITY_A, ENTITY_B], group=GROUP_1
                ).model_dump(),
                "score": Decimal("1.5"),
            }
        )
