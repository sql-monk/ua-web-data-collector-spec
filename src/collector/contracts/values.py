"""Гроші, контакти та виміряні значення (§5.1, §9.3 п.8, §12.2).

- `Money` — `amount_minor` (int) + ISO-4217 `currency`; float заборонений.
- `ContactValue` — `raw` ніколи не втрачається; `normalized`: телефон → E.164 (`phonenumbers`,
  default region UA), e-mail → lowercase local part + IDNA (punycode) домен.
- `MeasuredValue` — SI-значення разом із `raw_value/raw_unit` (§12.2).
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal
from typing import Annotated

from pydantic import Field, StringConstraints, model_validator

from collector.contracts._base import ContractModel
from collector.contracts.enums import ContactKind

CURRENCY_PATTERN = r"^[A-Z]{3}$"
E164_PATTERN = r"^\+[1-9][0-9]{1,14}$"
DEFAULT_PHONE_REGION = "UA"

CurrencyCode = Annotated[str, StringConstraints(pattern=CURRENCY_PATTERN)]
"""Формат ISO-4217 (три великі літери); повний список кодів не перевіряється."""


class Money(ContractModel):
    """Грошове значення без float: `amount_minor` у мінімальних одиницях + `currency` (§5.1)."""

    amount_minor: int = Field(
        strict=True,
        description="Сума в мінімальних одиницях валюти (копійки, центи); лише int (§5.1).",
    )
    currency: CurrencyCode


class ContactValue(ContractModel):
    """Контактне значення: тип, вихідний рядок і нормалізована форма (§9.3 п.8)."""

    kind: ContactKind
    raw: Annotated[str, StringConstraints(min_length=1)]
    normalized: str | None = None

    @model_validator(mode="after")
    def _normalized_shape(self) -> ContactValue:
        if self.normalized is None:
            return self
        if self.kind is ContactKind.PHONE and not _matches_e164(self.normalized):
            msg = f"normalized телефон має бути E.164, отримано {self.normalized!r}"
            raise ValueError(msg)
        if self.kind is ContactKind.EMAIL and self.normalized != self.normalized.lower():
            msg = "normalized e-mail має бути lowercase"
            raise ValueError(msg)
        return self

    @classmethod
    def from_raw(
        cls, kind: ContactKind, raw: str, *, default_region: str = DEFAULT_PHONE_REGION
    ) -> ContactValue:
        """Створити значення з нормалізацією; невдала нормалізація → `normalized=None`."""
        return cls(kind=kind, raw=raw, normalized=normalize_contact(kind, raw, default_region))


def _matches_e164(value: str) -> bool:
    # ASCII-only: `str.isdigit()` приймає не-ASCII цифри, E.164 — лише [0-9] (CR-07).
    return re.fullmatch(E164_PATTERN, value) is not None


def normalize_phone(raw: str, default_region: str = DEFAULT_PHONE_REGION) -> str | None:
    """Телефон → E.164 через `phonenumbers`; невалідний номер → `None`."""
    import phonenumbers

    try:
        parsed = phonenumbers.parse(raw, default_region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def normalize_email(raw: str) -> str | None:
    """E-mail → NFC, lowercase local part + IDNA-домен (punycode); невалідний → `None`."""
    import idna

    text = unicodedata.normalize("NFC", raw).strip()
    local, sep, domain = text.rpartition("@")
    if not sep or not local or not domain or any(ch.isspace() for ch in text):
        return None
    try:
        domain_ascii = idna.encode(domain.lower(), uts46=True).decode("ascii")
    except idna.IDNAError:
        return None
    return f"{local.lower()}@{domain_ascii}"


def normalize_contact(
    kind: ContactKind, raw: str, default_region: str = DEFAULT_PHONE_REGION
) -> str | None:
    """Нормалізація за типом; для `url/messenger/other` — лише NFC + strip."""
    if kind is ContactKind.PHONE:
        return normalize_phone(raw, default_region)
    if kind is ContactKind.EMAIL:
        return normalize_email(raw)
    text = unicodedata.normalize("NFC", raw).strip()
    return text or None


class MeasuredValue(ContractModel):
    """Значення в SI-одиниці разом із вихідними `raw_value/raw_unit` (§12.2)."""

    value: Decimal
    unit: Annotated[str, StringConstraints(min_length=1)]
    raw_value: Annotated[str, StringConstraints(min_length=1)]
    raw_unit: str | None = None
