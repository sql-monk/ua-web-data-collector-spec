"""Часова модель (§5.1, §9.6; R-43, R-49): source/effective і system/knowledge осі.

- `UtcDatetime` — aware UTC; naive datetime і будь-який ненульовий offset відхиляються
  (не нормалізуються, щоб не маскувати помилку джерела даних).
- `SourceTime` — час, заявлений джерелом; невідомий лишається `None` і ніколи не
  підміняється crawler time. `SystemTime` — `observed_at`, `fetched_at`, `ingested_at`.
- `EntityTime` — блок `time` current document (§9.2): обидві осі разом; validator-регресія
  R-43 відхиляє `source_event_at`/`source_updated_at`, що дорівнюють `fetched_at`.
- `EffectiveTime` + `derive_effective_time()` — аналітичний fallback з явним `basis`.
- `BitemporalInterval` + `build_intervals()` — `[valid_from, valid_to)` за `effective_at`
  і `[known_from, known_to)` за `ingested_at` для послідовності версій однієї сутності.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Annotated

from pydantic import AfterValidator, Field, model_validator

from collector.contracts._base import ContractModel
from collector.contracts.enums import EffectiveAtBasis, TimePrecision


def require_utc(value: datetime) -> datetime:
    """Validator: datetime має бути aware з нульовим offset."""
    offset = value.utcoffset()
    if offset is None:
        msg = "naive datetime заборонений: потрібен aware UTC (§5.1)"
        raise ValueError(msg)
    if offset != timedelta(0):
        msg = f"datetime має бути в UTC, отримано offset {offset} (§5.1)"
        raise ValueError(msg)
    return value


UtcDatetime = Annotated[datetime, AfterValidator(require_utc)]
"""Aware UTC datetime; naive або з offset ≠ 0 відхиляється."""


class SourceTime(ContractModel):
    """Час, заявлений джерелом (§5.1, §9.6); `None` ніколи не заповнюється crawler time."""

    source_event_at: UtcDatetime | None = None
    source_updated_at: UtcDatetime | None = None
    source_timezone_raw: str | None = None
    source_time_precision: TimePrecision = TimePrecision.UNKNOWN
    source_time_inferred: bool = False
    source_time_raw_text: str | None = None


class SystemTime(ContractModel):
    """System/knowledge time (§9.6): усі три обов'язкові, aware UTC."""

    observed_at: UtcDatetime = Field(description="Логічний час source snapshot.")
    fetched_at: UtcDatetime = Field(description="Завершення HTTP fetch.")
    ingested_at: UtcDatetime = Field(description="Commit normalized record.")

    @model_validator(mode="after")
    def _ordered(self) -> SystemTime:
        if self.ingested_at < self.fetched_at:
            msg = "ingested_at не може передувати fetched_at"
            raise ValueError(msg)
        return self


class EntityTime(ContractModel):
    """Блок `time` current document (§9.2): source і system осі в одному об'єкті.

    Регресія R-43: `source_event_at`/`source_updated_at`, що точно дорівнюють `fetched_at`,
    відхиляються — це ознака підміни source time crawler time (§9.6).
    """

    source_event_at: UtcDatetime | None = None
    source_updated_at: UtcDatetime | None = None
    observed_at: UtcDatetime
    fetched_at: UtcDatetime
    ingested_at: UtcDatetime
    source_timezone_raw: str | None = None
    source_time_precision: TimePrecision = TimePrecision.UNKNOWN
    source_time_inferred: bool = False

    @model_validator(mode="after")
    def _source_time_not_fetched_at(self) -> EntityTime:
        for name in ("source_event_at", "source_updated_at"):
            value: datetime | None = getattr(self, name)
            if value is not None and value == self.fetched_at:
                msg = f"{name} дорівнює fetched_at: source time не підміняється crawler time (§9.6)"
                raise ValueError(msg)
        if self.ingested_at < self.fetched_at:
            msg = "ingested_at не може передувати fetched_at"
            raise ValueError(msg)
        return self

    @classmethod
    def combine(cls, source: SourceTime, system: SystemTime) -> EntityTime:
        """Зібрати блок `time` з окремих осей."""
        return cls(
            source_event_at=source.source_event_at,
            source_updated_at=source.source_updated_at,
            observed_at=system.observed_at,
            fetched_at=system.fetched_at,
            ingested_at=system.ingested_at,
            source_timezone_raw=source.source_timezone_raw,
            source_time_precision=source.source_time_precision,
            source_time_inferred=source.source_time_inferred,
        )


class EffectiveTime(ContractModel):
    """Аналітичний fallback (§9.6): `effective_at` з явним `basis` і прапорцем inference."""

    effective_at: UtcDatetime
    effective_at_basis: EffectiveAtBasis
    source_time_inferred: bool

    @model_validator(mode="after")
    def _inferred_when_not_source_event(self) -> EffectiveTime:
        not_source_event = self.effective_at_basis != EffectiveAtBasis.SOURCE_EVENT
        if not_source_event and not self.source_time_inferred:
            msg = "source_time_inferred має бути True, якщо basis ≠ source_event (§9.6)"
            raise ValueError(msg)
        return self


def derive_effective_time(source: SourceTime, system: SystemTime) -> EffectiveTime:
    """`effective_at` = `source_event_at` → `source_updated_at` → `observed_at` (§9.6).

    `fetched_at` не використовується як basis (R-43): fallback — лише `observed_at`.
    `source_time_inferred=True`, якщо basis ≠ `source_event` або джерело саме позначило
    час як inferred.
    """
    if source.source_event_at is not None:
        return EffectiveTime(
            effective_at=source.source_event_at,
            effective_at_basis=EffectiveAtBasis.SOURCE_EVENT,
            source_time_inferred=source.source_time_inferred,
        )
    if source.source_updated_at is not None:
        return EffectiveTime(
            effective_at=source.source_updated_at,
            effective_at_basis=EffectiveAtBasis.SOURCE_UPDATED,
            source_time_inferred=True,
        )
    return EffectiveTime(
        effective_at=system.observed_at,
        effective_at_basis=EffectiveAtBasis.OBSERVED,
        source_time_inferred=True,
    )


class BitemporalInterval(ContractModel):
    """Напіввідкриті інтервали `[valid_from, valid_to)` і `[known_from, known_to)` (§9.6)."""

    valid_from: UtcDatetime
    valid_to: UtcDatetime | None = None
    known_from: UtcDatetime
    known_to: UtcDatetime | None = None

    @model_validator(mode="after")
    def _ordered(self) -> BitemporalInterval:
        if self.valid_to is not None and self.valid_to < self.valid_from:
            msg = "valid_to < valid_from"
            raise ValueError(msg)
        if self.known_to is not None and self.known_to <= self.known_from:
            msg = "known_to має бути пізніше known_from"
            raise ValueError(msg)
        return self

    def contains(self, *, as_of_valid_time: datetime, as_known_at: datetime) -> bool:
        """Чи версія чинна за обома осями у вказаний момент (§9.6 query contract)."""
        valid = self.valid_from <= as_of_valid_time and (
            self.valid_to is None or as_of_valid_time < self.valid_to
        )
        known = self.known_from <= as_known_at and (
            self.known_to is None or as_known_at < self.known_to
        )
        return valid and known


class VersionTimes(ContractModel):
    """Вхід `build_intervals`: одна версія сутності з `effective_at` та `ingested_at`."""

    projection_version: int = Field(ge=1)
    effective_at: UtcDatetime
    ingested_at: UtcDatetime


class VersionInterval(ContractModel):
    """Результат `build_intervals`: версія та її bitemporal-інтервал."""

    projection_version: int = Field(ge=1)
    interval: BitemporalInterval


def _next_strictly_later(
    versions: Sequence[VersionTimes], axis: Callable[[VersionTimes], datetime]
) -> dict[int, datetime | None]:
    """Для кожної версії — найближче строго більше значення осі серед інших версій."""
    ordered = sorted(versions, key=lambda v: (axis(v), v.projection_version))
    result: dict[int, datetime | None] = {}
    for index, version in enumerate(ordered):
        current = axis(version)
        result[version.projection_version] = next(
            (axis(n) for n in ordered[index + 1 :] if axis(n) > current), None
        )
    return result


def build_intervals(versions: Sequence[VersionTimes]) -> list[VersionInterval]:
    """Обчислює обидві осі незалежно для послідовності версій однієї сутності (§9.6).

    - `valid_to` = `effective_at` наступної версії зі строго більшим `effective_at`
      (версії з однаковим `effective_at` — backdated correction — ділять valid-інтервал);
    - `known_to` = `ingested_at` наступної версії за порядком `ingested_at`;
    - late arrival (менший `effective_at`, більший `ingested_at`) вставляється у valid-історію,
      не переписуючи known-вісь попередніх версій. Порядок вхідного списку не має значення;
      результат відсортовано за `projection_version`.
    """
    if len({v.projection_version for v in versions}) != len(versions):
        msg = "projection_version мають бути унікальними"
        raise ValueError(msg)
    valid_to = _next_strictly_later(versions, lambda v: v.effective_at)
    known_to = _next_strictly_later(versions, lambda v: v.ingested_at)

    return [
        VersionInterval(
            projection_version=version.projection_version,
            interval=BitemporalInterval(
                valid_from=version.effective_at,
                valid_to=valid_to[version.projection_version],
                known_from=version.ingested_at,
                known_to=known_to[version.projection_version],
            ),
        )
        for version in sorted(versions, key=lambda v: v.projection_version)
    ]
